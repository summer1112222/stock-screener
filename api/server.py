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
                # stock_daily 列名是 symbol(非 code),board_daily 是 name;etf_daily 是 code
                h = _q("SELECT COUNT(*) AS n, MIN(date) AS mn, MAX(date) AS mx, "
                       "COUNT(DISTINCT symbol) AS cc FROM stock_daily")
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


@app.get("/api/smart-money/seats")
def sm_seats(period: str = Query("近一月")):
    """游资营业部龙虎榜统计(席位级,按需 on-demand)：上榜次数/买入额/卖出额/净额。
    机械汇总,非荐股非买卖信号。period ∈ 近一月/近三月/近六月/近一年。"""
    res = smart_money.collect_seats(period)
    return _wrap(res.get("rows", []), {
        "total": res.get("total", 0), "period": period,
        "error": res.get("error"),
        "cand_disclaimer": SM_CAND_DISCLAIMER})


@app.get("/api/smart-money/seats-stocks")
def sm_seats_stocks(date: str | None = Query(None)):
    """游资追逐个股(席位明细,按需 on-demand):对该日龙虎榜个股逐个取买入席位。
    慢(N股×1调用),仅个股(ETF不上龙虎榜)。机械汇总,非荐股。"""
    res = smart_money.collect_seats_stocks(date)
    return _wrap(res.get("rows", []), {
        "total": res.get("total", 0), "date": res.get("date"),
        "error": res.get("error"),
        "cand_disclaimer": SM_CAND_DISCLAIMER})


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


@app.get("/api/stock-analysis")
def stock_analysis(code: str = Query(...)):
    """个股深度分析卡：聚合 基本面(buffett)+主力动向+研报+千股千评+技术信号+风险预筛。
    多维机械分析,研究优先级非买卖信号,盈亏自负。"""
    from collections import defaultdict
    card = {"code": code}
    # 1. 基本面+估值+护城河+风险+优先级(buffett,复用财报缓存)
    try:
        ba = bt_buf.analyze(code)
        card["name"] = ba.get("name")
        card["fundamentals"] = ba
        # 综合评分(0-100 机械): ROE/毛利率/负债/FCF/护城河/估值 - 红旗。研究优先级非买卖信号。
        rt = ba.get("ratios") or {}
        def _num(x):
            try:
                return float(x)
            except (TypeError, ValueError):
                return None
        score = 0.0
        roe = _num(rt.get("leverage_adj_roe"))
        score += 25 if roe and roe > 20 else (20 if roe and roe > 15 else (15 if roe and roe > 10 else 5))
        gm = _num(rt.get("gross_margin_avg"))
        score += 15 if gm and gm > 50 else (10 if gm and gm > 30 else 5)
        dr = _num(rt.get("debt_ratio_latest"))
        score += 15 if dr is not None and dr < 40 else (10 if dr is not None and dr < 60 else 5)
        fc = _num(rt.get("fcf_to_netincome"))
        score += 15 if fc and fc > 0.7 else (10 if fc and fc > 0.3 else 5)
        moat = _num(ba.get("moat_score"))
        score += 15 if moat and moat >= 4 else (10 if moat and moat >= 3 else 5)
        vt = (ba.get("valuation_tag") or ba.get("valuation") or "")
        score += 15 if any(w in str(vt) for w in ("便宜", "低估", "低估")) else (10 if "合理" in str(vt) else 5)
        score -= 10 * len(ba.get("red_flags") or [])
        card["score"] = max(0, min(100, round(score, 1)))
    except Exception as e:
        card["fundamentals"] = {"error": str(e)}
    # 2. 主力动向(该股近30日各通道净额)
    try:
        rows = db.query_rows("smart_money_action", where="code = ?",
                             params=(code,), order_by="date DESC", limit=200)
        ch = defaultdict(lambda: [0.0, 0])
        daily = defaultdict(float)
        for r in rows:
            c = r.get("channel"); a = r.get("amount"); dd = r.get("date")
            if c and a is not None:
                ch[c][0] += a; ch[c][1] += 1
            if dd and a is not None:
                daily[dd] += a or 0
        card["smart_money"] = {
            "channels": [{"channel": k, "net": v[0], "count": v[1]} for k, v in ch.items()],
            "daily": [{"date": d, "net": daily[d]} for d in sorted(daily)],
            "latest_date": rows[0].get("date") if rows else None,
            "total_rows": len(rows)}
    except Exception as e:
        card["smart_money"] = {"error": str(e)}
    # 3. 研报评级
    try:
        rr = research_data.query_reports(code=code, days=180, limit=10)
        card["research"] = {"reports": rr.get("rows", []), "total": rr.get("total", 0)}
    except Exception as e:
        card["research"] = {"error": str(e)}
    # 4. 千股千评
    try:
        cm = research_data.fetch_comments(code)
        card["comments"] = cm[0] if isinstance(cm, tuple) else cm
    except Exception as e:
        card["comments"] = {"error": str(e)}
    # 5. 技术信号(需该 code 历史)
    try:
        sc = bt_sig.scan_signals("stock", [code])
        card["signals"] = sc.get("rows", [])
        card["signals_error"] = sc.get("error")
    except Exception as e:
        card["signals"] = {"error": str(e)}
    # 6. 多因子机械预判(偏多/偏空/中性):聚合 基本面评分/资金净额/技术信号/机构评级/估值
    #    非买卖建议,仅多因子同向机械归类。bullish/bearish 计数定标签。
    bull, bear, reasons = [], [], []
    # 基本面评分
    s = card.get("score")
    if s is not None:
        (bull if s >= 60 else bear if s < 40 else []).append(f"综合评分 {s}/100")
        if 40 <= s < 60: reasons.append({"factor": "基本面评分", "dir": "中性", "detail": f"{s}/100"})
        else: reasons.append({"factor": "基本面评分", "dir": "偏多" if s >= 60 else "偏空", "detail": f"{s}/100"})
    # 估值
    vt = str((card.get("fundamentals") or {}).get("valuation_tag") or
             (card.get("fundamentals") or {}).get("valuation") or "")
    if any(w in vt for w in ("便宜", "低估")):
        bull.append(f"估值{vt}"); reasons.append({"factor": "估值", "dir": "偏多", "detail": vt})
    elif any(w in vt for w in ("贵", "高估")):
        bear.append(f"估值{vt}"); reasons.append({"factor": "估值", "dir": "偏空", "detail": vt})
    # 资金面(各通道净额合计)
    try:
        sm_net = sum(c.get("net") or 0 for c in (card.get("smart_money") or {}).get("channels", []))
        if sm_net > 0:
            bull.append(f"主力净流入 {sm_net:.0f}"); reasons.append({"factor": "资金面", "dir": "偏多", "detail": f"净流入{sm_net:.0f}"})
        elif sm_net < 0:
            bear.append(f"主力净流出 {sm_net:.0f}"); reasons.append({"factor": "资金面", "dir": "偏空", "detail": f"净流出{sm_net:.0f}"})
        else:
            reasons.append({"factor": "资金面", "dir": "中性", "detail": "无显著净流"})
    except Exception:
        pass
    # 技术信号(触发即偏多:金叉/突破/放量/超卖反转)
    try:
        sig_rows = card.get("signals") if isinstance(card.get("signals"), list) else []
        trig = []
        for sr in sig_rows:
            for s in (sr.get("signals") or []):
                t = s.get("type") if isinstance(s, dict) else str(s)
                if t: trig.append(t)
        if trig:
            bull.append(f"技术信号 {','.join(trig)}"); reasons.append({"factor": "技术面", "dir": "偏多", "detail": ",".join(trig)})
        else:
            reasons.append({"factor": "技术面", "dir": "中性", "detail": "无触发"})
    except Exception:
        pass
    # 机构评级(研报)
    try:
        reps = (card.get("research") or {}).get("reports", [])
        ratings = [str(r.get("rating") or r.get("评级") or "") for r in reps]
        buy_kw = [x for x in ratings if any(w in x for w in ("买入", "增持", "推荐", "强烈"))]
        sell_kw = [x for x in ratings if any(w in x for w in ("减持", "卖出", "回避"))]
        if buy_kw:
            bull.append(f"研报{len(buy_kw)}条看多"); reasons.append({"factor": "机构评级", "dir": "偏多", "detail": ",".join(set(buy_kw))})
        elif sell_kw:
            bear.append(f"研报{len(sell_kw)}条看空"); reasons.append({"factor": "机构评级", "dir": "偏空", "detail": ",".join(set(sell_kw))})
        else:
            reasons.append({"factor": "机构评级", "dir": "中性", "detail": f"{len(reps)}条" if reps else "无"})
    except Exception:
        pass
    label = "偏多" if len(bull) > len(bear) else ("偏空" if len(bear) > len(bull) else "中性")
    card["outlook"] = {"label": label, "bullish": bull, "bearish": bear,
                        "reasons": reasons,
                        "note": "多因子机械信号聚合预判,非买卖建议,不荐股不承诺收益,盈亏自负"}
    return _wrap(card, {"bt_disclaimer": "基于公开数据的多维机械分析+多因子预判,研究优先级非买卖信号,盈亏自负。"})


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
