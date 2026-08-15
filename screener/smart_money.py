# -*- coding: utf-8 -*-
"""主力动向查询/聚合层（不触网，纯 db.query_rows；
behavior_series 资金流通道表无记录时按需调 finshare get_money_flow_stock 补
历史序列为例外，诚实标 source=finshare，非买卖信号）。

合规：输出"主力动向观察清单/机械归类"，不荐股、不输出买卖点、不承诺收益。
依赖 smart_money_action 表（由 data/smart_money.refresh_today 写入）。
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta

import numpy as np

from data import db, smart_money as sm_data

_NATIONAL_TEAM_KEYWORD = "国家队"


def _expand_national_team() -> list[str]:
    return list(sm_data.NATIONAL_TEAM)


def today_list(date: str | None = None, channel: str | None = None,
               market: str | None = None, days: int = 7,
               limit: int = 1000) -> dict:
    """用法 A：主力动向清单，按 amount 降序。
    date 指定时按单日；省略时取表内最新日期往前 days 日窗口（默认7日），
    平衡数据量与加载速度，避免返回全表数万行。
    limit 默认 1000（amount DESC 已排序，截 top N 覆盖大额，0=无限制）；
    前端 flow 视图 CAP 300 显示，actor/code 聚合覆盖大额席位即够。"""
    where, params = [], []
    if not date:
        try:
            # 取表内最新日期往前 days 日窗口。排除未来日期——解禁通道把
            # 未来解禁日写进 date 列，若不封顶 max(date)=未来解禁日，会
            # 把今日真实的资金流/龙虎榜数据全过滤掉，today 视图只剩未来
            # 解禁行（"深查主力没效果"的根因）。封顶 today 取最新实盘日。
            today_str = datetime.now().strftime("%Y-%m-%d")
            latest = db.query_rows("smart_money_action",
                                   where="date <= ?", params=(today_str,),
                                   order_by="date DESC", limit=1)
            if latest:
                latest_date = latest[0].get("date")
                end = datetime.strptime(latest_date, "%Y-%m-%d")
                start = (end - timedelta(days=max(days - 1, 0))).strftime("%Y-%m-%d")
                where.append("date >= ?"); params.append(start)
                where.append("date <= ?"); params.append(latest_date)
                date = latest_date   # 响应回传实际最新日期
        except Exception:
            pass
    else:
        where.append("date = ?"); params.append(date)
    if channel:
        where.append("channel = ?"); params.append(channel)
    if market:
        where.append("market = ?"); params.append(market)
    w = " AND ".join(where) if where else ""
    rows = db.query_rows("smart_money_action", where=w, params=tuple(params),
                         order_by="amount DESC", limit=limit)
    _attach_intensity(rows)   # O1: 主力净额/当日成交额 强度归一化
    return {"rows": rows, "total": len(rows), "date": date}


def by_actor(actor: str, days: int = 30) -> dict:
    """用法 B：某席位/股东 N 日内动向记录 + 汇总。
    actor 传席位名/股东名子串，或保留词"国家队"（展开为 LIKE 多名匹配）。
    actor 过滤下推 SQL LIKE（不全表拉到内存再 Python 过滤）；国家队展开为 OR 多名。"""
    since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    where_parts = ["date >= ?"]
    params: list[str] = [since]
    if actor == _NATIONAL_TEAM_KEYWORD:
        keys = _expand_national_team()
        if keys:
            ors = " OR ".join(["actor LIKE ?"] * len(keys))
            where_parts.append(f"AND ({ors})")
            params += [f"%{k}%" for k in keys]
        else:
            where_parts.append("AND actor LIKE ?")
            params.append(f"%{actor}%")
    else:
        where_parts.append("AND actor LIKE ?")
        params.append(f"%{actor}%")
    rows = db.query_rows("smart_money_action",
                         where=" ".join(where_parts),
                         params=tuple(params),
                         order_by="date DESC", limit=0)
    amt = 0.0
    for r in rows:
        a = r.get("amount")
        if a is not None:
            amt += a
    return {"rows": rows,
            "summary": {"出现次数": len(rows), "累计净额": amt}}


def _attach_intensity(rows: list[dict]) -> list[dict]:
    """给 smart_money_action 行附 net_intensity = 主力净额/当日成交额(占当日成交比)。
    操盘手强度维度:绝对额不可跨市值比较,占比才可比。无成交额→None。
    只读 stock_spot,机械归一化,非买卖信号。"""
    try:
        spots = db.query_rows("stock_spot", limit=0)
        tnv = {}
        for s in spots:
            c = str(s.get("code"))
            t = s.get("turnover_amount")
            if t and t != 0:
                tnv[c] = float(t)
        for r in rows:
            t = tnv.get(str(r.get("code")))
            a = r.get("amount")
            r["net_intensity"] = round(float(a) / t, 4) if (t and a is not None) else None
    except Exception:
        for r in rows:
            r.setdefault("net_intensity", None)
    return rows


def top_by_amount(days: int = 5, market: str | None = None,
                  channel: str | None = None, limit: int = 30) -> dict:
    """用法 C：按 N 日累计主力净额排序的观察池（group by code 降序）。
    附 net_intensity 强度(主力净额/成交额)。SQL GROUP BY 下推因测试 mock
    query_rows 架构未启用,内存聚合毫秒级可接受(真实瓶颈在采集层 P1/P2/P3)。"""
    since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    where, params = ["date >= ?"], [since]
    if market:
        where.append("market = ?"); params.append(market)
    if channel:
        where.append("channel = ?"); params.append(channel)
    rows = db.query_rows("smart_money_action", where=" AND ".join(where),
                         params=tuple(params), order_by="", limit=0)
    agg: dict[str, dict] = {}
    for r in rows:
        code = r.get("code")
        if not code:
            continue
        cur = agg.setdefault(code, {"code": code, "name": r.get("name"),
                                    "market": r.get("market"),
                                    "amount": 0.0, "count": 0})
        a = r.get("amount")
        if a is not None:
            cur["amount"] += a
        cur["count"] += 1
    pool = sorted(agg.values(), key=lambda x: x["amount"], reverse=True)[:limit]
    for p in pool:
        p["amount"] = _nan(p.get("amount"))
        cnt = p.get("count")
        p["count"] = int(cnt) if cnt is not None else 0
    _attach_intensity(pool)
    return {"rows": pool, "total": len(pool)}


def unlock_by_month(month: str | None = None, code: str | None = None) -> dict:
    """限售解禁按 as_of 月份查(channel=限售解禁)。
    month 形如 2026-07;不传取当月。code 非空则再按 code 过滤。as_of 升序。

    合规:主力动向观察清单,机械归类,非荐股非买卖信号。"""
    if not month:
        month = datetime.now().strftime("%Y-%m")
    where, params = ["channel = ?", "as_of LIKE ?"], ["限售解禁", f"{month}%"]
    if code:
        where.append("code = ?")
        params.append(code)
    rows = db.query_rows("smart_money_action", where=" AND ".join(where),
                         params=tuple(params), order_by="as_of ASC", limit=0)
    total_amt = 0.0
    for r in rows:
        a = r.get("amount")
        if a is not None:
            total_amt += a
    return {"rows": rows, "total": len(rows),
            "month": month, "total_amount": total_amt}


# ---------- 筹码分布(P1) ----------
# 复用 backtest.signals._uni_panels 取 stock_daily 的 close+amount，
# 不触网、不入库（进程内按需算）。合规：机械统计，非支撑/压力位预测。

def _nan(v):
    """转 float，NaN/异常→None（防 JSONResponse allow_nan=False 500）。"""
    if v is None:
        return None
    try:
        f = float(v)
        return None if math.isnan(f) else f
    except (TypeError, ValueError):
        return None


def _weighted_percentile(values, weights, qs):
    """加权分位（按权重累积定位）。values/weights 1D，已去 NaN。返回 qs 对应分位值。"""
    v = np.asarray(values, float)
    w = np.asarray(weights, float)
    m = ~np.isnan(v) & ~np.isnan(w) & (w > 0)
    v, w = v[m], w[m]
    if len(v) == 0:
        return [None] * len(qs)
    idx = np.argsort(v)
    v, w = v[idx], w[idx]
    cum = np.cumsum(w) / w.sum()
    out = []
    for q in qs:
        i = int(np.searchsorted(cum, q))
        i = min(max(i, 0), len(v) - 1)
        out.append(float(v[i]))
    return out


def _histogram(values, weights, bins=20):
    """等宽分箱直方图（price=箱中值/amount=箱内权重和/pct=占比）。"""
    v = np.asarray(values, float)
    w = np.asarray(weights, float)
    m = ~np.isnan(v) & ~np.isnan(w)
    v, w = v[m], w[m]
    if len(v) == 0:
        return []
    lo, hi = float(v.min()), float(v.max())
    total = float(w.sum())
    if lo == hi or total <= 0:
        return [{"price": round(lo, 2), "amount": round(total, 2), "pct": 1.0}]
    edges = np.linspace(lo, hi, bins + 1)
    idx = np.clip(np.digitize(v, edges) - 1, 0, bins - 1)
    out = []
    for i in range(bins):
        sel = idx == i
        a = float(w[sel].sum())
        out.append({"price": round(float((edges[i] + edges[i + 1]) / 2), 2),
                    "amount": round(a, 2),
                    "pct": round(a / total, 4) if total else 0.0})
    return out


def chip_distribution(code: str, window: int = 60,
                      spot_price: float | None = None) -> dict:
    """移动成交量加权成本分布（收盘价代理成交价）。

    依赖 stock_daily 历史(需先 /api/backtest/fetch 拉该 code)；无历史→need_history=True。
    返回 avg_cost(加权平均成本)/profit_ratio(获利盘比例)/loss_ratio(套牢盘比例)/
    chip_concentration(加权价格标准差/均价，越小越集中)/chip_range_90(90%筹码区间宽/均价)/
    distribution(分箱直方图)。spot 取值优先级:参数>stock_spot.latest_price>daily末值。

    合规:移动成本分布机械统计，非支撑位/压力位预测，非买卖信号。
    """
    from backtest.signals import _uni_panels
    base = {"code": code, "window": window, "need_history": False,
            "avg_cost": None, "profit_ratio": None, "loss_ratio": None,
            "chip_concentration": None, "chip_range_90": None,
            "distribution": [], "n_days": 0, "spot": None, "spot_source": None}
    close, amount = _uni_panels("stock", [code])
    if close is None or close.empty or code not in close.columns:
        base["need_history"] = True
        return base
    sc = close[code].dropna().iloc[-window:]
    sa = None
    if amount is not None and code in amount.columns:
        sa = amount[code].dropna()
        common = sc.index.intersection(sa.index)
        if len(common):
            sc = sc.loc[common]
            sa = sa.loc[common]
        else:
            sa = None
    n = len(sc)
    base["n_days"] = n
    if n < 5:
        base["need_history"] = True
        return base
    c = sc.values.astype(float)
    if sa is not None:
        w = sa.reindex(sc.index).values.astype(float)
    else:
        w = np.ones(n)  # amount 缺失→等权降级
    w = np.where(np.isnan(w) | (w < 0), 0.0, w)
    total = float(w.sum())
    if total <= 0:
        w = np.ones(n)
        total = float(n)
    avg_cost = float((c * w).sum() / total)

    spot = _nan(spot_price)
    src = "param"
    if spot is None:
        sp = db.query_rows("stock_spot", where="code = ?",
                           params=(code,), limit=1)
        if sp and sp[0].get("latest_price") is not None:
            spot = _nan(sp[0]["latest_price"])
            src = "spot"
    if spot is None:
        spot = float(c[-1])
        src = "daily_close"

    profit_w = float(w[c < spot].sum())
    profit_ratio = profit_w / total
    var = float((w * (c - avg_cost) ** 2).sum() / total)
    chip_conc = (math.sqrt(var) / avg_cost) if avg_cost else None
    p05, p95 = _weighted_percentile(c, w, [0.05, 0.95])
    chip_range_90 = None
    if avg_cost and p05 is not None and p95 is not None:
        chip_range_90 = (p95 - p05) / avg_cost
    dist = _histogram(c, w, bins=20)
    return {**base,
            "avg_cost": round(avg_cost, 4),
            "profit_ratio": round(profit_ratio, 4),
            "loss_ratio": round(1 - profit_ratio, 4),
            "chip_concentration": round(chip_conc, 4) if chip_conc is not None else None,
            "chip_range_90": round(chip_range_90, 4) if chip_range_90 is not None else None,
            "distribution": dist,
            "spot": round(spot, 4), "spot_source": src}


# ---------- 主力行为序列(P2) ----------
# 只读 smart_money_action，按 channel 聚合到 code-date 级，算连续性/边际。
# 合规:主力行为序列机械统计，非买卖信号。

def _streak(amounts: list[float]) -> tuple[int, int]:
    """连续末尾正/负天数。amounts 按日期升序。返回 (连续净流入日, 连续净流出日)。"""
    if not amounts:
        return 0, 0
    last = amounts[-1]
    if last > 0:
        s = 0
        for a in reversed(amounts):
            if a > 0:
                s += 1
            else:
                break
        return s, 0
    if last < 0:
        s = 0
        for a in reversed(amounts):
            if a < 0:
                s += 1
            else:
                break
        return 0, s
    return 0, 0


def _finshare_fund_flow_series(code: str, days: int) -> dict | None:
    """finshare 个股资金流历史按需补（仅 behavior_series 表无资金流记录时调）。

    fs.get_money_flow_stock(code) 取主力净额(main_net,元)序列，截近 days 日。
    不走东财 push2 被封端点（实测可用）。finshare 未装/失败/空→None（上层保持
    原行为）。返回与 behavior_series channels 项同构 dict，附 source=finshare。"""
    try:
        import finshare as fs
    except Exception:
        return None
    try:
        df = fs.get_money_flow_stock(code)
    except Exception:
        return None
    if df is None or getattr(df, "empty", True) or "main_net" not in df.columns:
        return None
    if "trade_time" not in df.columns:
        return None
    dates = [sm_data._norm_date(v) for v in df["trade_time"].tolist()]
    amounts = []
    for x in df["main_net"].tolist():
        if x is None:
            continue
        try:
            f = float(x)
        except (TypeError, ValueError):
            continue
        if not math.isnan(f):
            amounts.append(f)
    pairs = [(d, a) for d, a in zip(dates, amounts) if d]
    if not pairs:
        return None
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    pairs = [(d, a) for d, a in pairs if d >= cutoff]
    if not pairs:
        return None
    pairs.sort(key=lambda x: x[0])
    dts = [p[0] for p in pairs]
    amts = [p[1] for p in pairs]
    si, so = _streak(amts)
    cum = float(sum(amts))
    accel = None
    if len(amts) >= 5:
        recent5 = float(np.mean(amts[-5:]))
        base_avg = (float(np.mean(amts[-20:])) if len(amts) >= 20
                    else float(np.mean(amts)))
        accel = recent5 - base_avg
    return {"streak_inflow": si, "streak_outflow": so,
            "cum_inflow": round(cum, 2),
            "margin_accel": round(accel, 2) if accel is not None else None,
            "daily": [{"date": d, "amount": round(a, 2)} for d, a in zip(dts, amts)],
            "n_days": len(amts), "source": "finshare"}


def behavior_series(code: str, days: int = 30) -> dict:
    """主力行为时间序列(多通道)。

    查 smart_money_action 近 days 日，按 channel(资金流/北向/龙虎榜) 聚合到
    code-date 级(amount 求和)，算:连续净流入/流出天数、累计净额、边际加速
    (近5日均额-近20日均额，>0 加速)、daily 序列。顶层取资金流口径。
    **资金流通道表无记录(停摆/该股未采集)时，按需调 finshare get_money_flow_stock
    补主力净额历史序列(不走东财被封端点,标 source=finshare)；finshare 不可用
    则该通道空。**

    合规:主力行为序列机械统计，非买卖信号，盈亏自负。
    """
    since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    base = {"code": code, "days": days, "channels": {},
            "streak_inflow": None, "streak_outflow": None,
            "cum_inflow": None, "margin_accel": None, "daily": [],
            "note": ""}
    rows = db.query_rows("smart_money_action",
                        where="code = ? AND date >= ?",
                        params=(code, since),
                        order_by="date ASC", limit=0)
    for ch in ("资金流", "北向", "龙虎榜"):
        ch_rows = [r for r in rows if r.get("channel") == ch]
        if not ch_rows:
            continue
        by_date: dict[str, float] = {}
        for r in ch_rows:
            d = r.get("date")
            if not d:
                continue
            a = r.get("amount") or 0.0
            by_date[d] = by_date.get(d, 0.0) + float(a)
        dates_sorted = sorted(by_date.keys())
        amounts = [by_date[d] for d in dates_sorted]
        si, so = _streak(amounts)
        cum = float(sum(amounts))
        accel = None
        if len(amounts) >= 5:
            recent5 = float(np.mean(amounts[-5:]))
            base_avg = float(np.mean(amounts[-20:])) if len(amounts) >= 20 else float(np.mean(amounts))
            accel = recent5 - base_avg
        base["channels"][ch] = {
            "streak_inflow": si, "streak_outflow": so,
            "cum_inflow": round(cum, 2),
            "margin_accel": round(accel, 2) if accel is not None else None,
            "daily": [{"date": d, "amount": round(by_date[d], 2)} for d in dates_sorted],
            "n_days": len(amounts),
        }
    # 资金流通道表无记录 → finshare 按需补(不走东财被封端点,实测可用)
    if "资金流" not in base["channels"]:
        ff_fs = _finshare_fund_flow_series(code, days)
        if ff_fs:
            base["channels"]["资金流"] = ff_fs
            # 落库缓存(标 source=finshare): 下次同 code 直接 DB 读避免重拉。
            # 与 collect_fund_flow 当日行同 channel/actor，UNIQUE upsert 覆盖
            # 历史日期不冲突；raw.source 区分来源。属 behavior 按需触发的写，
            # 非 refresh 全量(单股单次)，与"不进 refresh 避免 5200 次调用"一致。
            try:
                recs = [sm_data._rec(d["date"], code, None, "股票",
                                     "资金流", "主力资金", "净买入",
                                     d.get("amount"),
                                     raw={"source": "finshare",
                                          "净额(元)": d.get("amount")})
                        for d in ff_fs.get("daily", [])]
                if recs:
                    db.upsert_rows("smart_money_action", recs)
            except Exception:
                pass
    # 顶层取资金流口径(最全每日序列)
    ff = base["channels"].get("资金流", {})
    base["streak_inflow"] = ff.get("streak_inflow")
    base["streak_outflow"] = ff.get("streak_outflow")
    base["cum_inflow"] = ff.get("cum_inflow")
    base["margin_accel"] = ff.get("margin_accel")
    base["daily"] = ff.get("daily", [])
    if not base["channels"]:   # 表空 + finshare 皆无 → 诚实提示
        base["note"] = "无主力动向记录(需先 /api/smart-money/refresh 采集,或该股无finshare资金流历史)"
    return base


def _behavior_batch(codes: list[str], days: int = 30) -> dict[str, dict]:
    """批量行为序列(供 quality 口径3)：**一次**查 smart_money_action since days 前，
    按 code×channel 聚合算 streak_inflow/outflow/margin_accel/北向cum。

    与 behavior_series 的区别：① 一次 DB 查询服务多个 code(批量)；② 不调 finshare
    回退(批量场景，缺记录→对应字段 None，不触网)；③ 不返 daily/顶层 channels，仅返
    口径3 所需 4 个标量。

    合规:主力行为序列机械统计，非买卖信号，盈亏自负。
    """
    out: dict[str, dict] = {c: {
        "streak_inflow": None, "streak_outflow": None,
        "margin_accel": None, "north_cum": None,
    } for c in codes}
    if not codes:
        return out
    since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    rows = db.query_rows("smart_money_action",
                        where="date >= ?",
                        params=(since,),
                        order_by="date ASC", limit=0)
    # code×channel→{date: amount}
    grid: dict[str, dict[str, dict[str, float]]] = {}
    for r in rows:
        c = r.get("code")
        if c not in out:
            continue
        ch = r.get("channel")
        d = r.get("date")
        if not ch or not d:
            continue
        a = r.get("amount") or 0.0
        grid.setdefault(c, {}).setdefault(ch, {})
        grid[c][ch][d] = grid[c][ch].get(d, 0.0) + float(a)
    for c in codes:
        chans = grid.get(c, {})
        # 资金流口径:连续性 + 边际加速
        ff = chans.get("资金流")
        if ff:
            dates_sorted = sorted(ff.keys())
            amounts = [ff[d] for d in dates_sorted]
            si, so = _streak(amounts)
            out[c]["streak_inflow"] = si
            out[c]["streak_outflow"] = so
            if len(amounts) >= 5:
                recent5 = float(np.mean(amounts[-5:]))
                base_avg = (float(np.mean(amounts[-20:]))
                            if len(amounts) >= 20 else float(np.mean(amounts)))
                out[c]["margin_accel"] = round(recent5 - base_avg, 2)
        # 北向口径:累计净额(聪明资金代理)
        nb = chans.get("北向")
        if nb:
            out[c]["north_cum"] = round(float(sum(nb.values())), 2)
    return out


# ---------------------------------------------------------------------------
# 主力阶段判定 main_force_phase (计分制4阶段+观望)
# 复用 behavior_series + chip_distribution + spot + _uni_panels, 零新数据源。
# 进程缓存30s(依赖日级数据, 避免高频重算 chip)。
# ---------------------------------------------------------------------------
import time as _time_mod
_PHASE_CACHE: dict = {}
_PHASE_TTL = 30.0

def _high60_pct(code: str) -> float | None:
    """latest_price 在近60日 close 的 [min,max] 区间分位(0-1)。无历史→None。"""
    try:
        from backtest.signals import _uni_panels
        close, _ = _uni_panels("stock", [code])
        if close is None or close.empty or code not in close.columns:
            return None
        s = close[code].dropna().iloc[-60:]
        if len(s) < 5:
            return None
        lo, hi = float(s.min()), float(s.max())
        sp = db.query_rows("stock_spot", where="code = ?", params=(code,), limit=1)
        lp = None
        if sp:
            lp = _nan(sp[0].get("latest_price"))
        if lp is None:
            lp = float(s.iloc[-1])
        if hi == lo:
            return 0.5
        return round(min(max((lp - lo) / (hi - lo), 0.0), 1.0), 4)
    except Exception:
        return None

def _turnover5_avg(code: str) -> float | None:
    """近5日成交额均值(元)。无历史→None。"""
    try:
        from backtest.signals import _uni_panels
        _, amount = _uni_panels("stock", [code])
        if amount is None or amount.empty or code not in amount.columns:
            return None
        s = amount[code].dropna().iloc[-5:]
        if s.empty:
            return None
        return float(s.mean())
    except Exception:
        return None

_PHASE_COND = {
    "出货": lambda i: [
        (i["streak_outflow"] or 0) >= 2,
        (i["profit_ratio"] is not None and i["profit_ratio"] > 0.8),
        (i["margin_accel"] is not None and i["margin_accel"] < 0),
        (i["high60_pct"] is not None and i["high60_pct"] > 0.8),
    ],
    "拉升": lambda i: [
        (i["streak_inflow"] or 0) >= 2,
        (i["change_pct"] is not None and i["change_pct"] > 3),
        i["_vol_surge"],
        (i["latest_price"] is not None and i["avg_cost"] is not None and i["latest_price"] > i["avg_cost"]),
    ],
    "吸筹": lambda i: [
        (i["streak_inflow"] or 0) >= 3,
        (i["cum_inflow"] is not None and i["cum_inflow"] > 0),
        (i["change_pct"] is not None and i["change_pct"] < 3),
        (i["profit_ratio"] is not None and i["profit_ratio"] < 0.85),
    ],
    "洗盘": lambda i: [
        (i["cum_inflow"] is not None and i["cum_inflow"] > 0),
        (i["margin_accel"] is not None and i["margin_accel"] < 0),
        (i["change_pct"] is not None and -5 < i["change_pct"] < -1),
    ],
}
_PHASE_RISK_ORDER = ["出货", "拉升", "洗盘", "吸筹"]  # 并列时保守优先

def main_force_phase(code: str, days: int = 30) -> dict:
    """主力阶段判定(计分制): 出货/拉升/吸筹/洗盘/观望。
    confidence=命中条件数/该阶段总条件数, 并列按风险优先级取保守。
    复用 behavior_series + chip_distribution + spot, 零新数据源。
    进程缓存30s(依赖日级数据, 避免高频重算 chip)。"""
    key = (str(code), days)
    now = _time_mod.time()
    hit = _PHASE_CACHE.get(key)
    if hit and now - hit[0] < _PHASE_TTL:
        return hit[1]
    bs = behavior_series(code, days)
    chip = chip_distribution(code, window=60)
    spot = db.query_rows("stock_spot", where="code = ?", params=(code,), limit=1)
    srow = spot[0] if spot else {}
    tnv5 = _turnover5_avg(code)
    tnv = _nan(srow.get("turnover_amount"))
    ind = {
        "streak_inflow": bs.get("streak_inflow"),
        "streak_outflow": bs.get("streak_outflow"),
        "cum_inflow": bs.get("cum_inflow"),
        "margin_accel": bs.get("margin_accel"),
        "profit_ratio": chip.get("profit_ratio"),
        "chip_concentration": chip.get("chip_concentration"),
        "avg_cost": chip.get("avg_cost"),
        "spot": chip.get("spot"),
        "change_pct": _nan(srow.get("change_pct")),
        "turnover_amount": tnv,
        "latest_price": _nan(srow.get("latest_price")),
        "high60_pct": _high60_pct(code),
        "tnv5_avg": tnv5,
        "_vol_surge": (tnv is not None and tnv5 is not None and tnv5 > 0 and tnv > tnv5 * 1.5),
    }
    best_phase, best_conf, best_trig = "观望", 0.0, []
    for ph in _PHASE_RISK_ORDER:  # 出货>拉升>洗盘>吸筹,并列时先迭代者胜=保守优先
        flags = _PHASE_COND[ph](ind)
        hits = sum(1 for f in flags if f)
        conf = hits / len(flags)
        trig = [f"条件{i+1}={'命中' if f else '未'}" for i, f in enumerate(flags)]
        if conf > best_conf:   # 严格大于:并列(==)保留先迭代者(出货),实现风险优先级
            best_conf, best_phase, best_trig = conf, ph, trig
    if best_conf <= 0.5:   # 需>50%置信度才出阶段,否则观望(低置信=观望)
        best_phase, best_conf, best_trig = "观望", 0.0, []
    name = srow.get("name") or code
    out = {"code": str(code), "name": name, "phase": best_phase,
           "confidence": round(best_conf, 4), "triggers": best_trig,
           "indicators": {k: _nan(v) for k, v in ind.items() if not k.startswith("_")},
           "ts": datetime.now().strftime("%Y-%m-%dT%H:%M:%S")}
    if best_phase == "观望" and best_conf == 0 and not any(ind.values()):
        out["note"] = "数据不足"
    _PHASE_CACHE[key] = (now, out)
    return out
