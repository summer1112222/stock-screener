# -*- coding: utf-8 -*-
"""主力动向采集层：龙虎榜席位 / 十大流通股东 / 陆股通 / 个股资金流。

合规：本层只采集公开龙虎榜/股东/资金流数据，归类为"主力动向观察清单"，
      不荐股、不输出买卖点、不承诺收益。游资席位名/国家队持仓为公开事实陈述。
稳定性：每个 collector 返回 (records, ok, err)，网络/接口异常不抛崩。
NaN→None：出口统一经 _clean/_to_float 处理，确保 float NaN→None。
"""
from __future__ import annotations

import json
from datetime import datetime

import pandas as pd

try:
    import akshare as ak
    _AK_OK = True
    _AK_ERR = ""
except Exception as e:  # pragma: no cover
    ak = None  # type: ignore
    _AK_OK = False
    _AK_ERR = f"akshare 未安装或导入失败: {e}"

from . import db, collector  # noqa: F401  (import collector 触发 _install_http_patch 全局 UA 保护)

# 国家队关键字（查询层 by_actor("国家队") 展开为 LIKE 多名匹配）
NATIONAL_TEAM = ["中国证券金融", "中央汇金", "全国社保基金",
                 "中证金融", "梧桐树", "国家外汇管理局"]

# 各通道最近一次状态，前端据此灰掉不可用通道。
# 注意：这是内存态，容器重启即归零「未采集」；channel_status() 会叠加 DB 实况
# （最新日期有行即标 ok），避免重启后明明有数据却全显灰。
CHANNEL_STATUS = {
    "龙虎榜": {"ok": False, "source": "", "err": "未采集", "at": ""},
    "十大股东": {"ok": False, "source": "", "err": "未采集", "at": ""},
    "北向": {"ok": False, "source": "", "err": "未采集", "at": ""},
    "资金流": {"ok": False, "source": "", "err": "未采集", "at": ""},
    "高管增减持": {"ok": False, "source": "", "err": "未采集", "at": ""},
    "限售解禁": {"ok": False, "source": "", "err": "未采集", "at": ""},
}


def channel_status() -> dict:
    """返回各通道状态，叠加 DB 实况：内存 CHANNEL_STATUS 给 source/err/at 细节，
    DB 查最新日期各通道行数——有行即 ok=True（source 标 "DB(日期)"），
    防容器重启后内存态全归零、明明有数据却全显「未采集」灰点。只读，不写表。"""
    status = {k: dict(v) for k, v in CHANNEL_STATUS.items()}
    try:
        from . import db as _db
        _db.init_db()
        with _db.get_conn() as conn:
            row = conn.execute(
                "SELECT MAX(date) AS d FROM smart_money_action"
            ).fetchone()
            latest = row["d"] if row else None
            if latest:
                cur = conn.execute(
                    "SELECT channel, COUNT(*) AS n FROM smart_money_action "
                    "WHERE date=? GROUP BY channel", (latest,))
                counts = {r["channel"]: r["n"] for r in cur.fetchall()}
        for ch, st in status.items():
            n = counts.get(ch, 0) if latest else 0
            st["rows"] = n
            st["date"] = latest or ""
            if n > 0:
                # DB 有数据：强制 ok，保留内存态 source 细节(若有)，清错
                st["ok"] = True
                if not st.get("source"):
                    st["source"] = f"DB({latest})"
                st["err"] = ""
    except Exception:
        # DB 查询失败不阻塞，回落到内存态
        for st in status.values():
            st.setdefault("rows", 0)
            st.setdefault("date", "")
    return status


def _now_ts() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _set_status(channel: str, ok: bool, source: str, err: str) -> None:
    CHANNEL_STATUS[channel] = {"ok": ok, "source": source,
                               "err": err, "at": _now_ts()}


def _friendly_err(channel: str, e: Exception) -> str:
    """把 akshare 内部 traceback 归一为友好文案，不裸露 NoneType/下标等。"""
    s = str(e)
    low = s.lower()
    if "nonetype" in low or "subscriptable" in low or "none" in low:
        return f"{channel}: 接口不可用(东财返回异常/反爬)"
    if "remotedisconnected" in low or "connection aborted" in low or "502" in low \
            or "503" in low or "504" in low:
        return f"{channel}: 接口不可用(东财被封/断连)"
    return f"{channel}: {s}"


def _clean(v):
    """标量 NaN→None，便于 json 化。"""
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    return v


def _to_float(v):
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if pd.isna(f) else f


def _first_col(df: pd.DataFrame, candidates: list[str]) -> str:
    """从候选列名取第一个命中的实际列名；取不到回候选首项（r.get 自然得 None）。"""
    cols = set(df.columns)
    for c in candidates:
        if c in cols:
            return c
    return candidates[0]


def _rec(date, code, name, market, channel, actor, action,
         amount, rank=None, as_of=None, raw=None) -> dict:
    """构造一条 smart_money_action 记录（amount NaN→None）。"""
    a = None if amount is None else _to_float(amount)
    return {
        "date": date, "code": None if code is None else str(code),
        "name": name, "market": market, "channel": channel,
        "actor": actor, "action": action, "amount": a, "rank": rank,
        "as_of": as_of,
        "raw": json.dumps(raw, ensure_ascii=False) if raw else None,
        "ts": _now_ts(),
    }


# ------------------------------------------------------------------
# 资金流通道（无 actor，最简）
# ------------------------------------------------------------------
def collect_fund_flow(date: str) -> tuple[list[dict], bool, str]:
    """资金流通道：复用 stock_spot.main_net_inflow（/api/refresh 已采集的本地快照），
    不调东财个股资金流接口 stock_individual_fund_flow_rank（出口 IP 被封）。
    口径=当日大单主力净流入额，actor="" 保证 UNIQUE 去重。
    spot 无 main_net_inflow 字段或全 None 时标不可用，不写 null 废行（防 ok 误报、
    防前端 11059 行 '—' 看似有数据实则空）。"""
    spots = db.query_rows("stock_spot", limit=0)
    if not spots:
        _set_status("资金流", False, "", "无 stock_spot，先 /api/refresh")
        return [], False, "资金流: 无 stock_spot，先 /api/refresh"
    recs = []
    for sp in spots:
        amt = _to_float(sp.get("main_net_inflow"))
        if amt is None:
            continue   # 无净额数据跳过，不写 amount=null 废行
        recs.append(_rec(date, sp.get("code"), sp.get("name"), "股票",
                        "资金流", "", "净买入", amt,
                        raw={k: _clean(v) for k, v in sp.items()
                             if k in ("code", "name", "main_net_inflow",
                                      "change_pct", "turnover_amount")}))
    if not recs:
        _set_status("资金流", False, "",
                    "spot 无 main_net_inflow(东财个股资金流被封/字段缺失)")
        return [], False, "资金流: spot 无 main_net_inflow(东财个股资金流被封，本通道暂不可用)"
    _set_status("资金流", True, "spot快照", "")
    return recs, True, ""


# ------------------------------------------------------------------
# 北向（陆股通）
# ------------------------------------------------------------------
def collect_northbound(date: str) -> tuple[list[dict], bool, str]:
    if not _AK_OK:
        return [], False, _AK_ERR
    df = None
    src = ""
    try:
        df = ak.stock_hsgt_individual_em(stock="北向资金")
        src = "东财"
    except Exception:
        pass
    if df is None:
        try:
            df = ak.stock_hsgt_hold_stock_em(market="北向", indicator="今日排行")
            src = "东财"
        except Exception as e:
            _set_status("北向", False, "", _friendly_err("北向", e))
            return [], False, _friendly_err("北向", e)
    if df is None or df.empty:
        _set_status("北向", False, "", "空结果")
        return [], False, "北向: 空结果"
    col_code = _first_col(df, ["股票代码", "代码", "code"])
    col_name = _first_col(df, ["股票简称", "名称", "name"])
    col_amt = _first_col(df, ["持股数量变化", "增持市值", "净买额", "今日增持市值"])
    recs = []
    for _, r in df.iterrows():
        # actor="" 而非 None：同 collect_fund_flow，空串使 UNIQUE 去重生效，防重刷重复。
        recs.append(_rec(date, r.get(col_code), r.get(col_name), "股票",
                        "北向", "", "净买入", r.get(col_amt),
                        raw={k: _clean(v) for k, v in r.items()}))
    _set_status("北向", True, src, "")
    return recs, True, ""


# ------------------------------------------------------------------
# 龙虎榜（逐股×每席位）
# ------------------------------------------------------------------
def collect_dragon_tiger(date: str) -> tuple[list[dict], bool, str]:
    if not _AK_OK:
        return [], False, _AK_ERR
    try:
        stocks = ak.stock_lhb_detail_em(start_date=date, end_date=date)
    except Exception as e:
        _set_status("龙虎榜", False, "", _friendly_err("龙虎榜", e))
        return [], False, _friendly_err("龙虎榜", e)
    if stocks is None or stocks.empty:
        _set_status("龙虎榜", True, "", "当日无上榜票")
        return [], True, ""   # 当日无上榜票不算错
    col_code = _first_col(stocks, ["代码", "code"])
    col_name = _first_col(stocks, ["名称", "name"])
    recs = []
    partial_err = ""
    for _, s in stocks.iterrows():
        code, name = s.get(col_code), s.get(col_name)
        try:
            det = ak.stock_lhb_stock_detail_em(symbol=str(code))
        except Exception as e:
            partial_err = _friendly_err(f"龙虎榜(席位 {code})", e)
            continue
        if det is None or det.empty:
            continue
        col_seat = _first_col(det, ["席位名称", "营业部名称", "席位"])
        col_buy = _first_col(det, ["买入额", "买入金额"])
        col_sell = _first_col(det, ["卖出额", "卖出金额"])
        for _, r in det.iterrows():
            buy = _to_float(r.get(col_buy))
            sell = _to_float(r.get(col_sell))
            amt = None if (buy is None or sell is None) else (buy - sell)
            recs.append(_rec(date, code, name, "股票", "龙虎榜",
                            r.get(col_seat), "上榜", amt,
                            raw={k: _clean(v) for k, v in r.items()}))
    if not recs and partial_err:
        _set_status("龙虎榜", False, "", partial_err)
        return [], False, partial_err
    _set_status("龙虎榜", True, "东财", partial_err)
    return recs, True, ("(部分席位失败) " + partial_err if partial_err else "")


# ------------------------------------------------------------------
# 十大流通股东（季频，国家队关键字命中靠查询层 LIKE）
# ------------------------------------------------------------------
def collect_holders(date: str) -> tuple[list[dict], bool, str]:
    """十大流通股东（季频，国家队关键字命中靠查询层 LIKE）。
    缩范围：不全市场逐股拉 2000 次（东财必被封），改取 high-shortlist
    （成交额前 200，覆盖国家队重仓大盘股概率高），减请求量降被封概率。
    修 ok 误报：tried 计数区分'真拉到空'（ok=True）vs'全失败'（ok=False）。"""
    if not _AK_OK:
        return [], False, _AK_ERR
    last = db.get_meta("holders_last_as_of", "")
    if last:
        try:
            last_d = datetime.strptime(last, "%Y-%m-%d")
            if (datetime.now() - last_d).days < 60:
                _set_status("十大股东", True, "", "未到披露窗口，跳过")
                return [], True, "(未到季报披露窗口，跳过)"
        except Exception:
            pass
    spots = db.query_rows("stock_spot", limit=0)
    if not spots:
        _set_status("十大股东", False, "", "无 stock_spot")
        return [], False, "十大股东: 无 stock_spot，先 /api/refresh 拉个股 spot"
    # 缩范围：按成交额降序取前 200 只（高流动性 + 国家队重仓股大概率在内）
    spot_df = pd.DataFrame(spots)
    if "turnover_amount" in spot_df.columns:
        spot_df["turnover_amount"] = pd.to_numeric(spot_df["turnover_amount"],
                                                   errors="coerce").fillna(0)
        spot_df = spot_df.sort_values("turnover_amount", ascending=False).head(200)
    candidates = spot_df.to_dict("records")
    as_of = date
    recs = []
    tried = 0
    for sp in candidates:
        code = sp.get("code")
        tried += 1
        try:
            df = ak.stock_gdfx_free_top_10(symbol=str(code))
        except Exception:
            continue
        if df is None or df.empty:
            continue
        col_holder = _first_col(df, ["股东名称", "股东"])
        for _, r in df.iterrows():
            holder = r.get(col_holder)
            if holder is None:
                continue
            recs.append(_rec(date, code, sp.get("name"), "股票", "十大股东",
                            holder, "持仓", None, as_of=as_of,
                            raw={k: _clean(v) for k, v in r.items()}))
    # 区分真空 vs 全失败：tried>0 但 recs 空=全失败标不可用；tried=0=无候选
    if not recs:
        if tried > 0:
            _set_status("十大股东", False, "",
                        f"试{tried}股全失败(东财被封/接口异常)")
            return [], False, f"十大股东: 试{tried}股全失败(东财被封/接口异常)"
        _set_status("十大股东", True, "", "无候选股")
        return [], True, "十大股东: 无候选股"
    db.set_meta("holders_last_as_of", as_of)
    _set_status("十大股东", True, "东财", "")
    return recs, True, ""


# ------------------------------------------------------------------
# 高管增减持(按日期全市场)
# ------------------------------------------------------------------
def collect_management_hold(date: str) -> tuple[list[dict], bool, str]:
    """高管增减持(东财 stock_hold_management_em,按日期全市场)。
    actor=高管名, action=增持/减持, amount=变动金额, raw 存明细。"""
    if not _AK_OK:
        return [], False, _AK_ERR
    try:
        df = ak.stock_hold_management_em()
    except Exception as e:
        _set_status("高管增减持", False, "", _friendly_err("高管增减持", e))
        return [], False, _friendly_err("高管增减持", e)
    if df is None or df.empty:
        _set_status("高管增减持", True, "", "当日无增减持")
        return [], True, ""
    col_code = _first_col(df, ["代码", "股票代码", "code"])
    col_name = _first_col(df, ["名称", "股票简称", "name"])
    col_actor = _first_col(df, ["变动人", "高管名称", "姓名"])
    col_action = _first_col(df, ["变动方向", "增减"])
    col_amt = _first_col(df, ["变动金额", "成交金额", "变动数额"])
    recs = []
    for _, r in df.iterrows():
        act = str(r.get(col_action) or "")
        action = "增持" if "增持" in act else "减持" if "减持" in act else act
        recs.append(_rec(date, r.get(col_code), r.get(col_name), "股票",
                        "高管增减持", r.get(col_actor), action,
                        r.get(col_amt),
                        raw={k: _clean(v) for k, v in r.items()}))
    _set_status("高管增减持", True, "东财", "")
    return recs, True, ""


# ------------------------------------------------------------------
# 限售解禁(按月份)
# ------------------------------------------------------------------
def collect_share_unlock(date: str) -> tuple[list[dict], bool, str]:
    """限售解禁(东财 stock_share_change_em,按月份)。date 取所在月,
    拉当月解禁清单;actor=股东, action=解禁, amount=解禁数量, as_of=解禁日期。"""
    if not _AK_OK:
        return [], False, _AK_ERR
    month = date[:7]
    try:
        df = ak.stock_share_change_em(symbol=month)
    except Exception as e:
        _set_status("限售解禁", False, "", _friendly_err("限售解禁", e))
        return [], False, _friendly_err("限售解禁", e)
    if df is None or df.empty:
        _set_status("限售解禁", True, "", f"{month} 无解禁")
        return [], True, ""
    col_code = _first_col(df, ["代码", "股票代码", "code"])
    col_name = _first_col(df, ["名称", "股票简称", "name"])
    col_actor = _first_col(df, ["解禁股东", "股东名称"])
    col_amt = _first_col(df, ["解禁数量", "解禁股数", "实际解禁数量"])
    col_date = _first_col(df, ["解禁日期", "解禁时间", "公告日期"])
    recs = []
    for _, r in df.iterrows():
        recs.append(_rec(date, r.get(col_code), r.get(col_name), "股票",
                        "限售解禁", r.get(col_actor), "解禁",
                        r.get(col_amt), as_of=str(r.get(col_date) or ""),
                        raw={k: _clean(v) for k, v in r.items()}))
    _set_status("限售解禁", True, "东财", "")
    return recs, True, ""


# ------------------------------------------------------------------
# 编排
# ------------------------------------------------------------------
def refresh_today(date: str | None = None) -> dict:
    """串行跑 4 通道 → upsert smart_money_action → 写 meta + CHANNEL_STATUS。
    单通道崩不影响其他通道。"""
    db.init_db()
    if not date:
        date = datetime.now().strftime("%Y-%m-%d")
    report = {"date": date, "counts": {}, "channels": {}}
    plan = [("资金流", collect_fund_flow), ("北向", collect_northbound),
            ("龙虎榜", collect_dragon_tiger), ("十大股东", collect_holders),
            ("高管增减持", collect_management_hold),
            ("限售解禁", collect_share_unlock)]
    for ch, fn in plan:
        try:
            recs, ok, err = fn(date)
        except Exception as e:   # 双保险：collect 内部已 try，这里再兜
            recs, ok, err = [], False, f"{ch}: 未捕获异常 {e}"
            _set_status(ch, False, "", str(e))
        n = db.upsert_rows("smart_money_action", recs) if (ok and recs) else 0
        st = CHANNEL_STATUS.get(ch, {})
        report["counts"][ch] = n
        report["channels"][ch] = {"ok": ok, "rows": n, "err": err, "at": st.get("at", "")}
    report["update_time"] = db.stamp_update_time()
    return report
