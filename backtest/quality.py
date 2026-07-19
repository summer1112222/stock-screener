# -*- coding: utf-8 -*-
"""优质选股筛选编排层：四口径分位 + 共振层 + 组合层。

合规：多口径共振机械排序观察清单，非荐股非买卖信号，不承诺收益。
      resonance 是"多口径同靠前"事实陈述，非收益预测/推荐强度。
      权重默认等权，不预设风格。
不新增表/采集源：复用 stock_spot/etf_spot/*_daily/smart_money_action，
      因子源(buffett/signals/smart_money/candidates)只读结果，不改。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from data import db

_SPOT_TABLE = {"stock": "stock_spot", "etf": "etf_spot"}
_CAND_DISCLAIMER = ("多口径共振机械排序观察清单，非荐股非买卖信号，"
                    "不构成投资建议、不承诺收益。市场有风险，盈亏自负。")

ETF_BENCHMARK_MAP = {
    "510300": "sh000300", "510310": "sh000300", "510160": "sh000300",
    "510050": "sh000016", "510500": "sh000905", "588000": "sh000688",
    "512100": "sh000852", "159915": "sz399006",
}


def _to_float(v):
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    try:
        if pd.isna(f):
            return None
    except (TypeError, ValueError):
        pass
    return f


def _zscore(s: pd.Series) -> pd.Series:
    s = pd.to_numeric(s, errors="coerce")
    std = s.std(ddof=0)
    if std is None or pd.isna(std) or std == 0:
        return pd.Series(0.0, index=s.index)
    return (s - s.mean()) / std


def _to_pct(s: pd.Series) -> pd.Series:
    """横截分位 0-1（越大越好）。"""
    return s.rank(pct=True, method="average")


def _tradable(df: pd.DataFrame, min_turnover: float, limit_pct: float) -> pd.DataFrame:
    """可交易性预筛：排除 ST/停牌/涨停/低成交额。复用 candidates 风格。"""
    if df is None or df.empty:
        return df
    mask = pd.Series(True, index=df.index)
    if "name" in df.columns:
        mask &= ~df["name"].astype(str).str.contains("ST", case=False, na=False)
    if "latest_price" in df.columns:
        lp = pd.to_numeric(df["latest_price"], errors="coerce")
        mask &= lp.notna() & (lp > 0)
    if "turnover_amount" in df.columns:
        mask &= pd.to_numeric(df["turnover_amount"], errors="coerce").fillna(0) >= min_turnover
    if "change_pct" in df.columns:
        mask &= pd.to_numeric(df["change_pct"], errors="coerce").fillna(-99) < limit_pct
    return df[mask]


def _dim_scores(df, universe, days, min_signals):
    """四口径分位。返回 (code->{dim:pct}, dims_available, dim_status)。
    任一因子源失败→该口径 err、分位 None、不崩。"""
    codes = df["code"].astype(str).tolist() if "code" in df.columns else []
    scores = {c: {} for c in codes}
    dims_avail, status = [], {}

    # 口径1 风险调整(历史)：波动率(负)/动量/夏普/最大回撤(负)
    try:
        import backtest.eval as bt_eval
        close = bt_eval.load_panel(universe, codes, "1990-01-01", "2099-12-31", "close")
        if close is None or close.empty:
            status["1"] = "err:无历史数据，先 /api/backtest/fetch"
        else:
            ret = close.pct_change().dropna(how="all")
            vol = ret.std().clip(lower=1e-9)
            mom = close.pct_change(days).iloc[-1]
            sharpe = ret.mean() / vol
            dd = (close / close.cummax() - 1).min()
            comp = (_zscore(-vol) + _zscore(mom) + _zscore(sharpe) + _zscore(-dd)) / 4
            pct = _to_pct(comp)
            for c in codes:
                scores[c][1] = _to_float(pct.get(c)) if c in pct.index else None
            dims_avail.append(1)
            status["1"] = "ok"
    except Exception as e:
        status["1"] = f"err:{e}"

    # 口径2 价值质量(仅个股,buffett 按需)；红旗硬预筛
    if universe == "stock":
        try:
            import backtest.buffett as bt_buf
            if not getattr(bt_buf, "_AK_OK", False):
                status["2"] = "err:_AK_OK=False"
            else:
                # 性能：不全市场逐股拉 buffett，先 shortlist 缩到 ≤80 只
                # （spec §7.5）。非 shortlist 标的口径2 分位=None。
                try:
                    sl = set(bt_buf.shortlist_by_turnover(min_turnover=5e8, k=80))
                    sl_codes = [c for c in codes if c in sl]
                except Exception:
                    sl_codes = codes
                results = bt_buf.analyze_many(sl_codes)

                def _bad(r):
                    rt = r.get("ratios", {})
                    if rt.get("goodwill_to_equity_pct") and rt["goodwill_to_equity_pct"] > 30:
                        return True
                    if rt.get("debt_ratio_latest") is not None and rt["debt_ratio_latest"] > 75:
                        return True
                    if rt.get("fcf_to_netincome") is not None and rt["fcf_to_netincome"] < 0.3:
                        return True
                    return False

                results = [r for r in results if not _bad(r)]
                ey = pd.Series({r["code"]: r.get("earnings_yield_pct") for r in results}).dropna()
                moat = pd.Series({r["code"]: r.get("moat_score") for r in results}).dropna()
                lroe = pd.Series({r["code"]: (r.get("ratios") or {}).get("leverage_adj_roe")
                                  for r in results}).dropna()
                comp = (_zscore(ey) + _zscore(moat) + _zscore(lroe)) / 3
                pct = _to_pct(comp)
                for c in codes:
                    scores[c][2] = _to_float(pct.get(c)) if c in pct.index else None
                dims_avail.append(2)
                status["2"] = "ok"
        except Exception as e:
            status["2"] = f"err:{e}"
    else:
        # ETF 口径2：跟踪误差 + 成交额稳定性
        try:
            import backtest.eval as bt_eval
            from data.history import fetch_benchmark_hist
            close = bt_eval.load_panel("ETF", codes, "1990-01-01", "2099-12-31", "close")
            amount = bt_eval.load_panel("ETF", codes, "1990-01-01", "2099-12-31", "amount")
            if close is None or close.empty:
                status["2"] = "err:无etf历史，先 /api/backtest/fetch"
            else:
                ret = close.pct_change()
                te, av = {}, {}
                for c in codes:
                    bm = ETF_BENCHMARK_MAP.get(c)
                    te_c = None
                    if bm:
                        try:
                            bdf, ok, _ = fetch_benchmark_hist(bm, "19900101", "20991231")
                            if ok and not bdf.empty and "close" in bdf.columns:
                                bs = bdf.set_index("date")["close"].astype(float).sort_index()
                                bs.index = pd.to_datetime(bs.index)
                                bs = bs.reindex(close.index).ffill()
                                diff = (ret[c] - bs.pct_change()).dropna()
                                if len(diff) >= days:
                                    te_c = float(diff.tail(days).std())
                        except Exception:
                            pass
                    te[c] = te_c
                    if amount is not None and c in amount.columns:
                        a = amount[c].dropna()
                        if len(a) >= days and a.tail(days).mean() > 0:
                            av[c] = float(a.tail(days).std() / a.tail(days).mean())
                        else:
                            av[c] = None
                    else:
                        av[c] = None
                te_s = pd.Series(te).dropna()
                av_s = pd.Series(av).dropna()
                comp = pd.Series(0.0, index=codes)
                n_fac = 0
                if len(te_s):
                    comp = comp.add(_zscore(-te_s).reindex(codes).fillna(0.0))
                    n_fac += 1
                if len(av_s):
                    comp = comp.add(_zscore(-av_s).reindex(codes).fillna(0.0))
                    n_fac += 1
                comp = comp / n_fac if n_fac else comp
                pct = _to_pct(comp)
                for c in codes:
                    scores[c][2] = _to_float(pct.get(c)) if c in pct.index else None
                dims_avail.append(2)
                status["2"] = "ok(ETF:跟踪误差+成交额稳定)"
        except Exception as e:
            status["2"] = f"err:{e}"

    # 口径3 资金流向(smart_money 累计 + spot 当日 + 换手)
    try:
        import screener.smart_money as sm_q
        sm = sm_q.top_by_amount(days=days, market=None, channel=None, limit=10000)
        sm_amt = {r.get("code"): r.get("amount") for r in sm.get("rows", [])}
        spot_amt = pd.to_numeric(df.get("main_net_inflow"), errors="coerce").values \
            if "main_net_inflow" in df.columns else None
        spot_tr = pd.to_numeric(df.get("turnover_rate"), errors="coerce").values \
            if "turnover_rate" in df.columns else None
        sm_s = pd.Series([sm_amt.get(c) for c in codes], index=codes, dtype=float)
        sa = pd.Series(spot_amt, index=codes, dtype=float) if spot_amt is not None \
            else pd.Series(dtype=float, index=codes)
        st = pd.Series(spot_tr, index=codes, dtype=float) if spot_tr is not None \
            else pd.Series(dtype=float, index=codes)
        comp = (_zscore(sm_s) + _zscore(sa) + _zscore(st)) / 3
        pct = _to_pct(comp)
        for c in codes:
            scores[c][3] = _to_float(pct.get(c)) if c in pct.index else None
        dims_avail.append(3)
        status["3"] = "ok(仅spot)" if not sm.get("rows") else "ok"
    except Exception as e:
        status["3"] = f"err:{e}"

    # 口径4 多信号(历史)：胜率加权（excess_win_rate 均值→横截 pct），降级 trig/5
    try:
        import backtest.signals as bt_sig
        scan = bt_sig.scan_signals(universe, codes)
        if scan.get("error"):
            status["4"] = f"err:{scan['error']}"
        else:
            trig = {r["code"]: len(r["signals"]) for r in scan.get("rows", [])}
            bt = bt_sig.backtest_signals(universe, codes, k_days=5)
            win_by_code = {}
            if not bt.get("error"):
                sig_rows = {r["signal"]: r for r in bt.get("rows", [])}
                for r in scan.get("rows", []):
                    c = r["code"]
                    # 用 signal_keys(type key)匹配 sig_rows，而非显示串
                    ewrs = [sig_rows[k]["excess_win_rate"] for k in r.get("signal_keys", [])
                            if k in sig_rows and sig_rows[k].get("excess_win_rate") is not None]
                    win_by_code[c] = float(np.mean(ewrs)) if ewrs else None
            s = pd.Series(index=codes, dtype=float)
            for c in codes:
                v = win_by_code.get(c)
                s[c] = v if v is not None else (trig.get(c, 0) / 5.0)
            pct = _to_pct(s)
            for c in codes:
                scores[c][4] = _to_float(pct.get(c)) if c in pct.index else None
            dims_avail.append(4)
            status["4"] = ("ok(胜率加权)" if any(v is not None for v in win_by_code.values())
                           else "ok(降级trig/5)")
    except Exception as e:
        status["4"] = f"err:{e}"

    return scores, dims_avail, status


def _resonance(dim_scores, dim_thresh, weights=None):
    """resonance = hits×10 + 命中口径加权平均分位。返回 (resonance, hits)。
    dim_scores: {dim: pct}，pct 可能 None（None 不算命中不计分母）。"""
    w = weights or {}
    hits, wsum, wtot = 0, 0.0, 0.0
    for d, pct in dim_scores.items():
        if pct is None:
            continue
        if pct >= dim_thresh:
            hits += 1
            wi = w.get(d, 1.0)
            wsum += pct * wi
            wtot += wi
    avg = (wsum / wtot) if wtot > 0 else 0.0
    return hits * 10 + avg, hits


def _board_of(code, universe, df_spot):
    """best-effort 取行业/板块：spot 的 board 列 → industry_board 成分兜底。"""
    if "board" in df_spot.columns:
        row = df_spot[df_spot["code"].astype(str) == str(code)]
        if not row.empty and pd.notna(row["board"].iloc[0]):
            return str(row["board"].iloc[0])
    if universe == "stock":
        try:
            for b in db.query_rows("industry_board"):
                members = b.get("members") or b.get("stocks") or []
                if str(code) in [str(m) for m in members]:
                    return b.get("name", "未知")
        except Exception:
            pass
    return "未知"


def _corr_matrix(universe, codes):
    """候选集收益率相关矩阵。无历史返回 None。"""
    try:
        import backtest.eval as bt_eval
        close = bt_eval.load_panel(universe, codes, "1990-01-01", "2099-12-31", "close")
        if close is None or close.empty:
            return None
        return close.pct_change().corr()
    except Exception:
        return None


def _min_var_weights(codes: list[str], universe: str) -> list[float]:
    """最小方差权重解析解 w = Σ⁻¹·1 / (1ᵀ·Σ⁻¹·1)。Σ 奇异降级 1/方差。long-only 归零归一。

    合规：风险预算机械分配，非推荐仓位。
    额外防护：numpy 对奇异矩阵可能不抛 LinAlgError 而返回含 inf/极大值，
    此时一并降级 1/方差。
    """
    try:
        import backtest.eval as bt_eval
        close = bt_eval.load_panel(universe, codes, "1990-01-01", "2099-12-31", "close")
        if close is None or close.empty:
            return [1.0 / len(codes)] * len(codes)
        # load_panel 用 pivot_table(sort=True)→列按字典序非输入 codes 顺序。
        # cov/inv 都在字典序列列上算，返回前必须把列对齐回 codes 顺序，
        # 否则下游 _apply_combo 的 zip(kept, ws) 会把权重赋给错误 code。
        close = close.reindex(columns=codes)
        close = close.dropna(axis=1, how="all")
        if close.shape[1] != len(codes):
            # 某些 code 无任何历史数据（不应发生，codes 来自 kept 有数据）→等权兜底。
            return [1.0 / len(codes)] * len(codes)
        ret = close.pct_change().dropna(how="all").fillna(0.0)
        cov = ret.cov().values
        if cov.shape[0] != len(codes):
            return [1.0 / len(codes)] * len(codes)
        try:
            inv = np.linalg.inv(cov)
        except np.linalg.LinAlgError:
            var = np.diag(cov)
            inv_var = np.where(var > 0, 1.0 / var, 0.0)
            w = inv_var / inv_var.sum() if inv_var.sum() > 0 else np.ones(len(codes)) / len(codes)
            return w.tolist()
        # 奇异矩阵可能不抛异常而返回 inf/极大值，需额外防护
        if not np.all(np.isfinite(inv)):
            var = np.diag(cov)
            inv_var = np.where(var > 0, 1.0 / var, 0.0)
            s = inv_var.sum()
            w = inv_var / s if s > 0 else np.ones(len(codes)) / len(codes)
            return w.tolist()
        ones = np.ones(len(codes))
        denom = ones @ inv @ ones
        if denom == 0 or not np.isfinite(denom):
            return [1.0 / len(codes)] * len(codes)
        w = (inv @ ones) / denom
        w = np.where(w < 0, 0.0, w)
        s = w.sum()
        if s <= 0:
            return [1.0 / len(codes)] * len(codes)
        w = w / s
        return w.tolist()
    except Exception:
        return [1.0 / len(codes)] * len(codes)


def _apply_combo(main, universe, df_spot, max_per_board, max_corr, limit,
                 combo_method="greedy"):
    """贪心：按 resonance 降序，行业≤max_per_board + 相关性≤max_corr。
    combo_method: "greedy" 等权（默认）；"min_var" 最小方差权重（风险预算机械分配，非推荐仓位）。"""
    corr = _corr_matrix(universe, [r["code"] for r in main]) if max_corr > 0 else None
    kept, board_cnt = [], {}
    for it in main:
        b = _board_of(it["code"], universe, df_spot)
        if board_cnt.get(b, 0) >= max_per_board:
            continue
        if corr is not None and kept:
            mx = 0.0
            for k in kept:
                try:
                    v = corr.loc[it["code"], k["code"]]
                    if pd.notna(v) and abs(v) > mx:
                        mx = abs(v)
                except (KeyError, IndexError):
                    pass
            if mx > max_corr:
                continue
        it["constraints"] = {"board": b, "board_count_in_pool": board_cnt.get(b, 0) + 1}
        kept.append(it)
        board_cnt[b] = board_cnt.get(b, 0) + 1
        if len(kept) >= limit:
            break
    if combo_method == "min_var" and len(kept) >= 2:
        ws = _min_var_weights([it["code"] for it in kept], universe)
    else:
        ws = [1.0 / len(kept)] * len(kept) if kept else []
    for it, w in zip(kept, ws):
        it["weight"] = round(float(w), 4)
    return kept


def _build_reasons(item):
    """从已有 dim_scores/hits 机械拼入选理由（叙事化，不引入新判断）。"""
    ds = item.get("dim_scores", {})
    names = {1: "风险调整", 2: "价值质量", 3: "资金流向", 4: "多信号"}
    parts = [f"{names[d]}(分位{round(p, 2)})"
             for d, p in sorted(ds.items()) if p is not None]
    base = "命中 " + " + ".join(parts) if parts else "无口径命中"
    return [f"{base}，共振{item.get('hits', 0)}档"]


def quality_rank(universe="stock", days=20, weights=None, min_dims=2,
                 dim_thresh=0.6, min_turnover=5e7, max_per_board=3,
                 max_corr=0.85, limit=20, min_signals=2, limit_pct=9.9,
                 combo_method: str = "greedy") -> dict:
    """优质筛选主入口。返回 {main, by_dim, dims_available, dim_status,
    min_dims, cand_disclaimer, error}。口径分位见 _dim_scores；
    共振/组合见 _resonance/_apply_combo。combo_method: "greedy" 等权（默认），
    "min_var" 最小方差权重（风险预算机械分配，非推荐仓位）。"""
    table = _SPOT_TABLE.get(universe)
    if not table:
        return {"main": [], "by_dim": {}, "dims_available": [], "dim_status": {},
                "min_dims": min_dims, "cand_disclaimer": _CAND_DISCLAIMER,
                "error": f"不支持的 universe={universe}"}
    rows = db.query_rows(table)
    if not rows:
        return {"main": [], "by_dim": {}, "dims_available": [], "dim_status": {},
                "min_dims": min_dims, "cand_disclaimer": _CAND_DISCLAIMER,
                "error": f"{table} 为空，先 /api/refresh"}
    df = _tradable(pd.DataFrame(rows), min_turnover, limit_pct)
    if df.empty:
        return {"main": [], "by_dim": {}, "dims_available": [], "dim_status": {},
                "min_dims": min_dims, "cand_disclaimer": _CAND_DISCLAIMER,
                "error": "tradable 预筛后为空"}
    codes = df["code"].astype(str).tolist() if "code" in df.columns else []
    scores, dims_avail, dim_status = _dim_scores(df, universe, days, min_signals)
    eff_min_dims = min(min_dims, len(dims_avail)) if dims_avail else 0
    enriched, by_dim = [], {d: [] for d in (1, 2, 3, 4)}
    for c in codes:
        ds = scores.get(c, {})
        res, hits = _resonance(ds, dim_thresh, weights)
        name = df.loc[df["code"].astype(str) == c, "name"].iloc[0] \
            if "name" in df.columns else c
        item = {"code": c, "name": name, "resonance": _to_float(res),
                "hits": hits, "dim_scores": ds, "reasons": []}
        enriched.append(item)
        for d in (1, 2, 3, 4):
            if ds.get(d) is not None:
                by_dim[d].append({**item, "_pct": ds[d]})
    for d in by_dim:
        by_dim[d].sort(key=lambda x: x.get("_pct") or 0, reverse=True)
        by_dim[d] = [{k: v for k, v in x.items() if k != "_pct"} for x in by_dim[d]]
    main = [it for it in enriched if it["hits"] >= eff_min_dims]
    main.sort(key=lambda x: x["resonance"] or 0, reverse=True)
    main = _apply_combo(main, universe, df, max_per_board, max_corr, limit,
                        combo_method=combo_method)

    def _clean_item(it):
        it["reasons"] = _build_reasons(it)
        it["dim_scores"] = {d: _to_float(v) for d, v in it.get("dim_scores", {}).items()}
        it["resonance"] = _to_float(it.get("resonance"))
        return it

    main = [_clean_item(it) for it in main]
    by_dim = {d: [_clean_item(it) for it in lst] for d, lst in by_dim.items()}
    return {"main": main, "by_dim": by_dim, "dims_available": dims_avail,
            "dim_status": dim_status, "min_dims": eff_min_dims,
            "cand_disclaimer": _CAND_DISCLAIMER, "error": None}
