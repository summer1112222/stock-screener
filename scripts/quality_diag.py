"""quality 筛选逻辑一次性诊断工具（读库不写库，可本地/容器跑）。

目标：量化 quality 到底"准不准"，定位拖累口径/权重，供后续校准依据。

两大块：
1. Panel 诊断（无前视、长历史）：把 quality 口径1（风险调整）的构成因子
   （momentum/volatility/sortino/amount_accel——全 OHLCV 可历史重建）在
   stock_daily 全历史上做滚动横截面分档单调性 + Rank IC。复用 backtest.eval
   的 forward_returns / ic_series / decile_backtest，不改生产逻辑。
2. 当前 main 清单审计：调一次 quality_rank(refine=False, 不触盘口) 拿真实
   main，审计 confidence_level / risk_flags / 口径覆盖分布，作口径2/3/5
   （无历史可重建）的快照补看。

局限（如实标注）：口径2/3/5 只能用当前快照补看，无法严格历史回测；入选池
按当前 spot 划。脚本只读库，不产生副作用。
"""
from __future__ import annotations

import argparse
import os
import sys
from collections import Counter

import numpy as np
import pandas as pd


def amount_accel_panel(amount: pd.DataFrame, win: int = 5, base: int = 60) -> pd.DataFrame:
    """滚动成交额加速：近 win 日均量 / 更长 base 基准 - 1（对齐 quality
    _risk_factor_series 的 amount_accel 语义，但做成滚动面板）。

    rolling 只用 <=t 数据，天然前视安全。win<base 时初期 base 未铺满→NaN。
    """
    a_win = amount.rolling(win, min_periods=max(2, win // 2)).mean()
    a_base = amount.rolling(base, min_periods=max(2, base // 2)).mean()
    return a_win / a_base.replace(0, np.nan) - 1.0


def sortino_panel(close: pd.DataFrame, days: int = 20) -> pd.DataFrame:
    """滚动 Sortino：近 days 日日收益均值 / 下行波动（下行 std），对齐 quality
    _risk_factor_series 的 sortino（用下行 std，非总 std）。前视安全。"""
    ret = close.pct_change()
    mu = ret.rolling(days, min_periods=max(2, days // 2)).mean()
    down_std = (ret.where(ret < 0).rolling(days, min_periods=max(2, days // 2)).std())
    return mu / down_std.replace(0, np.nan)


def _risk1_factors(close: pd.DataFrame, amount: pd.DataFrame | None) -> dict:
    """口径1 构成因子面板（滚动版，大=好）。全 OHLCV 派生，可历史重建。"""
    from backtest import eval as bt_eval
    factors = {
        "momentum_20": bt_eval.compute_factor(close, "momentum_20"),
        "momentum_60": bt_eval.compute_factor(close, "momentum_60"),
        # 低波动好 → 取负
        "volatility_20": -bt_eval.compute_factor(close, "volatility_20"),
        "sortino_20": sortino_panel(close, days=20),
    }
    if amount is not None and not amount.empty:
        factors["amount_accel"] = amount_accel_panel(amount, win=5, base=60)
    return factors


def panel_diag(close: pd.DataFrame, amount: pd.DataFrame | None,
               ks=(5, 20)) -> dict:
    """对口径1 构成因子跑滚动分档 + IC。返回 {因子: {decile, ic, by_k}}。

    decile/ic 用主 k=ks[-1]；by_k 存全部 k 的 ic_summary 与分档多空末值。
    """
    from backtest import eval as bt_eval
    factors = _risk1_factors(close, amount)
    out: dict = {}
    for fname, fac in factors.items():
        entry: dict = {"by_k": {}}
        for k in ks:
            fwd = bt_eval.forward_returns(close, k)
            ic = bt_eval.ic_summary(bt_eval.ic_series(fac, fwd))
            dbt = bt_eval.decile_backtest(fac, fwd, 5)
            entry["by_k"][k] = {"ic": ic, "decile": dbt}
        main_k = ks[-1]
        entry["decile"] = entry["by_k"][main_k]["decile"]
        entry["ic"] = entry["by_k"][main_k]["ic"]
        out[fname] = entry
    return out


def audit_main(main: list[dict]) -> dict:
    """聚合 main 清单：confidence_level / risk_flags / 口径覆盖 计数。"""
    conf: Counter = Counter()
    risk: Counter = Counter()
    dim_cov: Counter = Counter()
    scores = []
    for it in main:
        conf[str(it.get("confidence_level") or "unknown")] += 1
        for r in (it.get("risk_flags") or []):
            risk[str(r)] += 1
        for d, v in (it.get("dim_scores") or {}).items():
            if v is not None:
                dim_cov[str(d)] += 1
        if it.get("adjusted_resonance") is not None:
            scores.append(float(it["adjusted_resonance"]))
    return {
        "n": len(main),
        "confidence": dict(conf),
        "risk_flags": dict(risk),
        "dim_coverage": dict(dim_cov),
        "adj_resonance": {"mean": float(np.mean(scores)) if scores else None,
                          "min": float(np.min(scores)) if scores else None,
                          "max": float(np.max(scores)) if scores else None},
    }


def _load_daily_panels(db_path: str, universe: str, days: int = 2000):
    """用 sqlite3 只读连接直接 pivot 真实库的 close/amount 面板（零 db 注入）。

    取最近 days 自然日；返回 (close, amount, codes)。panel_diag 是纯函数，
    不依赖 SCREENER_DB（仅 quality_rank 的 main 审计需要它）。
    """
    import sqlite3
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        df = pd.read_sql_query(
            "SELECT symbol, date, close, amount FROM stock_daily", conn)
    finally:
        conn.close()
    if df is None or df.empty:
        return None, None, []
    df["date"] = pd.to_datetime(df["date"])
    codes = sorted(df["symbol"].astype(str).unique())
    if days and days > 0:
        cutoff = df["date"].max() - pd.Timedelta(days=days)
        df = df[df["date"] >= cutoff]
    close = (df.pivot_table(index="date", columns="symbol", values="close",
                            aggfunc="last").sort_index())
    amount = (df.pivot_table(index="date", columns="symbol", values="amount",
                             aggfunc="last").sort_index())
    return close, amount, codes


def render_report(panel: dict, audit: dict | None) -> str:
    """把诊断结果渲染成 markdown 报告。"""
    lines = ["# quality 筛选逻辑诊断报告", ""]
    lines.append("## 一、口径1(风险调整) 构成因子 · 滚动分档 + Rank IC")
    lines.append("")
    lines.append("| 因子 | k | IC | IC胜率 | Q5/Q1多空 | 单调性 |")
    lines.append("|---|---|---|---|---|---|")
    for fname, fout in panel.items():
        for k, byk in fout["by_k"].items():
            ic = byk["ic"]
            ls = byk["decile"].get("long_short") or {}
            ls_val = list(ls.values())[-1] if ls else None
            ic_txt = f"{ic['ic']:+.3f}" if ic["ic"] is not None else "—"
            wr_txt = f"{ic['win_rate']:.0%}" if ic["win_rate"] is not None else "—"
            ls_txt = f"{ls_val:.3f}" if ls_val is not None else "—"
            # 单调性: 各档累计净值的简单序列相关性(5档应近似单调)
            lines.append(f"| {fname} | {k} | {ic_txt} | {wr_txt} | {ls_txt} | — |")
    lines.append("")
    if audit:
        lines.append("## 二、当前 main 清单审计（快照补看，口径2/3/5 无历史）")
        lines.append("")
        lines.append(f"- 样本数: {audit['n']}")
        lines.append(f"- confidence_level 分布: {audit['confidence']}")
        lines.append(f"- risk_flags 分布: {audit['risk_flags']}")
        lines.append(f"- 口径覆盖: {audit['dim_coverage']}")
        lines.append(f"- adjusted_resonance: {audit['adj_resonance']}")
        lines.append("")
    lines.append("## 局限")
    lines.append("- 口径1 构成因子可长历史无前视回测；口径2/3/5 仅当前快照补看，无法严格历史重建。")
    lines.append("- 入选池按当前 stock_spot 划分；诊断反映因子本身区分度，非全流程点对点回测。")
    lines.append("- 本报告为机械统计诊断，非荐股非买卖信号，盈亏自负。")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="quality 筛选逻辑一次性诊断")
    ap.add_argument("--db", default="/app/var/stock.db", help="真实库路径")
    ap.add_argument("--out", default="", help="报告输出路径(缺省打印 stdout)")
    ap.add_argument("--days", type=int, default=2000, help="panel 最近自然日窗口")
    ap.add_argument("--ks", default="5,20", help="前视窗口 k(逗号分隔)")
    args = ap.parse_args(argv)
    ks = tuple(int(x) for x in args.ks.split(",") if x.strip())

    # 让 quality_rank 的 main 审计读到真实库（db.DB_PATH import 时读 env）。
    os.environ["SCREENER_DB"] = args.db

    close, amount, codes = _load_daily_panels(args.db, "stock", days=args.days)
    if close is None or close.empty:
        print(f"[quality_diag] 无 stock_daily 历史（codes={len(codes)}），先 /api/backtest/fetch", file=sys.stderr)
        return 1
    panel = panel_diag(close, amount, ks=ks)
    print(f"[quality_diag] 面板诊断完成: {len(codes)} 票 × {len(close)} 日", file=sys.stderr)

    audit = None
    try:
        from backtest import quality
        if hasattr(quality, "quality_rank"):
            res = quality.quality_rank(
                universe="stock", days=20, min_dims=2, min_turnover=5e7,
                max_per_board=3, max_corr=0.85, limit=20, combo_method="greedy",
                resonance_mode="greedy", dim_thresh=0.7, refine=False,
                refine_pool=0, strict_quality=True, min_confidence=0.50,
                risk_penalty=True)
            main_list = res.get("main") or []
            audit = audit_main(main_list)
            print(f"[quality_diag] main 审计完成: {len(main_list)} 条", file=sys.stderr)
    except Exception as e:
        print(f"[quality_diag] main 审计跳过: {e}", file=sys.stderr)

    report = render_report(panel, audit)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(report)
        print(f"[quality_diag] 报告写入 {args.out}", file=sys.stderr)
    else:
        print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
