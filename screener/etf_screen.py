# screener/etf_screen.py
from __future__ import annotations
import math
import time
from datetime import datetime, timedelta
import os

import pandas as pd

from . import nextday as _nd
from backtest import eval as bt_eval
from data import db as _db
from data.history import _UNIVERSE

# akshare 仅容器采集层依赖；装入失败 → 真实 _fetch_* 诚实返 None(降级)。
try:  # pragma: no cover
    import akshare as _ak_mod
    _AK_OK = True
except Exception:  # pragma: no cover
    _ak_mod = None  # type: ignore
    _AK_OK = False

# 东财全市场 ETF 快照按需整表缓存(一次调用取全市场, 分摊到多股; 300s TTL)
_ETF_SPOT_EM_CACHE: dict = {"ts": 0.0, "df": None}

_CACHE: dict[tuple, tuple] = {}
_CACHE_TTL = 30

# long 清单规模硬门槛(亿; _fetch_quality_meta 的 fund_scale 若可得且 < 此值跳过)
_MIN_SCALE = 10.0

def _clip(v, lo=0.0, hi=1.0):
    return max(lo, min(hi, v))

def valuation_percentile(value: float, history: list[float]) -> float:
    """value 在 history（升序或任意序）中的百分位，低=便宜(被低估)。
    rank = 小于等于 value 的比例（历史最高 → 近 1）; history 越大越接近 1。"""
    hi_vals = [h for h in history if h is not None and not (isinstance(h, float) and math.isnan(h))]
    if not hi_vals:
        return 0.5
    below = sum(1 for h in hi_vals if h <= value)
    return _clip(below / len(hi_vals))

def quality_score(scale_wan=0.0, turnover_rate=0.0, fee_bps=50.0, tracking_err=None) -> float:
    """质量维度: 规模(亿, 避清盘)、换手/成交额代理流动性、费率(低优)、跟踪误差(紧优)。
    权重: 规模0.4 流动性0.2 费率0.2 跟踪0.2。缺项用中性0.5 参与, 全缺→0.5。"""
    s = _clip(math.log10(scale_wan + 1) / 2.5) if scale_wan else 0.5        # 0→1亿→100亿+非线性
    liq = _clip((math.log10(turnover_rate + 0.01) + 0.5) / 1.5) if turnover_rate else 0.5
    fee = _clip(1 - (fee_bps - 10) / 80) if fee_bps else 0.5                # 10bps 满, 90bps 近 0
    tr = _clip(1 - (tracking_err or 0) / 2.0) if tracking_err is not None else 0.5
    return 0.4 * s + 0.2 * liq + 0.2 * fee + 0.2 * tr

def long_score(valuation_pct: float, quality: float, w_val=0.55, w_qual=0.45) -> float:
    denom = w_val + w_qual
    return (w_val * _clip(1.0 - valuation_pct) + w_qual * quality) / denom * 100.0


_SHORT_FACTORS_DEF = {
    "momentum_5": {"key": "momentum", "n": 5},
    "momentum_20": {"key": "momentum", "n": 20},
    "vol_corr_20": {"key": "vol_corr", "n": 20},
    "volatility_20": {"key": "volatility", "n": 20},
}


def _panel_factor(close, amount, name, spec):
    """计算单因子面板(date×code)；compute_factor 缺 amount 时对需要 amount 的
    因子返回全 NaN 空面板(不抛异常) → 视该因子不可用(avail=False)。"""
    try:
        pv = bt_eval.compute_factor(close, name, params={"n": spec["n"]}, amount=amount)
        if pv is None or pv.shape[0] == 0 or pv.isna().all().all():
            return pd.DataFrame(index=close.index, columns=close.columns), False
        return pv, True
    except Exception:
        return pd.DataFrame(index=close.index, columns=close.columns), False


def short_scores(close, amount, codes, period_ns):
    """短清单量价横截面 rank 评分：复用 eval.compute_factor 面板因子，
    逐 code 按可用因子等权重归一聚合(缺失从不伪造 0)。
    返 (scores[c]∈[0,100], details[c][factor]=分位*100或None, coverage[c]=可用权重/权重和)。"""
    factor_maps = {n: {} for n in _SHORT_FACTORS_DEF}
    avail = {n: False for n in _SHORT_FACTORS_DEF}
    for name, spec in _SHORT_FACTORS_DEF.items():
        pv, ok = _panel_factor(close, amount, name, spec)
        avail[name] = ok
        for c in codes:
            if c in pv.columns:
                val = pv[c].iloc[-1]
                factor_maps[name][c] = None if val is None or (isinstance(val, float) and math.isnan(val)) else float(val)
    # 只保留真正可用的因子名，套 nextday 加权范式(缺失从不伪造 0)
    usable = {n: {} for n in _SHORT_FACTORS_DEF if avail[n]}
    weights = {n: 1.0 for n in usable}          # 等权, 可在验证后 IC 校准
    wm = {n: weights.get(n, 1.0) for n in usable}
    pct = {n: _nd._rank_pct(factor_maps[n]) for n in usable}
    scores, details, coverage = {}, {}, {}
    for c in codes:
        present = {n: pct[n].get(c) for n in usable if pct[n].get(c) is not None}
        denom = sum(wm[n] for n in present)
        scores[c] = round((sum(wm[n] * v for n, v in present.items()) / denom * 100) if denom else 0.0, 2)
        details[c] = {n: (round(pct[n].get(c) * 100, 2) if pct[n].get(c) is not None else None) for n in usable}
        coverage[c] = round(denom / sum(wm.values()), 4)
    return scores, details, coverage


# ------------------------------------------------------------------
# 数据适配层 + QDII 溢价 + long/short 编排入口
# 合规：输出"筛选/排序/观察清单"，机械排序，非荐股非买卖信号。
# GDPR 式数据诚实：etf_spot 表无 nav/fund_scale 列(仅 code/name/latest_price/
#   change_pct/turnover_amount/turnover_rate)——QDII 溢价与质量 meta 一律经
#   _fetch_* 抽象取得(Phase 0 后按实测源实现)，绝不读 etf_spot 缺字段。
# 每个 _fetch_* 失败 → 诚实 None(该维度走中性/不判门槛)，不抛不崩。
# ------------------------------------------------------------------

def _to_f(v):
    """健壮转 float；None/NaN/异常 → None。"""
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if (math.isnan(f) or math.isinf(f)) else f


def _nan(v):
    """数值 NaN/±inf → None(starlette JSONResponse allow_nan=False 会 500)。
    仅处理数值; 字符串/code/布尔与嵌套容器(dict/list)原样保留, 不被转 float。"""
    if v is None:
        return None
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        f = float(v)
        if math.isnan(f) or math.isinf(f):
            return None
        return f
    return v


def _to_record(item: dict) -> dict:
    """列表项统一 NaN→None 净化器; 仅对顶层数值 NaN 做 None 化, 字符串与嵌套保留。"""
    out = {}
    for k, val in item.items():
        if isinstance(val, dict):                       # 嵌套因子分位: 逐值净化保留 dict
            out[k] = {kk: _nan(vv) for kk, vv in val.items()}
        else:
            out[k] = _nan(val)
    return out


def _probe_flags() -> dict:
    """读 Phase 0 数据源探针结果 etf_source_probe.json；缺省全 False(未探测)。"""
    default = {"fund_etf_spot_em": False, "fund_etf_hist_sina": False,
               "fund_etf_fund_info_em": False, "csindex": False}
    candidates = ["etf_source_probe.json",
                  os.path.join(os.path.dirname(__file__), "..", "etf_source_probe.json"),
                  os.path.join(os.path.dirname(__file__), "..", "data", "etf_source_probe.json")]
    for p in candidates:
        try:
            if os.path.exists(p):
                import json
                with open(p, encoding="utf-8") as f:
                    loaded = json.load(f)
                default.update({k: bool(v) for k, v in loaded.items()})
                return default
        except Exception:
            pass
    return default


def _etf_spot_em_df():
    """东财全市场 ETF 快照(最新价/IOPV实时估值/总市值)。整表一次调用模块级缓存 300s。
    仅容器采集层(akshare)可用；失败/未装 → None。被 _fetch_qdii_premium/_fetch_quality_meta 复用。"""
    if not _AK_OK:
        return None
    now = time.time()
    if _ETF_SPOT_EM_CACHE["df"] is not None and now - _ETF_SPOT_EM_CACHE["ts"] < 300:
        return _ETF_SPOT_EM_CACHE["df"]
    try:
        df = _ak_mod.fund_etf_spot_em()
        if df is not None and not df.empty:
            _ETF_SPOT_EM_CACHE["df"] = df
            _ETF_SPOT_EM_CACHE["ts"] = now
        return df
    except Exception:
        return None


def fund_premium(market_price, nav) -> float | None:
    """场内溢价率 = (price - nav) / nav。任缺或 nav=0 → None(诚实缺省)。"""
    m = _to_f(market_price)
    n = _to_f(nav)
    if m is None or n is None or n == 0:
        return None
    return _nan((m - n) / n)


def _fetch_qdii_premium(code) -> float | None:
    """QDII 场内溢价率(绝对语义 (price-nav)/nav)。Phase 0 后按 fund_etf_spot_em 的
    最新价 vs IOPV实时估值 实现；失败 → None。**不读 etf_spot.nav(表无此列)。**"""
    try:
        df = _etf_spot_em_df()
        if df is None or df.empty:
            return None
        code_col = next((c for c in ("代码", "code") if c in df.columns), None)
        price_col = next((c for c in ("最新价", "最新价(元)") if c in df.columns), None)
        iopv_col = next((c for c in ("IOPV实时估值", "IOPV估值") if c in df.columns), None)
        if not code_col or price_col is None or iopv_col is None:
            return None
        match = df[df[code_col].astype(str).str.zfill(6) == str(code).zfill(6)]
        if match.empty:
            return None
        row = match.iloc[0]
        price = _to_f(row.get(price_col))
        iopv = _to_f(row.get(iopv_col))
        if price is None or iopv is None or iopv == 0:
            return None
        return fund_premium(price, iopv)
    except Exception:
        return None


def _display_name(code, spot=None):
    """返回 ETF 展示名称；本地占位代码时尝试复用全市场快照名称。"""
    key = str(code)
    local = (spot or {}).get("name")
    if local is not None and str(local).strip() and str(local).strip() != key:
        return local
    try:
        df = _etf_spot_em_df()
        if df is not None and not df.empty:
            code_col = next((c for c in ("代码", "code") if c in df.columns), None)
            name_col = next((c for c in ("名称", "基金简称", "name") if c in df.columns), None)
            if code_col and name_col:
                match = df[df[code_col].astype(str).str.zfill(6) == key.zfill(6)]
                if not match.empty:
                    name = match.iloc[0].get(name_col)
                    if name is not None and str(name).strip() and str(name).strip() != key:
                        return name
    except Exception:
        pass
    return local


def _fetch_quality_meta(code) -> dict | None:
    """{'fund_scale': 亿, 'fee_bps': int|None, 'tracking_err': float|None}。
    质量 meta 经 fund_etf_spot_em 的 总市值 代理规模；fee_bps/tracking_err 无可靠源 → None
    该维度走中性 0.5。失败/缺数据 → None(不判硬门槛)。"""
    try:
        df = _etf_spot_em_df()
        if df is None or df.empty:
            return None
        code_col = next((c for c in ("代码", "code") if c in df.columns), None)
        scale_col = next((c for c in ("总市值", "总市值(元)") if c in df.columns), None)
        if not code_col or scale_col is None:
            return None
        match = df[df[code_col].astype(str).str.zfill(6) == str(code).zfill(6)]
        if match.empty:
            return None
        total_mv = _to_f(match.iloc[0].get(scale_col))
        if total_mv is None:
            return None
        return {"fund_scale": _nan(total_mv / 1e8),   # 元 → 亿
                "fee_bps": None, "tracking_err": None}
    except Exception:
        return None


def _fetch_index_valuation(code) -> dict | None:
    """{'pe_pct': 0..1, 'div_yield': float|None}；指数历史估值分位, 低=便宜。
    本任务 csindex 映射未接线 → 诚实返 None(该维度走中性 0.5)；单测注入。"""
    return None


def _load_history_panel(universe, codes, start, end, field):
    """经 bt_eval.load_panel 取历史宽面板。QDII 不在 _UNIVERSE(无 etf_daily 表) →
    降级用 ETF 表加载(仅个股差异, 面板结构同)。无 codes → 传全部列。"""
    hist_uni = universe if universe in _UNIVERSE else "ETF"
    if not codes:
        return bt_eval.load_panel(hist_uni, [], start, end, field)
    return bt_eval.load_panel(hist_uni, codes, start, end, field)


def _rank_short(universe, codes, start, end, limit):
    """短清单：量价横截面 rank 评分(短_scores) → 降序。premium/source 附注。"""
    close = _load_history_panel(universe, codes, start, end, "close")
    amount = _load_history_panel(universe, codes, start, end, "amount")
    use_codes = list(close.columns) if codes is None else codes
    if not use_codes:
        return []
    scores, details, coverage = short_scores(close, amount, use_codes, [5, 20])
    try:
        spot_rows = _db.query_rows("etf_spot", limit=0) or []
        name_by = {str(r.get("code")): r.get("name") for r in spot_rows if r.get("code") is not None}
    except Exception:
        name_by = {}
    items = []
    for c in use_codes:
        item = _to_record({
            "code": c,
            "name": _display_name(c, {"name": name_by.get(c)}),
            "score": scores.get(c),
            "factor_scores": details.get(c),
            "coverage": coverage.get(c),
            "premium": _nan(_fetch_qdii_premium(c)),
            "source": "etf_daily量价横截面",
        })
        items.append(item)
    items.sort(key=lambda x: (x.get("score") is not None, x.get("score") or 0), reverse=True)
    return items[:limit] if limit else items


def _rank_long(universe, codes, start, end, limit):
    """长清单：质量(规模硬门槛)+估值分位 → long_score 降序；QDII 高溢价降权标 risk_flag。"""
    rows = _db.query_rows("etf_spot", limit=0) or []
    spot_by = {str(r.get("code")): r for r in rows if r.get("code") is not None}
    use_codes = codes or [c for c in spot_by if c]
    items = []
    for c in use_codes:
        spot = spot_by.get(str(c)) or {}
        meta = _fetch_quality_meta(c)
        fund_scale = (meta or {}).get("fund_scale") if meta else None
        # 规模硬门槛: meta 可得且 < 最小规模 → 跳过; meta 缺失则该维度不判门槛
        fs = _to_f(fund_scale)
        if fs is not None and fs < _MIN_SCALE:
            continue
        turnover_rate = _to_f(spot.get("turnover_rate"))
        turnover_amount = _to_f(spot.get("turnover_amount"))
        quality = quality_score(
            scale_wan=(fs * 1e4) if fs is not None else 0.0,   # 亿 → 万 对齐 quality_score 参数
            turnover_rate=turnover_rate or 0.0,
            fee_bps=(meta or {}).get("fee_bps") if meta else None,
            tracking_err=(meta or {}).get("tracking_err") if meta else None,
        )
        val = _fetch_index_valuation(c) or {}
        vp = _to_f(val.get("pe_pct"))
        vp = vp if vp is not None else 0.5                       # 源缺 → 中性 0.5
        premium = _nan(_fetch_qdii_premium(c))
        score = long_score(valuation_pct=vp, quality=quality)
        item = _to_record({
            "code": c,
            "name": _display_name(c, spot),
            "score": round(score, 2),
            "quality_score": round(quality, 4),
            "valuation_percentile": vp,                          # 源给则直接映射 pe_pct
            "div_yield": _nan(val.get("div_yield")),
            "premium": premium,
            "latest_price": _to_f(spot.get("latest_price")),
            "turnover_rate": turnover_rate,
            "fund_scale": fund_scale,
            "source": "etf_spot+估值/质量meta",
        })
        # QDII 高溢价 → 该 item 标风险, 不硬拒(观察清单机械标注)
        if premium is not None and premium > 0.03:
            item["risk_flag"] = "high_premium"
        items.append(item)
    items.sort(key=lambda x: x.get("score") or 0, reverse=True)
    return items[:limit] if limit else items


def etf_screen_rank(universe="ETF", mode="long", codes=None,
                    limit=50, days=365) -> dict:
    """ETF/QDII 长短清单编排入口。

    键=(universe, mode, tuple(codes or ()), limit, days)，30s 进程缓存(测试可 _CACHE.clear())。
    mode="long" 填 long_term, "short" 填 short_term, 另一为空 list。
    QDII 高溢价: long 降权(risk_flag 标注), short 顶部醒目(score 已按量价, premium 附注)。
    disclaimer 由路由 _wrap 处理, 本模块不附。
    """
    ck = tuple(codes) if codes else ()
    key = (universe, mode, ck, limit, days)
    now = time.time()
    hit = _CACHE.get(key)
    if hit and now - hit[0] < _CACHE_TTL:
        return hit[1]

    today = datetime.now().strftime("%Y%m%d")
    start = (datetime.now() - timedelta(days=max(days - 1, 1))).strftime("%Y%m%d")

    if mode == "long":
        long_term = _rank_long(universe, codes, start, today, limit)
        short_term = []
    else:
        short_term = _rank_short(universe, codes, start, today, limit)
        long_term = []

    out = {
        "universe": universe,
        "mode": mode,
        "count": len(long_term or short_term),
        "limit": limit,
        "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "long_term": long_term,
        "short_term": short_term,
    }
    _CACHE[key] = (now, out)
    return out