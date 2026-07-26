# -*- coding: utf-8 -*-
"""FastAPI 本地服务：板块/ETF 筛选接口 + 手动刷新 + meta。

启动: uvicorn api.server:app --reload --port 8000  (在 stock-screener 目录下)
合规: 每个数据响应都附 disclaimer + update_time，纯筛选无买卖点。
"""
from __future__ import annotations

import json
import pandas as pd
from typing import Any

from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import sys
from pathlib import Path
# 确保 stock-screener 根目录在 sys.path，便于 `from data import ...`
_ROOT = str(Path(__file__).resolve().parent.parent)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from data import collector, db, history, portfolio, smart_money
from data import research as research_data
from screener import engine, smart_money as sm_query
from screener.conditions import BOARD_FIELDS_CAT, ETF_FIELDS_CAT, STOCK_FIELDS_CAT, OPS
from backtest import (eval as bt_eval, engine as bt_engine, risk as bt_risk,
                      robust as bt_robust, candidates as bt_cand,
                      signals as bt_sig, buffett as bt_buf)

app = FastAPI(title="A股板块/ETF 筛选器(本地)")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],          # 本地验证用，放开
    allow_methods=["*"],
    allow_headers=["*"],
)

DISCLAIMER = "仅公开数据筛选结果，不构成投资建议，不承诺收益，不输出买卖点。市场有风险，决策请独立判断。"

# 启动即建表，避免首次访问 /api/* (未经 /api/refresh) 时 "no such table" 500
db.init_db()


def _wrap(data: Any, extra: dict | None = None) -> dict:
    """统一响应包装：附 disclaimer + update_time。"""
    out = {
        "data": data,
        "update_time": db.last_update_time(),
        "disclaimer": DISCLAIMER,
    }
    if extra:
        out.update(extra)
    return out


@app.get("/api/meta")
def meta():
    return _wrap({"update_time": db.last_update_time()})


import datetime as _dt


def _domain_status(rows, latest, stale=False, has_old=True):
    """三态：red(空/失败) > yellow(stale 或非当日有旧) > green(当日非空)。"""
    if rows is None:
        return "red"
    if rows == 0:
        return "red" if not has_old else "yellow"
    if stale:
        return "yellow"
    if latest and latest.startswith(_dt.date.today().strftime("%Y-%m-%d")):
        return "green"
    if latest:
        return "yellow"
    return "green"


def _collect_health():
    """聚合 7 数据域健康（只读，单域失败不崩）。非荐股，默认 disclaimer。"""
    domains = {}
    try:
        with db.get_conn() as conn:
            def _q(sql):
                return conn.execute(sql).fetchone()
            def _n(t):
                try:
                    r = _q(f"SELECT COUNT(*) AS n FROM {t}")
                    return r["n"] if r else 0
                except Exception:
                    return None
            ut = db.last_update_time()
            ss, es = _n("stock_spot"), _n("etf_spot")
            domains["spot"] = {"stock": {"rows": ss}, "etf": {"rows": es},
                               "status": _domain_status((ss or 0)+(es or 0), ut)}
            try:
                h = _q("SELECT COUNT(*) AS n, MIN(date) AS mn, MAX(date) AS mx, "
                       "COUNT(DISTINCT code) AS cc FROM stock_daily")
                etd, bdd = _n("etf_daily"), _n("board_daily")
                domains["history"] = {"rows": h["n"] if h else 0, "codes": h["cc"] if h else 0,
                                      "date_range": [h["mn"], h["mx"]] if h else [None, None],
                                      "etf_rows": etd, "board_rows": bdd,
                                      "status": "green" if h and h["n"] else "red"}
            except Exception as e:
                domains["history"] = {"status": "red", "err": str(e)}
            try:
                smr = _q("SELECT COUNT(*) AS n, MAX(date) AS d FROM smart_money_action")
                ch = smart_money.channel_status()
                any_stale = any(v.get("stale") for v in ch.values())
                any_red = any(not v.get("ok") and not v.get("last_ok_date") for v in ch.values())
                domains["smart_money"] = {"channels": ch, "rows": smr["n"] if smr else 0,
                                          "latest_date": smr["d"] if smr else "",
                                          "status": "red" if any_red else ("yellow" if any_stale else "green")}
            except Exception as e:
                domains["smart_money"] = {"status": "red", "err": str(e)}
            try:
                fa, fc = _n("financial_abstract_cache"), _n("fundamentals_cache")
                domains["fundamentals"] = {"abstract": {"hit": fa or 0}, "full": {"hit": fc or 0},
                                            "status": "green" if (fa or fc) else "red"}
            except Exception as e:
                domains["fundamentals"] = {"status": "red", "err": str(e)}
            try:
                rr = _q("SELECT COUNT(*) AS n, MAX(date) AS d FROM research_report")
                domains["research"] = {"rows": rr["n"] if rr else 0, "latest": rr["d"] if rr else "",
                                       "status": _domain_status(rr["n"] if rr else 0, rr["d"] if rr else "")}
            except Exception as e:
                domains["research"] = {"status": "red", "err": str(e)}
            try:
                sl = _q("SELECT COUNT(*) AS n FROM st_list")
                domains["st_list"] = {"rows": sl["n"] if sl else 0,
                                      "status": _domain_status(sl["n"] if sl else 0, ut)}
            except Exception as e:
                domains["st_list"] = {"status": "red", "err": str(e)}
            try:
                pf = _q("SELECT COUNT(*) AS n FROM portfolio")
                domains["portfolio"] = {"positions": pf["n"] if pf else 0, "status": "green"}
            except Exception as e:
                domains["portfolio"] = {"status": "red", "err": str(e)}
    except Exception as e:
        for k in ("spot", "history", "smart_money", "fundamentals",
                  "research", "st_list", "portfolio"):
            domains.setdefault(k, {"status": "red", "err": str(e)})
    order = {"red": 0, "yellow": 1, "green": 2}
    overall = "green"
    for d in domains.values():
        s = d.get("status", "red")
        if order.get(s, 0) < order.get(overall, 2):
            overall = s
    return {"domains": domains, "update_time": db.last_update_time(),
            "last_refresh_time": db.last_update_time(), "overall": overall}


@app.get("/api/health")
def health():
    """数据健康聚合（只读）：各域新鲜度三态+overall。非荐股，默认 disclaimer。"""
    return _wrap(_collect_health())


@app.get("/api/fields")
def fields():
    """返回字段目录 + 运算符，供前端动态渲染条件表单。"""
    return _wrap({
        "board_fields": BOARD_FIELDS_CAT,
        "etf_fields": ETF_FIELDS_CAT,
        "stock_fields": STOCK_FIELDS_CAT,
        "ops": OPS,
        "categories": ["行业", "概念", "个股"],
        "indicators": ["今日", "5日", "10日"],
    })


@app.get("/api/boards")
def boards(category: str = Query("行业"),
           sort: str | None = Query("change_pct"),
           asc: bool = Query(False),
           limit: int = Query(20, ge=1, le=200),
           indicator: str = Query("今日")):
    res = engine.filter_boards(category=category, conditions=[], sort=sort,
                               asc=asc, limit=limit, indicator=indicator)
    return _wrap(res["rows"], {"category": category, "indicator": indicator,
                               "total": res["total"]})


@app.get("/api/etfs")
def etfs(sort: str | None = Query("turnover_amount"),
         asc: bool = Query(False),
         limit: int = Query(30, ge=1, le=200)):
    res = engine.filter_etfs(conditions=[], sort=sort, asc=asc, limit=limit)
    return _wrap(res["rows"], {"total": res["total"]})


@app.get("/api/screen")
def screen(category: str = Query("行业"),
           conditions: str | None = Query(None),  # JSON 字符串
           sort: str | None = Query("main_net_inflow"),
           asc: bool = Query(False),
           limit: int = Query(50, ge=1, le=500),
           indicator: str = Query("今日"),
           min_turnover: float = Query(5e7, ge=0),
           limit_pct: float = Query(9.9, ge=0, le=30)):
    try:
        conds = json.loads(conditions) if conditions else []
    except (json.JSONDecodeError, TypeError):
        return _wrap([], {"category": category, "total": 0,
                          "skipped": [f"conditions 非法 JSON: {conditions!r}"]})
    if not isinstance(conds, list):
        conds = [conds]
    if category in ("ETF", "etf"):
        res = engine.filter_etfs(conditions=conds, sort=sort, asc=asc, limit=limit)
        return _wrap(res["rows"], {"category": "ETF", "total": res["total"],
                                    "skipped": res["skipped"]})
    if category in ("stock", "个股"):
        res = engine.filter_stocks(conditions=conds, sort=sort, asc=asc, limit=limit,
                                   min_turnover=min_turnover, limit_pct=limit_pct)
        return _wrap(res["rows"], {"category": "个股", "total": res["total"],
                                    "skipped": res["skipped"]})
    res = engine.filter_boards(category=category, conditions=conds, sort=sort,
                               asc=asc, limit=limit, indicator=indicator)
    return _wrap(res["rows"], {"category": category, "indicator": indicator,
                               "total": res["total"], "skipped": res["skipped"]})


@app.api_route("/api/refresh", methods=["GET", "POST"])
def refresh():
    """手动触发全量采集刷新。

    兼容 GET(浏览器地址栏直接触发) 与 POST(脚本/curl)，本地原型刷新幂等。
    """
    report = collector.refresh_all()
    return _wrap(report)


# ==================================================================
# 回测研究接口 —— 仅历史研究，不输出买卖点、不承诺收益、不自动下单
# ==================================================================
BT_DISCLAIMER = ("历史回测/因子评价结果不预示未来表现，不构成投资建议，"
                 "不承诺收益，不输出买卖点。")

BACKTEST_FACTORS = ["momentum_n", "volatility_n", "turnover_n", "activity", "momentum",
                    "reversal_5", "reversal_20", "amihud_20"]


class BTFetchReq(BaseModel):
    universe: str  # ETF / stock / board
    codes: list[str]
    start: str = "20200101"
    end: str = "20240101"


class BTEvalReq(BaseModel):
    universe: str
    codes: list[str]
    factor: str = "momentum_n"
    n: int = 20
    n_groups: int = 5
    start: str = "20200101"
    end: str = "20240101"


class BTRunReq(BaseModel):
    universe: str
    codes: list[str]
    factor: str = "momentum_n"
    n: int = 20
    topn: int = 10
    freq: str = "M"  # M/W/Q
    start: str = "20200101"
    end: str = "20240101"
    benchmark: str | None = "sh000300"
    cost_bps: float = 30.0
    delisted_codes: list[str] | None = None


@app.post("/api/backtest/fetch")
def bt_fetch(req: BTFetchReq):
    """抓取历史日线落库(ETF/个股新浪可用，板块 best-effort)。"""
    report = history.fetch_history(req.universe, req.codes, req.start, req.end)
    return _wrap(report, {"bt_disclaimer": BT_DISCLAIMER})


@app.post("/api/backtest/eval")
def bt_eval_route(req: BTEvalReq):
    """因子评价：IC/IR/分档多空。"""
    close = bt_eval.load_panel(req.universe, req.codes, req.start, req.end, "close")
    if close.empty:
        return _wrap({"error": "无历史数据，先 /api/backtest/fetch"},
                     {"bt_disclaimer": BT_DISCLAIMER})
    amount = bt_eval.load_panel(req.universe, req.codes, req.start, req.end, "amount")
    factor = bt_eval.compute_factor(close, req.factor,
                                    params={"n": req.n}, amount=amount)
    fwd = bt_eval.forward_returns(close, req.n)
    ics = bt_eval.ic_series(factor, fwd)
    summary = bt_eval.ic_summary(ics)
    dec = bt_eval.decile_backtest(factor, fwd, req.n_groups)
    boot = bt_robust.bootstrap_ic(ics)
    return _wrap({
        "ic_summary": summary,
        "decile": dec,
        "bootstrap_ic": boot,
        "factor": req.factor, "n": req.n,
        "factors": BACKTEST_FACTORS,
        "survivorship": bt_robust.survivorship_status(),
    }, {"bt_disclaimer": BT_DISCLAIMER})


@app.post("/api/backtest/run")
def bt_run(req: BTRunReq):
    """topN 等权回测 → 净值/风控/换手。"""
    close = bt_eval.load_panel(req.universe, req.codes, req.start, req.end, "close")
    if close.empty:
        return _wrap({"error": "无历史数据，先 /api/backtest/fetch"},
                     {"bt_disclaimer": BT_DISCLAIMER})
    amount = bt_eval.load_panel(req.universe, req.codes, req.start, req.end, "amount")
    factor = bt_eval.compute_factor(close, req.factor,
                                    params={"n": req.n}, amount=amount)
    bench = None
    if req.benchmark:
        bdf, _ok, _ = history.fetch_benchmark_hist(req.benchmark, req.start, req.end)
        if _ok and not bdf.empty and "close" in bdf.columns:
            bench = bdf.set_index("date")["close"].astype(float)
    res = bt_engine.run_backtest(close, factor, topn=req.topn,
                                 freq=req.freq, benchmark=bench,
                                 cost_bps=req.cost_bps,
                                 delisted_codes=req.delisted_codes)
    eq = pd.Series(res.get("equity_curve", {})).astype(float).sort_index()
    bench_nav = pd.Series(res.get("benchmark_curve", {}))
    bench_nav = bench_nav.astype(float).sort_index() if len(bench_nav) else None
    res["risk"] = bt_risk.risk_metrics(eq, bench_nav)
    res["factor"] = req.factor
    res["factors"] = BACKTEST_FACTORS
    res["survivorship"] = bt_robust.survivorship_status()
    return _wrap(res, {"bt_disclaimer": BT_DISCLAIMER})


@app.get("/api/candidates")
def candidates(universe: str = Query("ETF"),
               factor: str = Query("change_pct"),
               codes: str | None = Query(None),  # 逗号分隔，历史因子必填
               n: int = Query(20, ge=1, le=250),
               sort: str = Query("desc"),
               limit: int = Query(20, ge=1, le=500),
               tradable: bool = Query(False),
               min_turnover: float = Query(5e7, ge=0),
               limit_pct: float = Query(9.9, ge=0, le=30),
               multi_fields: str | None = Query(None)):
    """候选池：按因子排序的观察清单(非推荐)。

    spot 因子(ETF/stock: change_pct/turnover_amount/turnover_rate/pe/pb/市值 等)走 spot 快照；
    历史因子(momentum_n 等)走 *_daily，需 codes。tradable=true 预筛可交易性。
    factor=multi_z 时 multi_fields 指定合成字段(逗号分隔)。
    """
    code_list = [c.strip() for c in codes.split(",")] if codes else None
    mf = [f.strip() for f in multi_fields.split(",")] if multi_fields else None
    res = bt_cand.rank_candidates(universe, factor, code_list, n, sort, limit,
                                 tradable, min_turnover, limit_pct,
                                 multi_fields=mf)
    return _wrap(res["rows"], {
        "universe": res.get("universe"), "factor": res.get("factor"),
        "mode": res.get("mode"), "total": len(res["rows"]),
        "tradable": res.get("tradable"), "error": res.get("error"),
        "cand_disclaimer": res.get("disclaimer", ""),
    })


# ==================================================================
# 信号扫描 / 持仓跟踪 / 历史K线 —— 个人本地分析用
# ==================================================================
class BTPortfolioReq(BaseModel):
    code: str
    name: str = ""
    buy_date: str
    buy_price: float
    shares: float
    note: str = ""


class BTSignalReq(BaseModel):
    universe: str = "stock"
    codes: list[str]
    signal_types: list[str] | None = None


@app.post("/api/signals")
def signals(req: BTSignalReq):
    """扫描今日触发买入信号的标的(需先 /api/backtest/fetch 拉历史)。个人本地规则触发。"""
    res = bt_sig.scan_signals(req.universe, req.codes, req.signal_types)
    return _wrap(res["rows"], {
        "n_scanned": res.get("n_scanned"), "error": res.get("error"),
        "signal_types": res.get("signal_types"),
        "cand_disclaimer": "规则机械触发，非AI推荐，不构成投资建议，盈亏自负。",
    })


class BtSignalsReq(BaseModel):
    universe: str
    codes: list[str]
    signal_types: list[str] | None = None
    k_days: int = 5
    benchmark: str | None = "sh000300"


@app.post("/api/signals/backtest")
def bt_signals_route(req: BtSignalsReq):
    """信号历史胜率回测：对每信号扫历史触发点 t→t+k 收益统计。
    合规：历史触发统计事实，非预测。"""
    res = bt_sig.backtest_signals(req.universe, req.codes, req.signal_types,
                                  k_days=req.k_days, benchmark=req.benchmark)
    return _wrap(res.get("rows", res), {
        "n_scanned": res.get("n_scanned"), "error": res.get("error"),
        "k_days": res.get("k_days"), "signals": res.get("signals"),
        "cand_disclaimer": "历史触发统计事实，非预测，不构成投资建议，盈亏自负。",
    })


@app.get("/api/portfolio")
def portfolio_list():
    return _wrap(portfolio.list_positions())


@app.post("/api/portfolio")
def portfolio_add(req: BTPortfolioReq):
    pos = portfolio.add_position(req.code, req.name, req.buy_date,
                                req.buy_price, req.shares, req.note)
    return _wrap(pos)


@app.delete("/api/portfolio/{pid}")
def portfolio_close(pid: int):
    ok = portfolio.close_position(pid)
    return _wrap({"closed": ok, "id": pid})


# ------------------------------------------------------------------
# 主力动向（游资/国家队/外资/资金流）—— 观察清单，非荐股
# ------------------------------------------------------------------
SM_CAND_DISCLAIMER = "主力动向观察清单，机械归类，非荐股非买卖信号，盈亏自负。"


@app.get("/api/smart-money/today")
def sm_today(date: str | None = Query(None),
             channel: str | None = Query(None),
             market: str | None = Query(None),
             days: int = Query(7, ge=1, le=90)):
    res = sm_query.today_list(date, channel, market, days=days)
    return _wrap(res["rows"], {
        "total": res["total"], "date": res.get("date", date),
        "days": days,
        "cand_disclaimer": SM_CAND_DISCLAIMER})


@app.post("/api/smart-money/refresh")
def sm_refresh():
    report = smart_money.refresh_today()
    return _wrap(report, {"cand_disclaimer": SM_CAND_DISCLAIMER})


@app.get("/api/smart-money/channels")
def sm_channels():
    # channel_status() 叠加 DB 实况，防容器重启后内存态全「未采集」误显灰
    return _wrap(smart_money.channel_status())


# ------------------------------------------------------------------
# ST名单 / 高管增减持 / 限售解禁 / 研报评级 / 千股千评（观察清单口径）
# ------------------------------------------------------------------
@app.get("/api/st-list")
def st_list():
    rows = db.query_rows("st_list", order_by="st_type, change_pct DESC")
    return _wrap({"rows": rows}, {"total": len(rows)})


@app.get("/api/management")
def management(days: int = 30):
    """高管增减持(主力动向观察清单口径)。"""
    res = sm_query.top_by_amount(days=days, channel="高管增减持", limit=100)
    return _wrap(res, {"cand_disclaimer": SM_CAND_DISCLAIMER})


@app.get("/api/share-unlock")
def share_unlock(month: str | None = None, code: str | None = None):
    """限售解禁按 as_of 月份查(主力动向观察清单口径)。"""
    res = sm_query.unlock_by_month(month=month, code=code)
    extra = {"cand_disclaimer": SM_CAND_DISCLAIMER}
    if isinstance(res, dict):
        for k in ("month", "total_amount", "total"):
            if k in res:
                extra[k] = res[k]
    return _wrap(res, extra)


@app.get("/api/research")
def research(code: str | None = None, days: int = 30, limit: int = 200):
    """研报评级(机构视角机械汇总,非荐股)。"""
    res = research_data.query_reports(code=code, days=days, limit=limit)
    return _wrap(res, {"cand_disclaimer":
        "机构研报评级机械汇总，非荐股非买卖信号，盈亏自负。"})


@app.get("/api/comments")
def comments(code: str):
    """千股千评(机构视角机械汇总,非荐股)。"""
    res = research_data.fetch_comments(code)
    return _wrap(res, {"cand_disclaimer":
        "千股千评机构视角机械汇总，非荐股非买卖信号，盈亏自负。"})


# ------------------------------------------------------------------
# 优质选股筛选（多口径共振机械排序，非荐股）
# ------------------------------------------------------------------
@app.get("/api/quality")
def quality_screen(universe: str = Query("stock"), days: int = Query(20),
                   min_dims: int = Query(2), min_turnover: float = Query(5e7),
                   max_per_board: int = Query(3), max_corr: float = Query(0.85),
                   limit: int = Query(20), combo_method: str = Query("greedy"),
                   dim_thresh: float = Query(0.6, ge=0.0, le=1.0)):
    from backtest import quality
    res = quality.quality_rank(
        universe=universe, days=days, min_dims=min_dims,
        min_turnover=min_turnover, max_per_board=max_per_board,
        max_corr=max_corr, limit=limit, combo_method=combo_method,
        dim_thresh=dim_thresh)
    return _wrap(res, {"cand_disclaimer": res.get("cand_disclaimer",
                       "多口径共振机械排序观察清单，非荐股非买卖信号，盈亏自负。")})


@app.get("/api/buffett")
def buffett_route(code: str = Query(...)):
    """巴菲特式基本面分析(个股)：护城河(财务质量)+估值+安全边际+研究优先级标签。
    ETF 不适用。不给买卖指令(方法论要求)，输出研究优先级，买卖由用户定。"""
    res = bt_buf.analyze(code)
    return _wrap(res, {"bt_disclaimer": "基于公开财务摘要的机械评分，护城河来源需读年报人工判断；研究优先级非买卖信号，盈亏自负。"})


@app.get("/api/buffett/top")
def buffett_top(n: int = Query(10, ge=1, le=50),
                order: str = Query("valuation_desc"),  # valuation_desc/valuation_asc/priority
                min_turnover: float = Query(1e9, ge=0),
                shortlist_k: int = Query(40, ge=5, le=80)):
    """巴菲特分析 Top-N：可买入(可交易) shortlist → 逐个财务分析 → 按估值/优先级排序取 Top。
    较慢(每个标的拉一次财务摘要)。order=valuation_desc=当前估值高(贵)+可买入 Top10。"""
    codes = bt_buf.shortlist_by_turnover(min_turnover, shortlist_k)
    if not codes:
        return _wrap({"error": "无可买入 shortlist，先 /api/refresh 拉个股 spot"},
                      {"bt_disclaimer": bt_buf.__doc__ or ""})
    results = bt_buf.analyze_many(codes)
    top = bt_buf.rank_top(results, order, n, min_turnover, shortlist_k)
    return _wrap(top, {
        "n": n, "order": order, "scanned": len(results), "shortlist_k": shortlist_k,
        "bt_disclaimer": "可买入=排除ST/涨停/停牌+成交额达标；估值标签基于财务摘要机械评分，非买卖指令，盈亏自负。",
    })


@app.get("/api/history")
def history_route(code: str = Query(...), universe: str = Query("stock"),
                 start: str = Query("20230101"), end: str = Query("20240601")):
    """返回单个标的的历史日线(供前端画K线)。个人本地分析用。"""
    table, key = history._UNIVERSE[universe][0], history._UNIVERSE[universe][2]
    rows = db.query_rows(table)
    if not rows:
        return _wrap({"error": "无历史数据，先 /api/backtest/fetch"}, {"code": code})
    df = pd.DataFrame(rows)
    df = df[df[key] == code]
    dts = pd.to_datetime(df["date"], errors="coerce")
    df = df[(dts >= pd.to_datetime(start)) & (dts <= pd.to_datetime(end))]
    df = df.sort_values("date")
    return _wrap({
        "code": code, "dates": df["date"].tolist(),
        "ohlc": df[["open", "high", "low", "close"]].astype(float).round(4).to_dict("records")
                if not df.empty else [],
        "volume": df["volume"].astype(float).round(0).tolist() if "volume" in df and not df.empty else [],
    }, {"count": len(df)})


@app.post("/api/backtest/walkforward")
def bt_walkforward(req: BTEvalReq):
    """walk-forward：训练段 vs 测试段 IC 衰减 + 过拟合告警。"""
    close = bt_eval.load_panel(req.universe, req.codes, req.start, req.end, "close")
    if close.empty:
        return _wrap({"error": "无历史数据，先 /api/backtest/fetch"},
                     {"bt_disclaimer": BT_DISCLAIMER})
    amount = bt_eval.load_panel(req.universe, req.codes, req.start, req.end, "amount")
    factor = bt_eval.compute_factor(close, req.factor,
                                    params={"n": req.n}, amount=amount)
    wf = bt_robust.rolling_walk_forward(factor, close, n=req.n)
    return _wrap(wf, {"bt_disclaimer": BT_DISCLAIMER})


@app.get("/")
def root():
    return {"msg": "A股板块/ETF 筛选器(本地) 已启动", "docs": "/docs",
            "web": "/web/index.html", "disclaimer": DISCLAIMER}


# 托管前端静态页(同源，免 CORS)；访问 http://host:8000/web/index.html
_WEB_DIR = Path(__file__).resolve().parent.parent / "web"
if _WEB_DIR.is_dir():
    app.mount("/web", StaticFiles(directory=str(_WEB_DIR)), name="web")
