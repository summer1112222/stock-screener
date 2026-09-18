# -*- coding: utf-8 -*-
"""次日强势 5 步流程(硬剔除+软打分混合)。

**不触网不新增表**: 复用 stock_spot/sector_fund_flow/stock_daily。
step3 复用 backtest.signals._uni_panels 取 close 面板(仅取数)+本地算 MA。
step5 从 industry_board 成分表反查 code→board(同 daily_strong 套路)。

5 步(对齐用户方法论):
  step1 入选: 涨幅>5% + 换手率>3% + 股价<50
  step2 雷区: 流通市值 10-200亿 + PE<=150 且非亏损 + 非ST
  step3 形态: 多头排列(5/10/20 MA 向上发散) 或 放量突破(站稳60日线+量翻倍)
  step4 量价: 量比>2.5 强度 + 涨幅<7% 避追高
  step5 板块助攻: 所属板块(行业)热度前5 + 板块内>=2 只涨停

混合编排: step1/2/3/5 硬剔除(通过/不通过), step4 软打分(0-100 排序)。
排序键: 硬通过数×10 + 软分 降序。30s 进程缓存。
合规(个人自用放松): 次日强势清单——机械 5 步流程排序观察清单。
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta

import numpy as np

from data import db
from data import pytdx_client
from screener.indicators import ma_alignment
from screener import sector_heat as _sh

# 雷达共振触发原语（Task 1 产出，spec 2026-09-16）：模块级导入以便测试 monkeypatch；
# 导入失败（smart_money 缺失）时降级为 None，boost 块跳过不崩。
try:
    from screener.smart_money import radar_resonance_for
except Exception:
    radar_resonance_for = None

_SCAN_K = 200       # 粗筛后精算上限(按涨幅降序)
_TDX_ENRICH_K = 40  # TDX 实时/财务补全只覆盖前 N 名，避免全市场请求压力
_HISTORY_FILL_K = 40  # TDX 自动补历史只覆盖涨幅靠前的小名单，避免阻塞全市场
_CACHE: dict[tuple, tuple] = {}
_CACHE_TTL = 30


def _nan(v):
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return v
    if math.isnan(f) or math.isinf(f):
        return None
    return f


# ------------------------------------------------------------------
# TDX 字段补全(新浪 spot 缺 换手率/流通市值/PE/量比, 用 TDX 机械算出)
# 缺失值保持 None(前端显示 —), 不伪造。TDX 失败/无数据 → 不补, 降级旧路径。
# ------------------------------------------------------------------

def _finance_derived(fin: dict, quote: dict | None) -> dict:
    """从 get_finance_info + get_quote 机械补字段，功匹配产出 0/None 不崩。

    fin: liutongguben(流通股)/zongguben(总股本)/jinglirun(净利润)/meigujingzichan。
    quote: get_quote 返回的单股行情(price/vol 单位手, 1手=100股)。
    返回 {circulating_market_cap(亿), total_market_cap(亿), pe, turnover_rate}。
    任一项缺数据→对应 None(前端显示 —)，不伪造。
    """
    price = _to_f(quote.get("price")) if quote else None
    lt = _to_f(fin.get("liutongguben"))
    zg = _to_f(fin.get("zongguben"))
    ni = _to_f(fin.get("jinglirun"))
    vol = _to_f(quote.get("vol")) if quote else None  # 手
    out = {"circulating_market_cap": None, "total_market_cap": None,
           "pe": None, "turnover_rate": None}
    if price is not None and lt:
        out["circulating_market_cap"] = _nan(
            price * lt / 1e8)                     # 市值(亿)
    if price is not None and zg:
        out["total_market_cap"] = _nan(price * zg / 1e8)
    if price is not None and ni and zg:
        eps_proxy = ni / zg
        if eps_proxy > 0:
            out["pe"] = _nan(price / eps_proxy)   # PE=价/每股净利
    if vol is not None and lt and price is not None:
        out["turnover_rate"] = _nan(vol * 100 / lt * 100)  # 换手(%)
    return out


def _tdx_vol_ratio(code: str, quote: dict | None,
                   hist_amount: dict) -> float | None:
    """量比 = 当日实时量 / 前 5 日均量(用成交额做量代理, 排除停牌缩量)。
    hist_amount: {code: [近 N 日 amount 序列]}。无历史 → None。"""
    amt_cur = _to_f(quote.get("amount")) if quote else None
    once_hist = hist_amount.get(_code_key(code)) or hist_amount.get(code)
    if amt_cur is None or not once_hist:
        return None
    prev = [_to_f(x) for x in once_hist[:-1] if _to_f(x) is not None and _to_f(x) > 0]
    if not prev:
        return None
    avg5 = sum(prev[-5:]) / max(len(prev[-5:]), 1)
    if avg5 <= 0:
        return None
    return _nan(amt_cur / avg5)


def _tdx_rich_enrich(spot: dict, quote_by_code: dict,
                     fin_by_code: dict, hist_amount: dict) -> dict:
    """把 TDX 字段合并进 spot(值优先, 缺则保 spot 现状)。返回新 spot。"""
    code = str(spot.get("code") or "")
    pure = _code_key(code)
    if not pure:
        return spot
    out = dict(spot)
    q = dict(quote_by_code.get(pure) or {})
    if _to_f(q.get("price")) is None:
        q["price"] = spot.get("latest_price")
    # TDX 五档偶发不可用时，新浪 spot 仍有成交额；按成交额推导
    # vol(手)=成交额/(价格×100)，让换手率/量比不因盘口失败一起消失。
    if _to_f(q.get("amount")) is None and _to_f(spot.get("turnover_amount")) is not None:
        q["amount"] = spot.get("turnover_amount")
    if _to_f(q.get("vol")) is None:
        price = _to_f(q.get("price"))
        amount = _to_f(q.get("amount"))
        if price and amount is not None:
            q["vol"] = amount / (price * 100.0)
    # TDX 实时快照是主源；仅当字段缺失时才保留 stock_spot 降级值。
    price = _to_f(q.get("price"))
    last_close = _to_f(q.get("last_close"))
    if price is not None:
        out["latest_price"] = price
    if price is not None and last_close and last_close > 0:
        out["change_pct"] = _nan((price / last_close - 1.0) * 100.0)
    fi = _finance_derived(fin_by_code.get(pure) or {}, q or None)
    for k, v in fi.items():
        if v is not None:
            out[k] = v
    vr = _tdx_vol_ratio(code, q or None, hist_amount)
    if vr is not None and _to_f(out.get("volume_ratio")) is None:
        out["volume_ratio"] = vr
    return out


def _clip(v, lo=0.0, hi=1.0):
    return max(lo, min(hi, v))


def _to_f(v):
    try:
        f = float(v)
        return f if not (math.isnan(f) or math.isinf(f)) else None
    except (TypeError, ValueError):
        return None


def _step1_pass(s, p) -> bool:
    """入选门槛: 涨幅>min_change_pct + 换手>min_turnover + 股价<max_price。"""
    chg = _to_f(s.get("change_pct"))
    tr = _to_f(s.get("turnover_rate"))
    px = _to_f(s.get("latest_price"))
    if chg is None or tr is None or px is None:
        return False
    return chg > p["min_change_pct"] and tr > p["min_turnover"] and px < p["max_price"]


def _step2_pass(s, p, st_set=None) -> bool:
    """雷区剔除: 市值[min_mv,max_mv] + PE<=max_pe 且非亏损 + 非ST。
    st_set=预加载的 ST code 集合(st_list 表; stock_spot 无 st_type 列)。"""
    mc = _to_f(s.get("circulating_market_cap"))
    pe = _to_f(s.get("pe"))
    if not p.get("exclude_st", True):
        is_st = False
    else:
        is_st = (str(s.get("code")) in st_set) if st_set is not None else bool(s.get("st_type"))
    if mc is None or mc < p["min_mv"] or mc > p["max_mv"]:
        return False
    if pe is None or pe <= 0 or pe > p["max_pe"]:
        return False
    if is_st:
        return False
    return True


# ------------------------------------------------------------------
# step3 批量 MA(复用 backtest.signals._uni_panels)
# ------------------------------------------------------------------

def _missing_history_codes(close, codes: list[str], min_rows: int = 60) -> list[str]:
    """找出没有足够日 K 的候选股；同时兼容纯代码和 sh/sz 前缀列。"""
    if close is None or getattr(close, "empty", True):
        return list(codes)
    columns = set(close.columns)
    missing = []
    for code in codes:
        col = next((c for c in (code, _add_prefix(code), _code_key(code))
                    if c in columns), None)
        if col is None or int(close[col].count()) < min_rows:
            missing.append(code)
    return missing


def _fill_missing_history(universe: str, codes: list[str]) -> int:
    """为缺少 stock_daily 历史的候选股按需从 TDX 拉最近日 K 并入库。

    只服务个股次日强势流程；调用方已将名单限制在涨幅靠前的小名单。只取约
    180 个自然日，覆盖 MA60、20 日量均值和量比，避免首次请求为每股拉多年历史。
    采集并发、SQLite 写入串行，避免并发写锁；返回成功补入的股票数。
    """
    if str(universe).lower() != "stock" or not codes:
        return 0
    try:
        from concurrent.futures import ThreadPoolExecutor, as_completed
        from data import history
    except Exception:
        return 0

    def fetch_one(code):
        pure = _code_key(code)
        if not pure:
            return pure, []
        try:
            end = datetime.now()
            start = (end - timedelta(days=180)).strftime("%Y%m%d")
            df, ok, _ = history.fetch_stock_hist(
                _add_prefix(pure), start, end.strftime("%Y%m%d"))
            if not ok or df is None or df.empty:
                return pure, []
            records = df.astype(object).where(df.notna(), None).to_dict("records")
            return pure, records
        except Exception:
            return pure, []

    fetched: list[tuple[str, list[dict]]] = []
    workers = min(8, max(1, len(codes)))
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = [ex.submit(fetch_one, code) for code in codes]
        for future in as_completed(futures):
            pure, records = future.result()
            if records:
                fetched.append((pure, records))

    success = 0
    for pure, records in fetched:
        try:
            db.upsert_rows("stock_daily", records)
            success += 1
        except Exception:
            continue
    return success


def _ma_arrange_batch(universe: str, codes: list[str], days: int = 60) -> tuple[dict, dict]:
    """批量算 5/10/20/60 MA + 量。返 (ma_info, hist_amount)。
    ma_info: {code: {ma5,ma10,ma20,ma60,bullish_align,volume_breakout,bearish,converged,
                     need_history,last_vol,vol_avg20}}。
    hist_amount: {code: [近 N 日 amount 序列]} 供量比计算。"""
    out = {c: {"ma5": None, "ma10": None, "ma20": None, "ma60": None,
               "bullish_align": False, "volume_breakout": False,
               "bearish": False, "converged": False, "need_history": False,
               "last_vol": None, "vol_avg20": None,
               "close_series": [], "amount_series": []} for c in codes}
    hist_amount: dict[str, list] = {}
    if not codes:
        return out, hist_amount
    try:
        from backtest import signals as _sig
        # stock_daily.symbol 是 sh600519 格式，但 codes 来自 spot(纯6位代码)。
        # 传入 _uni_panels 前必须加前缀，否则 symbol 过滤全空。
        prefixed = [_add_prefix(c) for c in codes]
        close, amount = _sig._uni_panels(universe, prefixed)
    except Exception:
        for c in codes:
            out[c]["need_history"] = True
        return out, hist_amount
    # 无历史(从没拉过 stock_daily) → 按需从 TDX 补拉日 K 入库后重算。
    missing = _missing_history_codes(close, codes, 60)
    if missing:
        # codes 已按涨幅降序粗筛；只补最靠前的小名单，避免 200 股逐只串行
        # 占满 TDX 单连接锁。未补到的股票继续诚实标记 need_history。
        _fill_missing_history(universe, missing[:_HISTORY_FILL_K])
        try:
            close, amount = _sig._uni_panels(universe, [_add_prefix(c) for c in codes])
        except Exception:
            pass
    if close is None or close.empty:
        for c in codes:
            out[c]["need_history"] = True
        return out, hist_amount
    # 构建 hist_amount；历史面板使用 sh600519 格式，而候选列表可能带 sh/sz 前缀。
    if amount is not None:
        for c in codes:
            candidates = (c, _add_prefix(c), _code_key(c))
            amount_col = next((col for col in candidates if col in amount.columns), None)
            if amount_col is not None:
                s = amount[amount_col].dropna().tolist()
                if len(s) >= 5:
                    hist_amount[_code_key(c)] = s
    window = max(days, 60)
    for c in codes:
        candidates = (c, _add_prefix(c), _code_key(c))
        close_col = next((col for col in candidates if col in close.columns), None)
        if close_col is None:
            out[c]["need_history"] = True
            continue
        s = close[close_col].dropna().tail(window)
        if len(s) < 60:
            out[c]["need_history"] = True
            continue
        # 挂完整序列供连续因子(mom_5_1/sr_10/close_vol_corr)算真实历史，
        # 而非仅均线代理；amount_series 供量价相关，缺失保持空列表。
        out[c]["close_series"] = s.tolist()
        if amount is not None:
            amount_col = next((col for col in (c, _add_prefix(c), _code_key(c))
                               if col in amount.columns), None)
            if amount_col is not None:
                amt = amount[amount_col].dropna().tolist()
                if amt:
                    out[c]["amount_series"] = amt
        aligned = ma_alignment(s, (5, 10, 20, 60))
        ma5 = aligned["ma5"].iloc[-1]
        ma10 = aligned["ma10"].iloc[-1]
        ma20 = aligned["ma20"].iloc[-1]
        ma60 = aligned["ma60"].iloc[-1]
        ma20_prev = aligned["ma20"].iloc[-6] if len(s) >= 6 else ma20
        last_close = s.iloc[-1]
        out[c].update({"ma5": _nan(ma5), "ma10": _nan(ma10), "ma20": _nan(ma20),
                       "ma60": _nan(ma60)})
        out[c]["bullish_align"] = bool(ma5 > ma10 > ma20 and ma20 > ma20_prev)
        out[c]["bearish"] = bool(ma5 < ma10 < ma20)
        if min(ma5, ma10, ma20) > 0:
            spread = (max(ma5, ma10, ma20) - min(ma5, ma10, ma20)) / min(ma5, ma10, ma20)
            out[c]["converged"] = bool(spread < 0.005)
        if amount is not None:
            amount_col = next((col for col in (c, _add_prefix(c), _code_key(c))
                               if col in amount.columns), None)
            if amount_col is not None:
                amt = amount[amount_col].dropna()
                if len(amt) >= 20:
                    last_vol = amt.iloc[-1]
                    vol_avg20 = amt.iloc[-20:].mean()
                    out[c]["last_vol"] = _nan(last_vol)
                    out[c]["vol_avg20"] = _nan(vol_avg20)
                    out[c]["volume_breakout"] = bool(
                        last_close > ma60 and last_vol >= 2 * vol_avg20)
    return out, hist_amount


def _step3_pass(info: dict) -> bool:
    """形态过滤: 多头排列 OR 放量突破 通过; 空头排列 剔除。"""
    if info.get("need_history"):
        return False
    if info.get("bearish"):
        return False
    return info.get("bullish_align") or info.get("volume_breakout")


def _explain_step_status(s: dict, mi: dict, bd: dict, p: dict,
                        step4_score: float, step5_pass: bool) -> tuple[dict, dict]:
    """生成五步可解释诊断；缺数据与规则未通过明确区分。"""
    def status(passed, missing=False):
        return "missing" if missing else ("pass" if passed else "fail")

    reasons = {}
    statuses = {}
    chg, tr, price = (_to_f(s.get(k)) for k in
                      ("change_pct", "turnover_rate", "latest_price"))
    missing = [name for name, value in (("涨幅", chg), ("换手率", tr), ("价格", price))
               if value is None]
    statuses["step1"] = status(_step1_pass(s, p), bool(missing))
    reasons["step1"] = ("缺少" + "、".join(missing) if missing else
                         f"涨幅 {chg:.2f}% / 换手 {tr:.2f}% / 价格 {price:.2f}")

    mv, pe = _to_f(s.get("circulating_market_cap")), _to_f(s.get("pe"))
    missing = [name for name, value in (("流通市值", mv), ("PE", pe)) if value is None]
    statuses["step2"] = status(_step2_pass(s, p), bool(missing))
    if missing:
        reasons["step2"] = "缺少" + "、".join(missing)
    elif s.get("st_type"):
        reasons["step2"] = f"ST({s.get('st_type')})"
    else:
        reasons["step2"] = f"市值 {mv:.2f}亿 / PE {pe:.2f}"

    history_missing = bool(mi.get("need_history"))
    statuses["step3"] = status(_step3_pass(mi), history_missing)
    reasons["step3"] = ("历史日线不足 60 日" if history_missing else
                         ("多头排列" if mi.get("bullish_align") else
                          "放量突破" if mi.get("volume_breakout") else
                          "空头排列或未满足形态"))

    vr = _to_f(s.get("volume_ratio"))
    missing = [name for name, value in (("量比", vr), ("涨幅", chg)) if value is None]
    statuses["step4"] = status(step4_score > 0, bool(missing))
    reasons["step4"] = ("缺少" + "、".join(missing) if missing else
                         f"量比 {vr:.2f} / 软分 {step4_score:.2f}")

    board_missing = not bd.get("board") or bd.get("board_rank") is None
    statuses["step5"] = status(step5_pass, board_missing)
    reasons["step5"] = ("板块成员或热度数据缺失" if board_missing else
                         f"{bd.get('board')}·热度第 {bd.get('board_rank')}·涨停 {bd.get('board_zt_count') or 0} 只")
    return statuses, reasons


def _data_quality(s: dict, mi: dict, quote_available: bool,
                  score_coverage: float) -> dict:
    """汇总结果可用性，不将缺失数据等同于规则否决。"""
    missing = []
    for label, key in (("涨幅", "change_pct"), ("换手率", "turnover_rate"),
                       ("流通市值", "circulating_market_cap"), ("PE", "pe"),
                       ("量比", "volume_ratio")):
        if _to_f(s.get(key)) is None:
            missing.append(label)
    if mi.get("need_history"):
        missing.append("历史日线")
    return {"source": "tdx" if quote_available else "stock_spot",
            "quote_available": bool(quote_available),
            "history_available": not bool(mi.get("need_history")),
            "score_coverage": round(float(score_coverage or 0), 4),
            "missing": missing}


# ------------------------------------------------------------------
# step4 软打分
# ------------------------------------------------------------------

def _step4_score(s) -> float:
    """软打分(0-100): 量比>2.5 强度(0.5权重) + 涨幅<7% 避追高(0.5权重)。"""
    vr = _to_f(s.get("volume_ratio"))
    chg = _to_f(s.get("change_pct"))
    a = _clip((vr - 1.0) / 1.5) if vr is not None else 0.0
    if chg is None:
        b = 0.0
    elif chg < 7:
        b = 1.0
    elif chg < 9.8:
        b = (9.8 - chg) / 2.8
    else:
        b = 0.0
    return round(_clip(0.5 * a + 0.5 * b) * 100, 2)


# ------------------------------------------------------------------
# step5 板块助攻(行业资金流 + 按需成分股)
# ------------------------------------------------------------------

def _code_key(code) -> str:
    """将 spot 的纯代码与 board_stocks 的 sh/sz/bj 代码归一到同一键。"""
    s = str(code or "").strip().lower()
    for prefix in ("sh", "sz", "bj"):
        if s.startswith(prefix) and s[len(prefix):].isdigit():
            s = s[len(prefix):]
            break
    return s.zfill(6) if s.isdigit() else s


def _add_prefix(code) -> str:
    """给纯 6 位代码加 sh/sz 前缀，匹配 stock_daily.symbol 列格式。"""
    c = _code_key(str(code))
    if not c or not c.isdigit():
        return str(code)
    if c.startswith(("5", "6", "9")):
        return "sh" + c
    return "sz" + c


def _rank_sector_flow(sff: list) -> list[dict]:
    """按行业今日主力净流入降序排名，保留名称和排名。

    资金流字段为空时不把所有板块伪装成同一排名；调用方会再用 TDX
    成员实时涨幅构造机械热度排名。
    """
    valid = [x for x in (sff or [])
             if x.get("name") and _to_f(x.get("main_net_inflow")) is not None]
    ranked = sorted(valid, key=lambda x: _to_f(x.get("main_net_inflow")), reverse=True)
    return [{"name": x.get("name"), "rank": i + 1} for i, x in enumerate(ranked)]


def _rank_tdx_blocks(block_members: dict[str, list[str]],
                     spot_by_code: dict[str, dict],
                     quote_by_code: dict[str, dict] | None = None) -> list[dict]:
    """用 TDX 板块成员的实时涨幅/涨停扩散计算板块热度排名。

    TDX 板块文件提供成员关系，stock_spot/TDX quote 提供当日价格；这里是
    机械横截面热度，不等同主力资金流。成员缺少行情时跳过，不伪造涨幅。
    """
    def _change(row, quote):
        value = _to_f((row or {}).get("change_pct"))
        if value is not None:
            return value
        price = _to_f((quote or {}).get("price"))
        close = _to_f((quote or {}).get("last_close"))
        if price is not None and close is not None and close > 0:
            return (price - close) / close * 100.0
        return None

    rows = []
    for name, members in (block_members or {}).items():
        changes = [_change(spot_by_code.get(_code_key(c)),
                           (quote_by_code or {}).get(_code_key(c)))
                   for c in members]
        changes = [x for x in changes if x is not None]
        if not changes:
            continue
        zt = sum(x >= 9.8 for x in changes)
        positive = sum(x > 0 for x in changes)
        rows.append({"name": name, "heat": float(np.mean(changes)),
                     "zt": zt, "up": positive, "coverage": len(changes)})
    rows.sort(key=lambda x: (x["heat"], x["zt"], x["up"]), reverse=True)
    return [{"name": x["name"], "rank": i + 1, "board_zt_count": x["zt"],
             "board_heat": _nan(x["heat"]), "member_coverage": x["coverage"]}
            for i, x in enumerate(rows)]


def _board_members_batch(board_names: list[str], spot_by_code: dict | None = None) -> dict[str, list[str]]:
    """按需取得板块成分股，优先通达信板块文件，失败再用既有板块源。"""
    if not board_names:
        return {}
    try:
        tdx = pytdx_client.get_block_members("industry")
    except Exception:
        tdx = {}
    if tdx:
        # TDX 的行业文件本身是行业/概念混合分类；只取请求板名精确或包含命中。
        out = {}
        for board in board_names:
            names = [name for name in tdx if name == board or name in board or board in name]
            members = []
            for name in sorted(names, key=len, reverse=True):
                members.extend(_code_key(c) for c in tdx.get(name, []))
            out[str(board)] = list(dict.fromkeys(c for c in members if c))
        # 不要用 any(out.values()) 短路的降级：TDX 缺少"通信设备""汽车零部件"
        # 等常见行业板块，部分匹配不应阻挡其他板块从 board_stocks 补全。
        missing = [b for b in board_names if not out.get(b)]
        if not missing:
            return out
        # 对缺失板块从 board_stocks 降级补全
        try:
            from data import board_stocks
        except Exception:
            return out
        for board in missing:
            try:
                rows = board_stocks.fetch_constituents(board, "行业") or []
                members = [_code_key(row.get("code") or row.get("raw_code")) for row in rows]
                out[str(board)] = list(dict.fromkeys(c for c in members if c))
            except Exception:
                out[str(board)] = []
        return out
    try:
        from data import board_stocks
    except Exception:
        return {str(board): [] for board in board_names}
    out = {}
    for board in board_names:
        try:
            rows = board_stocks.fetch_constituents(board, "行业") or []
            members = [_code_key(row.get("code") or row.get("raw_code")) for row in rows]
            out[str(board)] = list(dict.fromkeys(c for c in members if c))
        except Exception:
            out[str(board)] = []
    return out


def _step5_pass(code: str, ranked_sectors: list[dict], board_members: dict,
                spot_by_code: dict) -> tuple[bool, dict]:
    """板块助攻: 行业资金流热度前5 + 板块内至少2只涨停(≥9.8%)。

    一个股票可能属于多个行业，按热度最高的命中板块计；成员数据失败时不猜测，
    返回 False。`board_members` 由主流程批量取得，避免每只股票重复触网。
    """
    base = {"board": None, "board_rank": None, "board_zt_count": None}
    key = _code_key(code)
    for sector in ranked_sectors:
        name = str(sector.get("name") or "")
        members = set(board_members.get(name, []))
        if key not in members:
            continue
        base["board"] = name
        base["board_rank"] = sector.get("rank")
        zt = 0
        for member in members:
            spot = spot_by_code.get(member)
            chg = _to_f(spot.get("change_pct")) if spot else None
            if chg is not None and chg >= 9.8:
                zt += 1
        base["board_zt_count"] = zt
        return bool(base["board_rank"] is not None and base["board_rank"] <= 5 and zt >= 2), base
    return False, base


# ------------------------------------------------------------------
# 连续五因子评分
# ------------------------------------------------------------------

# 连续五因子评分(量价范式，贴近次日强势 T+1 追涨)
# 因子来自开源量化/券商研报常见量价范式，公式透明、低相关、可用 stock_daily 历史重建：
#   mom_5_1       短中期动量(跳过最近1日隔夜噪声，看真实历史收盘)
#   rel_strength  横截面相对强度(当日涨幅 - 全市场中位数)
#   sr_10         波动率归一收益(近10日日收益均值/标准差，同收益奖波动小)
#   close_vol_corr 量价共振(近20日 close 与成交额的相关，价升量增方向)
#   liq_turnover  流动性质量(流通市值充足 + 换手适中，过滤极端小盘与过度拥挤)
# 权重为经验先验，需用本地历史研究(IC/分层)验证，不代表预测能力。
# IC 校准(2026-09-06, 692 只/1619 交易日)：mom_5_1 是唯一 1 日强正 IC(+0.023, 胜率 54%)→提至 0.30；
# close_vol_corr 双窗口负 IC(1 日 -0.007 / 5 日 -0.018)→由 0.20 大降至 0.10(逆着信号)；
# sr_10 弱正(1 日 +0.007)、5 日转负→降至 0.15。rel_strength/liq_turnover 无法用 OHLCV 回测，维持。
_FACTOR_WEIGHTS = {
    "mom_5_1": 0.30,
    "rel_strength": 0.20,
    "sr_10": 0.15,
    "close_vol_corr": 0.10,
    "liq_turnover": 0.15,
}


def _rank_pct(values: dict[str, float | None]) -> dict[str, float | None]:
    """把横截面原始值转为 0-1 分位；缺失保持 None，避免伪造零分。"""
    valid = [(c, float(v)) for c, v in values.items() if _to_f(v) is not None]
    if not valid:
        return {c: None for c in values}
    ordered = sorted(valid, key=lambda x: x[1])
    n = len(ordered)
    ranks: dict[str, float] = {}
    i = 0
    while i < n:
        j = i + 1
        while j < n and ordered[j][1] == ordered[i][1]:
            j += 1
        # average rank，保证并列值获得相同分数
        pct = ((i + j - 1) / 2) / (n - 1) if n > 1 else 1.0
        for k in range(i, j):
            ranks[ordered[k][0]] = round(pct, 6)
        i = j
    return {c: ranks.get(c) for c in values}


def _weighted_score(factor_maps: dict[str, dict[str, float | None]],
                    codes: list[str]) -> tuple[dict[str, float], dict[str, dict], dict[str, float]]:
    """按可用因子重新归一权重，返 score、逐股因子分和覆盖率。"""
    pct_maps = {name: _rank_pct(vals) for name, vals in factor_maps.items()}
    scores, details, coverage = {}, {}, {}
    total = sum(_FACTOR_WEIGHTS.values())
    for code in codes:
        present = {name: pct_maps[name].get(code)
                   for name in _FACTOR_WEIGHTS
                   if pct_maps[name].get(code) is not None}
        denom = sum(_FACTOR_WEIGHTS[name] for name in present)
        score = (sum(_FACTOR_WEIGHTS[name] * float(value)
                     for name, value in present.items()) / denom * 100
                 if denom else 0.0)
        scores[code] = round(score, 2)
        details[code] = {name: round(float(pct_maps[name][code]) * 100, 2)
                         if pct_maps[name].get(code) is not None else None
                         for name in _FACTOR_WEIGHTS}
        # 兼容旧诊断键(板块助攻)；新因子不再参与评分，但保留键供前端/测试读取。
        details[code].setdefault("board_assist", None)
        coverage[code] = round(denom / total, 4)
    return scores, details, coverage


def _series(mi: dict | None, key: str) -> list:
    """从 ma_info 取历史序列；缺/非 list 返空列表。"""
    v = (mi or {}).get(key)
    return v if isinstance(v, list) else []


def _factor_mom_5_1(s, ma_info: dict | None = None) -> float | None:
    """短中期动量(代理 5-1 动量): 最新收盘相对 5 日前收盘。

    跳过最近 1 日隔夜噪声，看 5 日真实趋势。无历史序列时退化为当日涨幅归一。
    """
    closes = _series(ma_info, "close_series")
    if len(closes) >= 7:
        prev = closes[-6]
        cur = closes[-1]
        if prev and prev > 0:
            return _clip((cur / prev - 0.95) / 0.15)
    chg = _to_f(s.get("change_pct"))
    if chg is None:
        return None
    return _clip((chg + 5.0) / 15.0)


def _factor_relative_strength(s, market_median: float | None = None,
                              ma_info: dict | None = None) -> float | None:
    """横截面相对强度: 个股当日涨幅相对全市场中位数的强弱。"""
    chg = _to_f(s.get("change_pct"))
    if chg is None:
        return None
    base = market_median if market_median is not None else 0.0
    return _clip((chg - base + 5.0) / 10.0)


def _factor_relative_strength_hist(ma_info: dict | None = None,
                                   market_median_ret: float | None = None,
                                   lookback: int = 5) -> float | None:
    """历史可回测相对强度: 过去 lookback 日个股收益 - 市场横截面中位数收益。

    与实时 _factor_relative_strength(当日涨幅相对强度)对称，但基于 stock_daily
    的 close_series，可用 OHLCV 历史重建 → 进 IC/分层研究。线上 nextday 排序
    仍用实时口径；本函数仅供历史研究调用，不接进 _FACTOR_WEIGHTS。
    映射 (ret - market_median_ret + 0.1) / 0.2: 相对市场强弱 ±10% 覆盖 0-1。
    缺历史序列 / 基准 → None(不伪造 0 分)。
    """
    closes = _series(ma_info, "close_series")
    if len(closes) < lookback + 1:
        return None
    prev, cur = closes[-1 - lookback], closes[-1]
    if not (prev and prev > 0) or market_median_ret is None:
        return None
    ret = cur / prev - 1.0
    return _clip((ret - market_median_ret + 0.1) / 0.2)


def _factor_sr_10(s, ma_info: dict | None = None) -> float | None:
    """波动率归一收益: 近10日日收益均值/标准差(夏普式)。

    同收益下奖励低波动(波动大、同等收益分低)。缺序列 → None(不伪造 0)。
    """
    closes = _series(ma_info, "close_series")
    if len(closes) < 11:
        return None
    rets = []
    for i in range(1, len(closes)):
        p, c = closes[i - 1], closes[i]
        if p and p > 0:
            rets.append(c / p - 1.0)
    if len(rets) < 10:
        return None
    rets = rets[-10:]
    m = sum(rets) / len(rets)
    var = sum((r - m) ** 2 for r in rets) / (len(rets) - 1)
    sd = math.sqrt(var)
    if sd <= 0:
        return _clip(m / 0.05)
    return _clip(m / sd / 1.0 + 0.5)


def _factor_close_vol_corr(s, ma_info: dict | None = None) -> float | None:
    """量价共振: 近20日 close 与 amount 的皮尔逊相关 + 价升量增方向。

    相关为正(价升量增)得分高，为负(价升量缩)低分。缺任一序列 → None。
    """
    closes = _series(ma_info, "close_series")
    amounts = _series(ma_info, "amount_series")
    n = min(len(closes), len(amounts))
    if n < 20:
        return None
    closes = closes[-20:]
    amounts = amounts[-20:]
    mc = sum(closes) / len(closes)
    ma = sum(amounts) / len(amounts)
    num = sum((x - mc) * (y - ma) for x, y in zip(closes, amounts))
    den_c = sum((x - mc) ** 2 for x in closes) ** 0.5
    den_a = sum((y - ma) ** 2 for y in amounts) ** 0.5
    if den_c <= 0 or den_a <= 0:
        return 0.5
    corr = num / (den_c * den_a)
    return _clip(corr / 1.0 + 0.5)


def _mv_platform(mv: float) -> float:
    """流通市值分段平台评分: 过小盘低分，中小盘随市值改善，超大市值平台不再单调奖励。

    - <10亿   0→0.5   极端小盘(通常已被 min_mv 硬剔除，此处仅兜底)
    - 10→50亿 0.5→0.9 流动性随市值快速改善
    - 50→200亿 0.9→1.0 次新/活跃中小盘高分(贴合次日强势追涨偏好)
    - >200亿  1.0 平台 流动性已充分，不再额外加分(原 _clip(mv/300) 对超大市值单调奖励)
    """
    if mv < 10:
        return max(mv / 20.0, 0.0)
    if mv < 50:
        return 0.5 + (mv - 10) / 40.0 * 0.4
    if mv < 200:
        return 0.9 + (mv - 50) / 150.0 * 0.1
    return 1.0


def _factor_liq_turnover(s, ma_info: dict | None = None) -> float | None:
    """流动性质量: 流通市值充足 + 换手适中。

    市值分段平台(_mv_platform)过滤极端小盘、对超大市值封顶而非单调加分，
    换手适中(目标 6%)过滤极端小盘与过度拥挤。
    """
    mv, tr = _to_f(s.get("circulating_market_cap")), _to_f(s.get("turnover_rate"))
    if mv is None and tr is None:
        return None
    mv_score = _mv_platform(mv) if mv is not None else 0.5
    tr_score = _clip(1.0 - abs(tr - 6.0) / 10.0) if tr is not None else 0.5
    return 0.6 * mv_score + 0.4 * tr_score



# ------------------------------------------------------------------
# 主流程
# ------------------------------------------------------------------

def nextday_strong_rank(universe: str = "stock",
                        codes: list[str] | None = None,
                        limit: int = 50, days: int = 30,
                        min_change_pct: float = 5.0,
                        min_turnover: float = 3.0,
                        max_price: float = 50.0,
                        min_mv: float = 10.0, max_mv: float = 200.0,
                        max_pe: float = 150.0,
                        exclude_st: bool = True,
                        selection_mode: str = "strict") -> dict:
    """次日强势五因子连续评分，兼容保留旧步骤诊断字段。

    五因子先横截面 rank-pct，再按可用因子重归一加权；旧 step 字段仅用于
    解释筛选条件，不再主导排序。TDX 盘口失败时 quote 因子诚实缺失。
    """
    if selection_mode not in {"strict", "score"}:
        selection_mode = "strict"
    p = {"min_change_pct": min_change_pct, "min_turnover": min_turnover,
         "max_price": max_price, "min_mv": min_mv, "max_mv": max_mv,
         "max_pe": max_pe, "exclude_st": exclude_st,
         "selection_mode": selection_mode}
    key = (universe, tuple(codes or []), limit, days, min_change_pct,
           min_turnover, max_price, min_mv, max_mv, max_pe, exclude_st,
           selection_mode)
    now = datetime.now()
    hit = _CACHE.get(key)
    if hit and (now - hit[0]).total_seconds() < _CACHE_TTL:
        return hit[1]

    base = {"universe": universe, "count": 0, "items": [], "limit": limit,
            "days": days, "filters": p,
            "ts": now.strftime("%Y-%m-%dT%H:%M:%S")}

    spot_all = db.query_rows("stock_spot", limit=0)
    all_spot = list(spot_all)
    # 预加载 ST 名单
    try:
        st_rows = db.query_rows("st_list", limit=0)
        st_map = {str(r.get("code")): r.get("st_type") for r in st_rows}
    except Exception:
        st_map = {}
    st_set = set(st_map)
    if codes:
        cset = {_code_key(c) for c in codes}
        spot_all = [s for s in spot_all
                    if _code_key(s.get("code")) in cset]

    if not spot_all:
        base["note"] = "stock_spot 为空，先 /api/refresh 采集"
        base["market_median_chg"] = None
        _CACHE[key] = (now, base)
        return base

    # 诊断缺失字段(新浪源缺换手率/市值/PE/量比,东财被封时)
    _missing = [f for f in ("turnover_rate", "circulating_market_cap", "pe", "volume_ratio")
                if not any(s.get(f) is not None for s in spot_all)]
    if _missing:
        base["note"] = (f"原始 spot 缺少: {', '.join(_missing)}；"
                        f"本次优先用 TDX 财务概要/行情补全，仍无数据的股票按缺失处理")

    median_chg = _median([_to_f(s.get("change_pct")) for s in all_spot])
    base["market_median_chg"] = _nan(median_chg)

    # 粗筛(codes 限定时不粗筛)
    if not codes:
        # 前端“全部”以 min_change_pct=0 表示，不再隐含涨幅硬过滤；默认
        # 阈值仍用于缩小精算名单，避免全市场 TDX 五档请求过大。
        if min_change_pct <= 0:
            cand = list(spot_all)
        else:
            cand = [s for s in spot_all
                    if (_to_f(s.get("change_pct")) or -99) > min_change_pct]
        cand.sort(key=lambda s: _to_f(s.get("change_pct")) or -99, reverse=True)
        cand = cand[:_SCAN_K]
    else:
        cand = spot_all

    # 从这里开始统一使用纯 6 位代码；否则前缀 spot 会让 MA/step3
    # 的键与后续 item 键分裂，表现为历史形态全部缺失。
    codes_k = [_code_key(s.get("code")) for s in cand]
    # 盘口请求可以批量覆盖全部粗筛候选；财务概要仍限前 N，避免逐股财务调用
    # 把未进入财务小名单的股票误显示为 quote 缺失并影响五因子评分。
    tdx_codes = codes_k[:_TDX_ENRICH_K]
    quote_codes = codes_k

    # step3 批量 MA + 历史量(供量比)；兼容旧测试/mock 只返 ma_info dict
    _ma_result = _ma_arrange_batch(universe, codes_k, days)
    if isinstance(_ma_result, tuple):
        ma_info, hist_amount = _ma_result
    else:
        ma_info, hist_amount = _ma_result, {}
    # 兼容历史 mock/旧调用返回 sh/sz 前缀键，和候选纯代码统一。
    ma_info = {_code_key(k): v for k, v in (ma_info or {}).items()}
    hist_amount = {_code_key(k): v for k, v in (hist_amount or {}).items()}

    # TDX 盘口 + 财务字段：实时行情(五档) + 市值/PE/换手/量比机械补全
    quote_by_code = {}
    fin_by_code = {}
    pure_codes_k = list(dict.fromkeys(_code_key(c) for c in tdx_codes if _code_key(c)))
    pure_quote_codes = list(dict.fromkeys(_code_key(c) for c in quote_codes if _code_key(c)))
    try:
        quote_by_code = {_code_key(q.get("code")): q
                         for q in pytdx_client.get_quote(pure_quote_codes)
                         if q.get("code")}
    except Exception:
        quote_by_code = {}
    # 单股失败不影响其余股票；TDX 服务器偶发返回空/异常时保留已成功结果。
    # 财务概要覆盖全部粗筛候选，确保候选池第 N 只也能有换手率/市值/PE。
    for c in pure_quote_codes:
        try:
            fin_by_code[c] = pytdx_client.get_finance_info(c) or {}
        except Exception:
            fin_by_code[c] = {}

    # 字段补全(值优先合并进 spot；新浪源缺 换手/市值/PE/量比 用 TDX 机械算出)
    spot_by_code_raw = {_code_key(s.get("code")): s for s in all_spot if s.get("code")}
    _rich_map = {_code_key(c): c for c in codes_k}
    enriched = []
    for s in cand:
        k = _code_key(s.get("code"))
        if k in _rich_map:
            # 保留采集快照涨幅供门槛判定；TDX 最新价用于展示与连续评分。
            s = dict(s)
            s["_screen_change_pct"] = s.get("change_pct")
            s = _tdx_rich_enrich(s, quote_by_code, fin_by_code, hist_amount)
        # spot 可能返回 sh/sz 前缀，内部统一使用纯 6 位代码，避免
        # step/MA/板块/TDX 结果分别以不同键查找而导致候选被误判。
        if k:
            s = dict(s)
            s["code"] = k
        enriched.append(s)
    cand = enriched

    # step5 板块助攻：资金流有效时按行业净流入排名；资金流字段为空/不可用时，
    # 改用 TDX 板块成员 + 实时行情涨幅构造机械热度，避免 step5 整列缺失。
    try:
        sff = db.query_rows("sector_fund_flow",
                            where="sector_type = ? AND indicator = ?",
                            params=("行业", "今日"), limit=0)
    except Exception:
        sff = []
    ranked_sectors = _rank_sector_flow(sff)
    spot_by_code = {_code_key(s.get("code")): s for s in all_spot if s.get("code")}
    if not ranked_sectors:
        try:
            tdx_blocks = pytdx_client.get_block_members("industry")
        except Exception:
            tdx_blocks = {}
        ranked_sectors = _rank_tdx_blocks(tdx_blocks, spot_by_code,
                                           quote_by_code)
    top5 = [s["name"] for s in ranked_sectors[:5] if s.get("name")]
    # 批量取前5板块成员列表，优先 TDX，失败再用既有板块源。
    board_members = _board_members_batch(top5) if top5 else {}
    # TDX 行业文件是本地板块/指数混合快照，不能保证覆盖当前候选股。
    # 没有可匹配的板块成员时，step5 仅标记为不可用，不把整批候选误判为失败；
    # 有真实行业资金流或可匹配成员时仍严格执行前5+至少2只涨停门槛。
    candidate_keys = {_code_key(s.get("code")) for s in cand}
    board_gate_available = bool(
        ranked_sectors and any(
            candidate_keys.intersection(_code_key(c) for c in (board_members.get(name) or []))
            for name in top5
        )
    )

    # 先计算每个因子的原始值，再做横截面 rank-pct；不把缺失伪装成 0。
    factor_maps = {name: {} for name in _FACTOR_WEIGHTS}
    details_by_code = {}
    board_by_code = {}
    pass_by_code = {}
    for s in cand:
        code = str(s.get("code"))
        mi = ma_info.get(code, {})
        s5, bd = _step5_pass(code, ranked_sectors, board_members, spot_by_code)
        if not board_gate_available:
            # 板块源不可用时诚实保留空字段，但不让缺失数据阻断其它因子。
            s5 = True
        board_by_code[code] = bd
        pass_by_code[code] = s5
        factor_maps["mom_5_1"][code] = _factor_mom_5_1(s, mi)
        factor_maps["rel_strength"][code] = _factor_relative_strength(s, median_chg)
        factor_maps["sr_10"][code] = _factor_sr_10(s, mi)
        factor_maps["close_vol_corr"][code] = _factor_close_vol_corr(s, mi)
        factor_maps["liq_turnover"][code] = _factor_liq_turnover(s, mi)

    scores, factor_scores, coverage = _weighted_score(factor_maps, codes_k)

    # 雷达共振触发 boost（spec 2026-09-16）：乘法条件叠加，不进 _FACTOR_WEIGHTS。
    # base_score≥50 才吃 boost，低 base 不救；has_data=False / low_liq → 不 boost。
    # 股数通道(mgmt_confirm) 绝不并入 in/out 计数（量纲分离）。
    try:
        _radar = (radar_resonance_for(codes_k)
                  if radar_resonance_for is not None else {})
    except Exception:
        _radar = {}
    _boost_by_code: dict[str, float] = {}
    _radar_in_by_code: dict[str, object] = {}
    _radar_out_by_code: dict[str, object] = {}
    _radar_src_by_code: dict[str, str] = {}
    _radar_mgmt_by_code: dict[str, object] = {}
    _base_scores = dict(scores)
    for _c in codes_k:
        _r = _radar.get(_c) or {}
        _has = _r.get("has_data") and not _r.get("low_liq")
        _in = _r.get("in_count") if _has else None
        _out = _r.get("out_count") if _has else None
        # 股数通道(高管增减持方向)单独标，绝不并入 in/out 计数(量纲分离)；
        # has_data=False/low_liq 时 _r 为空或无该键 → None(无数据语义)。
        _mgmt = _r.get("mgmt_confirm") if _has else None
        _base = scores.get(_c, 0.0)
        _boost = 1.0
        if _in is not None and _base >= 50:
            _boost = 1.0 + 0.05 * max(0, _in - 1)
        scores[_c] = round(_base * _boost, 2)  # 覆盖为 boosted score（排序用）
        _boost_by_code[_c] = _boost
        _radar_in_by_code[_c] = _in
        _radar_out_by_code[_c] = _out
        _radar_mgmt_by_code[_c] = _mgmt
        if not _r or not _r.get("has_data"):
            _radar_src_by_code[_c] = "无主力数据"
        elif _r.get("low_liq"):
            _radar_src_by_code[_c] = "low_liq"
        else:
            _radar_src_by_code[_c] = f"smart_money_action@{_r.get('asof')}"

    items = []
    for s in cand:
        code = str(s.get("code"))
        name = s.get("name") or code
        mi = ma_info.get(code, {})
        bd = board_by_code.get(code, {})
        screen_s = dict(s)
        if screen_s.get("_screen_change_pct") is not None:
            screen_s["change_pct"] = screen_s["_screen_change_pct"]
        s1 = _step1_pass(screen_s, p)
        s2 = _step2_pass(s, p, st_set)
        s3 = _step3_pass(mi)
        s4 = _step4_score(s)
        s5 = pass_by_code.get(code, False)
        hard = sum([s1, s2, s3, s5])
        statuses, reasons = _explain_step_status(s, mi, bd, p, s4, s5)
        items.append({
            "code": code, "name": name,
            "change_pct": _nan(_to_f(s.get("change_pct"))),
            "turnover_rate": _nan(_to_f(s.get("turnover_rate"))),
            "latest_price": _nan(_to_f(s.get("latest_price"))),
            "circulating_market_cap": _nan(_to_f(s.get("circulating_market_cap"))),
            "pe": _nan(_to_f(s.get("pe"))),
            "st_type": st_map.get(code) or s.get("st_type"),
            "volume_ratio": _nan(_to_f(s.get("volume_ratio"))),
            "board": bd.get("board"), "board_rank": bd.get("board_rank"),
            "board_zt_count": bd.get("board_zt_count"),
            "step1_pass": s1, "step2_pass": s2, "step3_pass": s3,
            "step4_score": s4, "step4_pass": s4 > 0,
            "step5_pass": s5,
            "step_status": statuses, "step_reasons": reasons,
            "data_quality": _data_quality(s, mi, code in quote_by_code,
                                          coverage.get(code, 0.0)),
            "hard_pass": hard,
            "failed_steps": [name for name, passed in (
                ("step1", s1), ("step2", s2), ("step3", s3),
                ("step4", s4 > 0), ("step5", s5),
            ) if not passed],
            "need_history": mi.get("need_history", False),
            "factor_scores": factor_scores.get(code, {}),
            "score_coverage": coverage.get(code, 0.0),
            "quote_available": code in quote_by_code,
            "data_source": "tdx" if code in quote_by_code else "stock_spot",
            "base_score": round(_base_scores.get(code, 0.0), 2),
            "radar_resonance_in": _radar_in_by_code.get(code),
            "radar_resonance_out": _radar_out_by_code.get(code),
            "radar_mgmt_confirm": _radar_mgmt_by_code.get(code),
            "radar_boost": _boost_by_code.get(code, 1.0),
            "radar_data_source": _radar_src_by_code.get(code, "无主力数据"),
            "risk_flag": ("主力多通道净流出"
                          if (_radar_out_by_code.get(code) or 0) >= 2 else None),
            "score": scores.get(code, 0.0),
        })

    # step4 为连续量价分，正分即通过；单独保留五步全通过清单。
    passed_items = [it for it in items if (
        it["step1_pass"] and it["step2_pass"] and it["step3_pass"]
        and it["step4_pass"] and it["step5_pass"]
    )]
    passed_items.sort(key=lambda x: (x["score"], x["hard_pass"]), reverse=True)
    base["passed_items"] = passed_items[:max(0, limit)]

    # A: 行业景气/政策命中 穿透标注(仅 passed_items,只读上下文,不改排序)
    if base["passed_items"]:
        _ff, _br, _mm = [], [], {}
        try:
            _ff = db.query_rows("sector_fund_flow",
                                where="sector_type='行业' AND indicator='今日'",
                                order_by="", limit=0)
            _br = db.query_rows("industry_board", order_by="", limit=0)
            _ff_names = {str(r.get("name")) for r in _ff if r.get("name")}
            _scored = [str(r.get("name")) for r in _br
                       if r.get("name") and str(r.get("name")) in _ff_names]
            _mm = {b: set(c) for b, c in _board_members_batch(_scored).items()}
        except Exception:
            _ff, _br, _mm = [], [], {}
        _sh.attach_sector_heat(base["passed_items"], _ff, _br, _mm)

    # B2: 主力阶段/资金连续性 穿透标注(复用 quality._enrich_main_behavior,
    #     lazy import 避循环依赖;universe!=stock 时函数内直接返回原行)
    if base["passed_items"] and universe == "stock":
        try:
            from backtest import quality as _q
            base["passed_items"] = _q._enrich_main_behavior(
                base["passed_items"], universe, days=days)
        except Exception:
            pass  # 富集失败→字段 None(诚实缺失)

    diagnostic_items = sorted(
        items,
        key=lambda x: (x["hard_pass"], x["score"], x["score_coverage"],
                       x.get("change_pct") or -99),
        reverse=True,
    )
    base["all_items"] = diagnostic_items[:max(0, limit)]
    base["rejected_items"] = [it for it in diagnostic_items if it not in passed_items]
    if selection_mode == "score":
        selected_items = sorted(items, key=lambda x: (x["score"], x["score_coverage"]), reverse=True)
        base["selection_mode"] = "score"
    elif base["passed_items"]:
        base["selection_mode"] = "strict"
        selected_items = base["passed_items"]
    else:
        base["selection_mode"] = "fallback"
        base["note"] = "当前数据条件下没有五步全部通过的股票，以下为接近通过的观察清单"
        selected_items = diagnostic_items
    items = selected_items[:max(0, limit)]
    for i, it in enumerate(items):
        it["rank"] = i + 1

    base["count"] = len(items)
    base["items"] = items
    _CACHE[key] = (now, base)
    return base


def _median(values):
    xs = [v for v in values if v is not None]
    if not xs:
        return None
    return float(np.median(xs))