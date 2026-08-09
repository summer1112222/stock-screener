# -*- coding: utf-8 -*-
"""主力动向采集层：龙虎榜席位 / 十大流通股东 / 陆股通 / 个股资金流。

合规：本层只采集公开龙虎榜/股东/资金流数据，归类为"主力动向观察清单"，
      不荐股、不输出买卖点、不承诺收益。游资席位名/国家队持仓为公开事实陈述。
稳定性：每个 collector 返回 (records, ok, err)，网络/接口异常不抛崩。
NaN→None：出口统一经 _clean/_to_float 处理，确保 float NaN→None。
"""
from __future__ import annotations

import json
from datetime import datetime, date as _date

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

# 国家队历史重仓股种子（硬编兜底，覆盖小盘/低成交额国家队重仓股——成交额 shortlist 会漏）。
# 学习式扩充：成功拉取后命中 NATIONAL_TEAM 的 code 并入 meta nt_holdings_seed。
# dict{code:name}：stock_spot 常不含这些大盘股，硬编名字保证十大股东行能显示股票名。
NATIONAL_TEAM_HOLDINGS_SEED = {
    "600519": "贵州茅台", "601318": "中国平安", "600036": "招商银行",
    "601398": "工商银行", "601288": "农业银行", "601628": "中国人寿",
    "600028": "中国石化", "601857": "中国石油", "600030": "中信证券",
    "601166": "兴业银行", "600276": "恒瑞医药", "000858": "五粮液",
    "600000": "浦发银行", "601988": "中国银行", "601328": "交通银行",
    "600436": "片仔癀", "600009": "上海机场", "601088": "中国神华",
}

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
    """各通道状态，叠加 DB 实况 + stale meta：内存 CHANNEL_STATUS 给 source/err/at/stale
    细节；DB 给 rows/date；meta(sm_stale_<ch>)给 stale_date/last_ok_date。只读。"""
    status = {k: dict(v) for k, v in CHANNEL_STATUS.items()}
    try:
        from . import db as _db
        _db.init_db()
        with _db.get_conn() as conn:
            row = conn.execute(
                "SELECT MAX(date) AS d FROM smart_money_action").fetchone()
            latest = row["d"] if row else None
            counts = {}
            if latest:
                cur = conn.execute(
                    "SELECT channel, COUNT(*) AS n FROM smart_money_action "
                    "WHERE date=? GROUP BY channel", (latest,))
                counts = {r["channel"]: r["n"] for r in cur.fetchall()}
        for ch, st in status.items():
            meta = _db.get_meta(f"sm_stale_{ch}", "")
            if meta and "|" in meta:
                sd, _err = meta.split("|", 1)
                st.setdefault("stale_date", sd)
                st.setdefault("stale", True)
                st.setdefault("stale_note", f"采集失败: {_err}")
            st.setdefault("stale", False)
            st.setdefault("last_ok_date", _last_ok_date(ch))
            n = counts.get(ch, 0) if latest else 0
            st["rows"] = n
            st["date"] = latest or ""
            if n > 0 and not st.get("stale"):
                st["ok"] = True
                if not st.get("source"):
                    st["source"] = f"DB({latest})"
                st["err"] = ""
    except Exception:
        for st in status.values():
            st.setdefault("rows", 0)
            st.setdefault("date", "")
            st.setdefault("stale", False)
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
    """标量 NaN→None + date/datetime/Timestamp→字符串，便于 json 化。"""
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(v, datetime):
        return v.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(v, _date):
        return v.strftime("%Y-%m-%d")
    if isinstance(v, pd.Timestamp):
        return None if pd.isna(v) else v.strftime("%Y-%m-%d %H:%M:%S")
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


def _norm_date(v) -> str | None:
    """把 akshare 返回的日期/str/datetime 统一成 'YYYY-MM-DD'；解析失败回 None。"""
    if v is None:
        return None
    try:
        ts = pd.to_datetime(v, errors="coerce")
    except Exception:
        return None
    if pd.isna(ts):
        return None
    return ts.strftime("%Y-%m-%d")


def _filter_recent(df: pd.DataFrame, col: str, days: int) -> pd.DataFrame:
    """保留 col 列在近 days 天内的行；col 不在表里或全 NaT 时原样返回（测试/旧 schema 兜底）。"""
    if df is None or df.empty or col not in df.columns:
        return df
    ts = pd.to_datetime(df[col], errors="coerce")
    cutoff = pd.to_datetime(datetime.now()) - pd.Timedelta(days=days)
    mask = ts >= cutoff
    return df[mask.fillna(False)]


def _prefix_code(code) -> str:
    """6/9 开头→sh，其余→sz（akshare 股东接口要带交易所前缀）。"""
    c = str(code).strip()
    return ("sh" if c.startswith(("6", "9")) else "sz") + c


def _load_seed() -> set[str]:
    """加载国家队种子：硬编常量 ∪ meta nt_holdings_seed（学习扩充）。"""
    codes = set(NATIONAL_TEAM_HOLDINGS_SEED)
    try:
        meta = db.get_meta("nt_holdings_seed", "")
        if meta:
            codes |= {c for c in meta.split(",") if c}
    except Exception:
        pass
    return codes


def _save_seed(codes: set[str]) -> None:
    """把新命中国家队重仓的 code 并入种子 meta。"""
    try:
        all_codes = _load_seed() | codes
        db.set_meta("nt_holdings_seed", ",".join(sorted(all_codes)))
    except Exception:
        pass


def _latest_report_period() -> str:
    """最近一个已披露的报告期 YYYYMMDD（季报披露窗口：Q1→4/30、中报→8/31、
    三季报→10/31、年报→次年 4/30）。"""
    today = datetime.now()
    y, m = today.year, today.month
    if m >= 11:
        return f"{y}0930"
    if m >= 9:
        return f"{y}0630"
    if m >= 5:
        return f"{y}0331"
    return f"{y - 1}1231"


def _month_end(month: str) -> str:
    """'YYYY-MM' → 该月最后一天 'YYYY-MM-DD'。"""
    import calendar
    y, mm = int(month[:4]), int(month[5:7])
    return f"{month}-{calendar.monthrange(y, mm)[1]:02d}"


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
def _parse_cn_amount(v) -> float | None:
    """解析同花顺中文金额字符串 '822.74万'/'1.63亿'/'-3172.81万'/'5300.0' → float。"""
    if v is None:
        return None
    s = str(v).strip().replace(",", "")
    if not s or s in ("--", "---", "—"):
        return None
    neg = s.startswith("-")
    if neg:
        s = s[1:]
    try:
        if s.endswith("亿"):
            f = float(s[:-1]) * 1e8
        elif s.endswith("万"):
            f = float(s[:-1]) * 1e4
        else:
            f = float(s)
    except ValueError:
        return None
    return -f if neg else f


def _fetch_ths_individual_fund_flow(max_pages: int = 120) -> tuple[list[dict], bool, str]:
    """同花顺个股资金流(即时净额)。绕过 akshare stock_fund_flow_individual 的列名
    bug（akshare 硬编 10 列，THS 即时表列数/表头已变致 ValueError）。直接取 THS ajax
    分页，read_html 按表头取 '股票代码'/'股票简称'/'净额(元)'。hexin-v token 复用
    akshare 内部 ths.js + py_mini_racer（每页重算，与 akshare 一致）。东财被封时本路
    是资金流通道的唯一可靠源。"""
    if not _AK_OK:
        return [], False, _AK_ERR
    try:
        import py_mini_racer
        from akshare.stock_feature.stock_fund_flow import _get_file_content_ths
        import requests
        from io import StringIO
    except Exception as e:
        return [], False, f"资金流: THS 依赖缺失 {e}"
    base = ("http://data.10jqka.com.cn/funds/ggzjl/field/zdf/"
            "order/desc/page/{}/ajax/1/free/1/")

    def _token() -> str:
        js = py_mini_racer.MiniRacer()
        js.eval(_get_file_content_ths("ths.js"))
        return js.call("v")

    def _headers():
        return {"hexin-v": _token(),
                "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                               "AppleWebKit/537.36 (KHTML, like Gecko) "
                               "Chrome/90.0.4430.85 Safari/537.36"),
                "Referer": "http://data.10jqka.com.cn/funds/hyzjl/",
                "X-Requested-With": "XMLHttpRequest"}
    try:
        r0 = requests.get(base.format(1), headers=_headers(), timeout=15)
    except Exception as e:
        return [], False, f"资金流: THS 首页请求失败 {e}"
    if r0.status_code != 200:
        return [], False, f"资金流: THS 首页 HTTP {r0.status_code}"
    import re
    m = re.search(r'class="page_info"[^>]*>(\d+)/(\d+)', r0.text)
    total_pages = int(m.group(2)) if m else 1
    total_pages = min(total_pages, max_pages)

    def _rows(text):
        try:
            t = pd.read_html(StringIO(text))[0]
        except Exception:
            return []
        cols = set(t.columns)
        col_code = "股票代码" if "股票代码" in cols else None
        col_name = "股票简称" if "股票简称" in cols else None
        col_amt = next((c for c in cols if "净额" in str(c)), None)
        if not (col_code and col_name and col_amt):
            return []
        out = []
        for _, r in t.iterrows():
            amt = _parse_cn_amount(r.get(col_amt))
            if amt is None:
                continue
            # THS 股票代码可能带 sh/sz/bj 前缀，且 pandas read_html 常把代码读成
            # 数字丢前导零(002514→2514)。统一成 6 位纯代码(与 stock_spot 一致)。
            _c = str(r.get(col_code) or "")
            if _c[:2] in ("sh", "sz", "bj"):
                _c = _c[2:]
            if _c:
                try:
                    _c = str(int(float(_c))).zfill(6)
                except (ValueError, TypeError):
                    _c = _c.zfill(6)
            out.append({"code": _c, "name": r.get(col_name),
                        "amount": amt})
        return out

    recs = _rows(r0.text)
    for page in range(2, total_pages + 1):
        try:
            rp = requests.get(base.format(page), headers=_headers(), timeout=15)
            if rp.status_code != 200:
                break
            recs.extend(_rows(rp.text))
        except Exception:
            break   # 单页失败不丢全量
    if not recs:
        return [], False, "资金流: THS 解析为空(表头/结构再变?)"
    return recs, True, ""


def collect_fund_flow(date: str) -> tuple[list[dict], bool, str]:
    """资金流通道：同花顺个股即时净额优先(THS 不封东财 IP)，spot.main_net_inflow 兜底。

    原口径复用 stock_spot.main_net_inflow，但该字段来自东财个股资金流(被封)，新浪
    spot 又无该列，故长期为空 → 资金流通道停摆。改走 THS 直取(绕过 akshare 列名 bug)。
    THS 失败再回落 spot(东财残量，通常空)。actor="" 保证 UNIQUE 去重。"""
    recs_ths, ok_ths, err_ths = _fetch_ths_individual_fund_flow()
    if ok_ths and recs_ths:
        out = []
        for r in recs_ths:
            out.append(_rec(date, r["code"], r["name"], "股票",
                            "资金流", "主力资金", "净买入", r["amount"],
                            raw={"source": "同花顺", "净额(元)": r["amount"]}))
        _set_status("资金流", True, "同花顺", "")
        return out, True, ""
    # THS 失败 → 回落 spot.main_net_inflow(东财残量，通常空)
    spots = db.query_rows("stock_spot", limit=0)
    recs = []
    for sp in spots:
        amt = _to_float(sp.get("main_net_inflow"))
        if amt is None:
            continue
        recs.append(_rec(date, sp.get("code"), sp.get("name"), "股票",
                        "资金流", "主力资金", "净买入", amt,
                        raw={k: _clean(v) for k, v in sp.items()
                             if k in ("code", "name", "main_net_inflow",
                                      "change_pct", "turnover_amount")}))
    if recs:
        _set_status("资金流", True, "spot快照", err_ths)
        return recs, True, err_ths
    _set_status("资金流", False, "",
                err_ths or "资金流: THS 与 spot 均无净额(东财被封/THS 结构变)")
    return [], False, (err_ths or "资金流: THS 与 spot 均无净额(东财被封/THS 结构变)")


# ------------------------------------------------------------------
# 北向（陆股通）
# ------------------------------------------------------------------
def _north_probe_due() -> bool:
    """距上次北向主源探活 ≥7 天才试（主源已下线，日常不空等）。"""
    last = db.get_meta("north_probe_date", "")
    if not last:
        return True
    try:
        last_d = datetime.strptime(last, "%Y-%m-%d")
        return (datetime.now() - last_d).days >= 7
    except Exception:
        return True


def _nb_individual():
    """主源/备援1（已下线，仅探活时调）。"""
    try:
        df = ak.stock_hsgt_individual_em(stock="北向资金")
        if df is not None and not df.empty:
            return df, "东财个股"
    except Exception:
        pass
    try:
        df = ak.stock_hsgt_hold_stock_em(market="北向", indicator="今日排行")
        if df is not None and not df.empty:
            return df, "东财排行"
    except Exception:
        pass
    return None, ""


def _nb_acc_flow():
    """备援2（默认）：沪股通+深股通盘后十大成交股。"""
    out = []
    for sym in ("沪股通", "深股通"):
        try:
            df = ak.stock_hsgt_north_acc_flow_in(symbol=sym)
        except Exception:
            continue
        if df is None or df.empty:
            continue
        col_code = _first_col(df, ["股票代码", "代码", "code"])
        col_name = _first_col(df, ["股票简称", "名称", "name"])
        col_amt = _first_col(df, ["净买额", "买入金额", "成交金额"])
        for _, r in df.iterrows():
            out.append((r.get(col_code), r.get(col_name), _to_float(r.get(col_amt))))
    return out


def _nb_total_flow():
    """降级3：北向总额（无个股，1 条汇总）。"""
    try:
        df = ak.stock_hsgt_north_net_flow_in(symbol="北向")
    except Exception:
        return None
    if df is None or df.empty:
        return None
    col_amt = _first_col(df, ["当日资金流入", "资金净流入", "净流入额"])
    return _to_float(df.iloc[-1].get(col_amt)) if len(df) else None


def collect_northbound(date: str) -> tuple[list[dict], bool, str]:
    """北向：主源(探活,已下线)→备援2 十大成交股(默认,盘后)→降级3 总额。
    2024-08 起实时端点 NoneType 崩，默认走盘后十大成交股；全失败走 stale(§4.2)。"""
    if not _AK_OK:
        return [], False, _AK_ERR
    if _north_probe_due():
        db.set_meta("north_probe_date", datetime.now().strftime("%Y-%m-%d"))
        df, src = _nb_individual()
        if df is not None:
            col_code = _first_col(df, ["股票代码", "代码", "code"])
            col_name = _first_col(df, ["股票简称", "名称", "name"])
            col_amt = _first_col(df, ["持股数量变化", "增持市值", "净买额", "今日增持市值"])
            recs = [_rec(date, r.get(col_code), r.get(col_name), "股票",
                         "北向", "北向资金", "净买入", r.get(col_amt),
                         raw={k: _clean(v) for k, v in r.items()})
                    for _, r in df.iterrows()]
            _set_status("北向", True, src, "")
            return recs, True, ""
    acc = _nb_acc_flow()
    if acc:
        recs = [_rec(date, code, name, "股票", "北向", "北向资金", "上榜", amt,
                     raw={"source": "北向十大成交股(盘后)", "净额(元)": amt})
                for code, name, amt in acc]
        _set_status("北向", True, "北向十大成交股(盘后)", "")
        return recs, True, ""
    tot = _nb_total_flow()
    if tot is not None:
        recs = [_rec(date, None, "北向总额", "股票", "北向", "北向总额",
                     "净买入", tot,
                     raw={"source": "北向总额(盘后)", "净额(元)": tot})]
        _set_status("北向", True, "北向总额(盘后)", "")
        return recs, True, ""
    _set_status("北向", False, "", "北向: 主源崩+十大成交股空+总额无(全失败)")
    return [], False, "北向: 主源崩+十大成交股空+总额无(全失败)"


# ------------------------------------------------------------------
# 龙虎榜（逐股×每席位）
# ------------------------------------------------------------------
def collect_dragon_tiger(date: str) -> tuple[list[dict], bool, str]:
    """龙虎榜（个股级，东财 stock_lhdetail_em）。

    akshare 1.18 起 start_date/end_date 要无破折号 YYYYMMDD（旧版带破折号会
    内部 NoneType 崩）。席位明细 stock_lhb_stock_detail_em 现需 date+flag 逐股
    2 次请求×近百股，东财反爬下太慢且易失败；改用主榜单"龙虎榜净买额"出个股级
    记录（上榜即主力动向），可靠且快。当日无上榜票不算错。"""
    if not _AK_OK:
        return [], False, _AK_ERR
    dash = date.replace("-", "")
    try:
        stocks = ak.stock_lhb_detail_em(start_date=dash, end_date=dash)
    except Exception as e:
        _set_status("龙虎榜", False, "", _friendly_err("龙虎榜", e))
        return [], False, _friendly_err("龙虎榜", e)
    if stocks is None or stocks.empty:
        _set_status("龙虎榜", True, "", "当日无上榜票")
        return [], True, ""   # 当日无上榜票不算错
    col_code = _first_col(stocks, ["代码", "code"])
    col_name = _first_col(stocks, ["名称", "name"])
    col_amt = _first_col(stocks, ["龙虎榜净买额", "净买额", "龙虎榜买入额"])
    col_reason = _first_col(stocks, ["上榜原因", "解读"])
    recs = []
    for _, s in stocks.iterrows():
        code, name = s.get(col_code), s.get(col_name)
        amt = _to_float(s.get(col_amt))
        recs.append(_rec(date, code, name, "股票", "龙虎榜",
                        s.get(col_reason) or "龙虎榜", "上榜", amt,
                        raw={k: _clean(v) for k, v in s.items()}))
    _set_status("龙虎榜", True, "东财", "")
    return recs, True, ""


# ------------------------------------------------------------------
# 游资营业部统计(龙虎榜席位级,按需 on-demand,不入 smart_money_action)
# ------------------------------------------------------------------
def collect_seats(period: str = "近一月") -> dict:
    """游资营业部龙虎榜统计：ak.stock_lhb_traderstatistic_em(symbol=period)
    返回 {rows:[{seat,count,buy,sell,net,amount}], total, error}。
    净额=买入-卖出(>0 净买入游资),按净额降序。机械汇总,非荐股。"""
    if not _AK_OK:
        return {"rows": [], "total": 0, "error": _AK_ERR}
    try:
        df = ak.stock_lhb_traderstatistic_em(symbol=period)
    except Exception as e:
        return {"rows": [], "total": 0, "error": _friendly_err("游资", e)}
    if df is None or df.empty:
        return {"rows": [], "total": 0, "error": None}
    col_name = _first_col(df, ["营业部名称", "营业部"])
    col_cnt = _first_col(df, ["上榜次数"])
    col_buy = _first_col(df, ["买入额"])
    col_sell = _first_col(df, ["卖出额"])
    col_amt = _first_col(df, ["龙虎榜成交金额"])
    rows = []
    for _, r in df.iterrows():
        buy = _to_float(r.get(col_buy))
        sell = _to_float(r.get(col_sell))
        net = (buy - sell) if (buy is not None and sell is not None) else None
        rows.append({
            "seat": r.get(col_name), "count": _to_float(r.get(col_cnt)),
            "buy": buy, "sell": sell, "net": net,
            "amount": _to_float(r.get(col_amt)),
        })
    rows.sort(key=lambda x: x.get("net") or 0, reverse=True)
    return {"rows": rows, "total": len(rows), "error": None}


def collect_seats_stocks(date: str | None = None) -> dict:
    """游资追逐个股(席位明细): 对该日龙虎榜个股逐个调
    stock_lhb_stock_detail_em(symbol,date,'买入') 取买入席位明细。
    返回 {rows:[{date,code,name,seat,buy,sell,net}], total, date, error}。
    慢(约 N 股 × 1 调用,N≈10-30),on-demand 不入库,机械汇总非荐股。
    ETF 不上龙虎榜,仅个股。"""
    if not _AK_OK:
        return {"rows": [], "total": 0, "error": _AK_ERR}
    if not date:
        try:
            latest = db.query_rows("smart_money_action", where="channel = ?",
                                   params=("龙虎榜",), order_by="date DESC", limit=1)
            date = latest[0].get("date") if latest else None
        except Exception:
            date = None
    if not date:
        return {"rows": [], "total": 0, "date": None, "error": "无龙虎榜数据日期"}
    stocks = db.query_rows("smart_money_action",
                           where="channel = ? AND date = ?",
                           params=("龙虎榜", date), limit=0)
    if not stocks:
        return {"rows": [], "total": 0, "date": date, "error": f"{date} 无龙虎榜个股"}
    dash = date.replace("-", "")
    rows = []
    for s in stocks:
        code = str(s.get("code") or "")
        if not code:
            continue
        try:
            df = ak.stock_lhb_stock_detail_em(symbol=code, date=dash, flag="买入")
        except Exception:
            continue   # 单股失败不丢全量
        if df is None or df.empty:
            continue
        col_seat = _first_col(df, ["交易营业部名称", "营业部名称", "席位"])
        col_buy = _first_col(df, ["买入金额"])
        col_sell = _first_col(df, ["卖出金额"])
        col_net = _first_col(df, ["净额"])
        for _, r in df.iterrows():
            seat = r.get(col_seat)
            if not seat:
                continue
            rows.append({"date": date, "code": code, "name": s.get("name"),
                         "seat": seat, "buy": _to_float(r.get(col_buy)),
                         "sell": _to_float(r.get(col_sell)),
                         "net": _to_float(r.get(col_net))})
    rows.sort(key=lambda x: x.get("net") or 0, reverse=True)
    return {"rows": rows, "total": len(rows), "date": date, "error": None}


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
    # 候选 = 成交额前200 ∪ 国家队种子（去重，覆盖小盘国家队重仓股）
    candidates = spot_df.to_dict("records")
    seed_codes = _load_seed()
    spot_codes = {str(sp.get("code")) for sp in candidates}
    # stock_spot 全表 code→名（种子代码常不在 top200，需全表兜底解析股票名）
    spot_name = {str(sp.get("code")): sp.get("name") for sp in spots}
    for sc in seed_codes:
        if sc not in spot_codes:
            candidates.append({"code": sc,
                                "name": NATIONAL_TEAM_HOLDINGS_SEED.get(sc) or spot_name.get(sc),
                                "turnover_amount": 0})
    as_of = date
    period = _latest_report_period()
    recs = []
    tried = 0
    for sp in candidates:
        code = sp.get("code")
        tried += 1
        try:
            # akshare 1.18：stock_gdfx_free_top_10 → stock_gdfx_free_top_10_em，
            # symbol 需 sh/sz 前缀，date 需报告期 YYYYMMDD。
            df = ak.stock_gdfx_free_top_10_em(symbol=_prefix_code(code),
                                              date=period)
        except Exception:
            continue
        if df is None or df.empty:
            continue
        col_holder = _first_col(df, ["股东名称", "股东"])
        col_chg = _first_col(df, ["增减", "变动股数", "持股变动", "变化"])
        for _, r in df.iterrows():
            holder = r.get(col_holder)
            if holder is None:
                continue
            # 增减=持股变动股数(股)；'不变'/非数值→0。作 amount 让前端净额列有数(单位股)
            chg = _to_float(r.get(col_chg))
            if chg is None:
                chg = 0.0
            recs.append(_rec(date, code, sp.get("name"), "股票", "十大股东",
                            holder, "持仓", chg, as_of=as_of,
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
    # 学习式扩充：命中国家队关键字的 code 并入种子
    hit_codes = {str(rec["code"]) for rec in recs
                 if rec.get("actor") and any(k in rec["actor"] for k in NATIONAL_TEAM)}
    if hit_codes:
        _save_seed(hit_codes)
    _set_status("十大股东", True, "东财", "")
    return recs, True, ""


# ------------------------------------------------------------------
# 高管增减持(按日期全市场)
# ------------------------------------------------------------------
def collect_management_hold(date: str) -> tuple[list[dict], bool, str]:
    """高管增减持(东财 stock_hold_management_detail_em,全市场全历史)。

    akshare 1.18：旧 stock_hold_management_em 改名 stock_hold_management_detail_em，
    无参、返回 17 万行全历史(拉取约 4-5 分钟)。本地按'日期'列过滤近 7 日真实变动，
    记录 date 取行内变动日期(非刷新日)，保今日仍有近期动作可查。"""
    if not _AK_OK:
        return [], False, _AK_ERR
    try:
        df = ak.stock_hold_management_detail_em()
    except Exception as e:
        _set_status("高管增减持", False, "", _friendly_err("高管增减持", e))
        return [], False, _friendly_err("高管增减持", e)
    if df is None or df.empty:
        _set_status("高管增减持", True, "", "无增减持")
        return [], True, ""
    col_code = _first_col(df, ["代码", "股票代码", "code"])
    col_name = _first_col(df, ["名称", "股票简称", "name"])
    col_actor = _first_col(df, ["变动人", "高管名称", "姓名"])
    col_action = _first_col(df, ["变动原因", "变动方向", "增减"])
    col_amt = _first_col(df, ["变动金额", "成交金额", "变动数额"])
    col_date = _first_col(df, ["日期", "变动日期"])
    df = _filter_recent(df, col_date, days=7)
    recs = []
    for _, r in df.iterrows():
        act = str(r.get(col_action) or "")
        action = "增持" if "增持" in act else "减持" if "减持" in act else act
        d = _norm_date(r.get(col_date)) or date
        recs.append(_rec(d, r.get(col_code), r.get(col_name), "股票",
                        "高管增减持", r.get(col_actor), action,
                        r.get(col_amt),
                        raw={k: _clean(v) for k, v in r.items()}))
    _set_status("高管增减持", True, "东财", "")
    return recs, True, ""


# ------------------------------------------------------------------
# 限售解禁(按月份)
# ------------------------------------------------------------------
def collect_share_unlock(date: str) -> tuple[list[dict], bool, str]:
    """限售解禁(东财 stock_restricted_release_detail_em,按日期范围)。

    akshare 1.18：旧 stock_share_change_em(symbol=月) 已下线；改用
    stock_restricted_release_detail_em(start_date,end_date) 拉当月个股解禁清单。
    列无'解禁股东'，actor 取'限售股类型'(如股权激励限售股份)；amount=实际解禁市值；
    as_of/date=解禁时间(真实解禁日，可能未来)。“当月无解禁”不算错。"""
    if not _AK_OK:
        return [], False, _AK_ERR
    month = date[:7]                       # 'YYYY-MM'
    start = month + "-01"
    end = _month_end(month)
    try:
        df = ak.stock_restricted_release_detail_em(
            start_date=start.replace("-", ""), end_date=end.replace("-", ""))
    except Exception as e:
        _set_status("限售解禁", False, "", _friendly_err("限售解禁", e))
        return [], False, _friendly_err("限售解禁", e)
    if df is None or df.empty:
        _set_status("限售解禁", True, "", f"{month} 无解禁")
        return [], True, ""
    col_code = _first_col(df, ["股票代码", "代码", "code"])
    col_name = _first_col(df, ["股票简称", "名称", "name"])
    col_amt = _first_col(df, ["实际解禁市值", "解禁数量", "实际解禁数量"])
    col_date = _first_col(df, ["解禁时间", "解禁日期"])
    col_actor = _first_col(df, ["限售股类型", "解禁股东", "股东名称"])
    recs = []
    for _, r in df.iterrows():
        as_of = _norm_date(r.get(col_date)) or date
        recs.append(_rec(as_of, r.get(col_code), r.get(col_name), "股票",
                        "限售解禁", r.get(col_actor) or "限售解禁", "解禁",
                        r.get(col_amt), as_of=as_of,
                        raw={k: _clean(v) for k, v in r.items()}))
    _set_status("限售解禁", True, "东财", "")
    return recs, True, ""


def _last_ok_date(channel: str) -> str:
    """该通道最近一次有数据日期（无则空）。"""
    try:
        rows = db.query_rows("smart_money_action", where="channel = ?",
                             params=(channel,), order_by="date DESC", limit=1)
        return rows[0].get("date", "") if rows else ""
    except Exception:
        return ""


def _stale_fallback(channel: str, err: str) -> tuple[list[dict], bool, str]:
    """拉取失败时查 DB 该通道最近一次有数据日期的全部行作回退数据。
    有→(旧rows, True, "回退至 <stale_date>（采集失败: <err>）")；
    无→([], False, err)。stale 标记落 CHANNEL_STATUS + db.set_meta。"""
    try:
        stale_date = _last_ok_date(channel)
        if not stale_date:
            return [], False, err
        old = db.query_rows("smart_money_action",
                            where="channel = ? AND date = ?",
                            params=(channel, stale_date), order_by="", limit=0)
        note = f"回退至 {stale_date}（采集失败: {err}）"
        _set_status(channel, True, "", note)
        CHANNEL_STATUS[channel]["stale"] = True
        CHANNEL_STATUS[channel]["stale_date"] = stale_date
        CHANNEL_STATUS[channel]["stale_note"] = note
        db.set_meta(f"sm_stale_{channel}", f"{stale_date}|{err}")
        return old, True, note
    except Exception:
        return [], False, err


# ------------------------------------------------------------------
# 编排
# ------------------------------------------------------------------
def refresh_today(date: str | None = None,
                  channels: list[str] | None = None) -> dict:
    """串行跑 6 通道 → upsert → 写 meta + CHANNEL_STATUS。
    拉取失败时若 DB 有旧数据 → stale 降级（不入库新行）。
    channels 非空时只跑指定通道，其余用 CHANNEL_STATUS 现状回填 skipped=True
    （不重采、不误显灰），供单通道按需刷新；单通道模式不刷新全局 update_time
    （避免误导"全量已更新"），改标 partial=True + 保留上次全量时间。"""
    db.init_db()
    if not date:
        date = datetime.now().strftime("%Y-%m-%d")
    report = {"date": date, "counts": {}, "channels": {}}
    plan = [("资金流", collect_fund_flow), ("北向", collect_northbound),
            ("龙虎榜", collect_dragon_tiger), ("十大股东", collect_holders),
            ("高管增减持", collect_management_hold),
            ("限售解禁", collect_share_unlock)]
    for ch, fn in plan:
        if channels and ch not in channels:
            st = CHANNEL_STATUS.get(ch, {})
            report["counts"][ch] = 0
            report["channels"][ch] = {
                "ok": st.get("ok", False), "stale": st.get("stale", False),
                "skipped": True, "rows": 0,
                "last_ok_date": _last_ok_date(ch),
                "err": "本次未刷新(单通道)", "at": st.get("at", "")}
            continue
        try:
            recs, ok, err = fn(date)
        except Exception as e:
            recs, ok, err = [], False, f"{ch}: 未捕获异常 {e}"
            _set_status(ch, False, "", str(e))
        if not ok:
            old, stale_flag, note = _stale_fallback(ch, err)
            if stale_flag:
                st = CHANNEL_STATUS.get(ch, {})
                report["counts"][ch] = 0
                report["channels"][ch] = {
                    "ok": True, "stale": True, "rows": len(old),
                    "stale_date": st.get("stale_date", ""),
                    "last_ok_date": st.get("stale_date", ""),
                    "err": note, "at": st.get("at", "")}
                continue
            st = CHANNEL_STATUS.get(ch, {})
            report["counts"][ch] = 0
            report["channels"][ch] = {
                "ok": False, "stale": False, "rows": 0,
                "last_ok_date": _last_ok_date(ch),
                "err": err, "at": st.get("at", "")}
            continue
        n = db.upsert_rows("smart_money_action", recs) if recs else 0
        st = CHANNEL_STATUS.get(ch, {})
        report["counts"][ch] = n
        report["channels"][ch] = {
            "ok": True, "stale": False, "rows": n,
            "last_ok_date": date, "err": err, "at": st.get("at", "")}
    if channels is None:
        report["update_time"] = db.stamp_update_time()
    else:
        report["update_time"] = db.last_update_time()
        report["partial"] = True
    return report
