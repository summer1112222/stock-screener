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
import datetime as _dt
import pandas as pd

from data import db

_SPOT_TABLE = {"stock": "stock_spot", "etf": "etf_spot"}
_CAND_DISCLAIMER = ("多口径共振机械排序观察清单，非荐股非买卖信号，"
                    "不构成投资建议、不承诺收益。盘口微结构为实时供求机械观察，"
                    "非买卖信号；A/B 收盘后失效。市场有风险，盈亏自负。")
# 结果级缓存(5min TTL)：quality_rank 计算重(历史+buffett+signals)，避免短时重复重算
_RESULT_CACHE: dict = {}
_RESULT_TTL = 300.0  # 秒
# 共振默认经验权重(因子有效性先验,非 IC 校准,可被 weights/resonance_mode 覆盖)：
# 口径2 价值质量/5 景气(高持久) > 口径1 风险调整 > 口径4 多信号 > 口径3 资金流(最噪声)。
# 加权均值不要求和归一(wsum/wtot 已处理)。
_DEFAULT_DIM_WEIGHTS = {1: 1.0, 2: 1.3, 3: 0.6, 4: 0.7, 5: 1.3}

ETF_BENCHMARK_MAP = {
    "510300": "sh000300", "510310": "sh000300", "510160": "sh000300",
    "510050": "sh000016", "510500": "sh000905", "588000": "sh000688",
    "512100": "sh000852", "159915": "sz399006",
}


def _is_in_session(now: "_dt.datetime | None" = None) -> bool:
    """best-effort 判 A 股交易时段：周一至周五 9:30-11:30 / 13:00-15:00。
    节假日无历：误判盘中时 get_quote 返回收盘盘口，上层降级为盘后语义，不崩。
    now=None 取当前本地时间；测试可注入 mock datetime。"""
    now = now or _dt.datetime.now()
    if now.weekday() >= 5:  # 周六5/周日6
        return False
    t = now.hour * 100 + now.minute
    return (930 <= t <= 1130) or (1300 <= t <= 1500)


def _refine_by_quote(pool: list, df_spot, in_session: bool):
    """对小名单 get_quote 取盘口，算 A 流动性深度(+综合分重排) + B/C raw 展示。
    B/C 方向不进排序（合规：方向=择时信号非质量）。
    返回 (pool, refine_status, quote_by_code)。pool 原地附加 quote 字段。
    盘中：按 _refine_score=0.6*resonance_pct+0.4*liquidity_pct 重排。
    盘后：A/B=None + note，仅 C 全天内外盘，不重排。
    get_quote 失败：refine_status=err，pool 不变。"""
    import math
    from data import pytdx_client
    codes = [str(it.get("code")) for it in pool]
    try:
        quotes = {str(q.get("code")): q for q in pytdx_client.get_quote(codes)}
    except Exception:
        return pool, "err:通达信不可用,跳过精排", {}
    if not quotes:
        return pool, "err:通达信不可用,跳过精排", {}

    depths, brs, iors = {}, {}, {}
    for c in codes:
        q = quotes.get(c) or {}
        bv = [q.get(f"bid_vol{i}") for i in range(1, 6)]
        av = [q.get(f"ask_vol{i}") for i in range(1, 6)]
        bid_sum = sum((x or 0) for x in bv)
        ask_sum = sum((x or 0) for x in av)
        tot = bid_sum + ask_sum
        depths[c] = math.log(tot) if tot > 0 else None
        brs[c] = (bid_sum / tot) if tot > 0 else None
        b_vol = q.get("b_vol")
        s_vol = q.get("s_vol")
        iors[c] = (b_vol / s_vol) if (b_vol and s_vol and s_vol != 0) else None

    def _quote_dict(c):
        lp = lpct.get(c) if in_session else None
        return {
            "liquidity_depth": _to_float(depths.get(c)) if in_session else None,
            "bid_ask_ratio": _to_float(brs.get(c)) if in_session else None,
            "inner_outer_ratio": _to_float(iors.get(c)),
            "liquidity_pct": _to_float(lp) if in_session else None,
            "in_session": in_session,
            **({} if in_session else {"note": "收盘挂单,A/B失效"}),
        }

    lpct = {}
    if in_session:
        ds = pd.Series({c: depths[c] for c in codes if depths.get(c) is not None})
        lpct = _to_pct(ds).to_dict() if not ds.empty else {}
        rs = pd.Series({str(it.get("code")): it.get("resonance") or 0 for it in pool})
        rpct = _to_pct(rs).to_dict() if not rs.empty else {}
        for it in pool:
            c = str(it.get("code"))
            lp = lpct.get(c, 0.0)
            rp = rpct.get(c, 0.0)
            it["_refine_score"] = 0.6 * _to_float(rp) + 0.4 * _to_float(lp)
            it["quote"] = _quote_dict(c)
        pool.sort(key=lambda x: x.get("_refine_score") or 0.0, reverse=True)
    else:
        for it in pool:
            it["quote"] = _quote_dict(str(it.get("code")))

    status = "ok(盘中)" if in_session else "ok(盘后,仅C展示)"
    return pool, status, {c: _quote_dict(c) for c in codes}


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


def _avg_rank_pct(factors: list, codes: list) -> pd.Series:
    """每个因子先转横截 rank-pct(_to_pct)，再按 code 取可用因子的均值。

    缺失(None/NaN)因子跳过、不拖累该 code。比 zscore 等权更抗异常值
    （涨停极端动量不再主导，rank 天然夹在 [0,1]），且支持异质缺失
    （不同 code 命中不同因子集时仍可比较）。返回 code->pct Series（index=codes）。
    全因子缺失的 code → NaN（_to_float 转 None，不计命中）。"""
    if not factors:
        return pd.Series(dtype=float, index=codes)
    pct = pd.DataFrame({
        f"_{i}": _to_pct(pd.to_numeric(f, errors="coerce")).reindex(codes)
        for i, f in enumerate(factors)
    })
    return pct.mean(axis=1, skipna=True)


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


def _dim_scores(df, universe, days, min_signals, close=None):
    """四口径分位。返回 (code->{dim:pct}, dims_available, dim_status)。
    任一因子源失败→该口径 err、分位 None、不崩。
    close=预加载的历史收盘(quality_rank 传，避免口径1/相关性/最小方差各重载一次)。"""
    codes = df["code"].astype(str).tolist() if "code" in df.columns else []
    scores = {c: {} for c in codes}
    dims_avail, status = [], {}

    # 口径1 风险调整(历史)：波动率(负)/动量/夏普/最大回撤(负)
    try:
        import backtest.eval as bt_eval
        if close is None:
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
            elif bt_buf.akshare_blocked():
                # akshare 财报接口熔断(连续失败):跳 analyze_many 省 40s 白烧,
                # 下方 spot估值代理块接管(status 非 "ok" 且无 scores[2]→进降级)
                status["2"] = "skip(akshare熔断→spot估值代理)"
            else:
                # 性能：不全市场逐股拉 buffett，先 shortlist 缩到 ≤80 只
                # （spec §7.5）。非 shortlist 标的口径2 分位=None。
                try:
                    sl = set(bt_buf.shortlist_by_turnover(min_turnover=5e8, k=80))
                    sl_codes = [c for c in codes if c in sl]
                except Exception:
                    sl_codes = codes
                results = bt_buf.analyze_many(sl_codes, deadline_s=40.0)  # 总体40s:akshare慢时返部分分位而非150s挂起;首次<75s前端超时,5min内重算走财报7天缓存秒回

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
                # 丰富因子(B):纳 buffett 已算的 ROIC/安全边际/盈利含金量,不增网络调用
                roic = pd.Series({r["code"]: (r.get("ratios") or {}).get("roic") for r in results}).dropna()
                mos = pd.Series({r["code"]: r.get("margin_of_safety") for r in results}).dropna()
                oet = pd.Series({r["code"]: (r.get("ratios") or {}).get("owner_earnings_to_ni")
                                 for r in results}).dropna()
                comp = _avg_rank_pct([ey, moat, lroe, roic, mos, oet], codes)
                for c in codes:
                    scores[c][2] = _to_float(comp.get(c)) if c in comp.index else None
                dims_avail.append(2)
                status["2"] = "ok"
        except Exception as e:
            status["2"] = f"err:{e}"
        # 降级：buffett 主路径失败(_AK_OK=False / analyze_many 空 / 抛异常) → spot 估值代理
        # 触发条件：status=err，或主路径"成功"但未产出任何分位（空 shortlist/空 results）
        if status.get("2", "").startswith("err") or not any(
                scores.get(c, {}).get(2) is not None for c in codes):
            try:
                idx = df["code"].astype(str)
                pe = pd.to_numeric(df.get("pe"), errors="coerce").set_axis(idx)
                pb = pd.to_numeric(df.get("pb"), errors="coerce").set_axis(idx)
                amp = pd.to_numeric(df.get("amplitude"), errors="coerce").set_axis(idx)
                tr = pd.to_numeric(df.get("turnover_rate"), errors="coerce").set_axis(idx)
                comp = (_zscore(-pe) + _zscore(-pb) + _zscore(-amp) + _zscore(tr)) / 4
                pct = _to_pct(comp)
                for c in codes:
                    scores[c][2] = _to_float(pct.get(c)) if c in pct.index else None
                if 2 not in dims_avail:
                    dims_avail.append(2)
                status["2"] = "ok(降级spot估值代理)"
            except Exception as e2:
                status["2"] = f"err:buffett与spot代理均失败:{e2}"
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

    # 口径3 资金流向(改造A:资金流连续性+北向累计+边际加速,低相关替代高相关三因子;
    #               限 shortlist,非 shortlist 口径3=None;降级旧 spot 路径)
    try:
        import screener.smart_money as sm_q
        used_new = False
        if universe == "stock":
            try:
                import backtest.buffett as bt_buf
                sl = set(bt_buf.shortlist_by_turnover(min_turnover=5e8, k=80))
                sl_codes = [c for c in codes if c in sl]
            except Exception:
                sl_codes = codes
            bb = sm_q._behavior_batch(sl_codes, days=days)
            # 任一 shortlist 标的有行为序列 → 用新口径(连续性+北向+边际)
            if any(bb[c]["streak_inflow"] is not None or bb[c]["north_cum"] is not None
                   or bb[c]["margin_accel"] is not None for c in sl_codes):
                f1 = pd.Series({c: (bb[c]["streak_inflow"] or 0) for c in sl_codes})   # 资金流连续性
                f2 = pd.Series({c: bb[c]["north_cum"] for c in sl_codes})            # 北向累计(聪明资金)
                f3 = pd.Series({c: bb[c]["margin_accel"] for c in sl_codes})         # 边际加速
                comp = _avg_rank_pct([f1, f2, f3], codes)
                for c in codes:
                    scores[c][3] = _to_float(comp.get(c)) if c in comp.index else None
                dims_avail.append(3)
                status["3"] = "ok(连续性+北向+边际)"
                used_new = True
        if not used_new:
            # 降级旧路径:top_by_amount累计+spot当日净额+换手(ETF/无行为序列,高相关)
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
            status["3"] = "ok(降级spot)" if sm.get("rows") else "ok(仅spot)"
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

    # 口径5 景气成长(仅个股,shortlist 限定,研报覆盖/评级偏多/目标价上行空间)
    if universe == "stock":
        try:
            from data.research import query_reports
            # shortlist 限定(与口径2/3 一致):非 shortlist 口径5=None
            try:
                import backtest.buffett as bt_buf
                sl5 = set(bt_buf.shortlist_by_turnover(min_turnover=5e8, k=80))
                sl5_codes = [c for c in codes if c in sl5]
            except Exception:
                sl5_codes = codes
            spot_price = {}
            if "latest_price" in df.columns:
                lp = pd.to_numeric(df["latest_price"], errors="coerce")
                spot_price = dict(zip(df["code"].astype(str), lp))
            cov, bull, upside = {}, {}, {}
            any_rows = False
            for c in sl5_codes:
                rpt = query_reports(c, days=days)["rows"]
                if not rpt:
                    continue
                any_rows = True
                cov[c] = len(rpt)
                nbull = sum(1 for r in rpt
                            if (r.get("rating") or "") in ("买入", "增持", "推荐", "强推"))
                bull[c] = nbull / max(len(rpt), 1)
                # 目标价上行空间 = mean(tp/spot - 1)
                ups = []
                for r in rpt:
                    tp = r.get("target_price")
                    sp = spot_price.get(c)
                    try:
                        tp = float(tp); sp = float(sp)
                        if tp > 0 and sp > 0:
                            ups.append(tp / sp - 1.0)
                    except (TypeError, ValueError):
                        continue
                if ups:
                    upside[c] = float(np.mean(ups))
            if any_rows:
                cov_s = pd.Series(cov)
                bull_s = pd.Series(bull)
                up_s = pd.Series(upside)
                comp = _avg_rank_pct([cov_s, bull_s, up_s], codes)
                for c in codes:
                    scores[c][5] = _to_float(comp.get(c)) if c in comp.index else None
                dims_avail.append(5)
                status["5"] = "ok(景气:覆盖/评级/目标价)"
            else:
                status["5"] = "ok(降级:无研报)"
        except Exception as e:
            status["5"] = f"err:{e}"

    return scores, dims_avail, status


def _resonance(dim_scores, dim_thresh, weights=None, mode="greedy"):
    """resonance = 多口径共振机械分。返回 (resonance, hits)。
    dim_scores: {dim: pct}，pct 可能 None（None 不算命中不计分母）。
    weights 默认 _DEFAULT_DIM_WEIGHTS(经验有效性,非 IC 校准)。
    mode='greedy'(默认): hits×10 + 命中口径加权平均分位(数量偏好,共识优先)。
    mode='penalize': 命中口径加权几何均值(短板惩罚,某口径极低会拉低总分;
                     pct 裁剪 [0.01,1] 防 log0)。hits 仍独立返回供 min_dims 门槛。
    合规:resonance 是"多口径同靠前"机械事实陈述,非收益预测/推荐强度。"""
    w = _DEFAULT_DIM_WEIGHTS if weights is None else weights
    present, hit_pcts = [], []   # present=非 None; hit_pcts=≥thresh
    hits = 0
    for d, pct in dim_scores.items():
        if pct is None:
            continue
        present.append((d, float(pct)))
        if pct >= dim_thresh:
            hits += 1
            hit_pcts.append((d, float(pct)))
    if not present:
        return 0.0, hits
    if mode == "penalize":
        # 加权几何均值(over 所有 present 非None 口径):某口径极低会拉低总分;
        # pct 裁剪 [0.01,1] 防 log0。hits 独立返回供 min_dims 门槛。
        import math as _math
        num, den = 0.0, 0.0
        for d, p in present:
            pc = min(max(p, 0.01), 1.0)
            wi = w.get(d, 1.0)
            num += wi * _math.log(pc)
            den += wi
        geo = _math.exp(num / den) if den > 0 else 0.0
        return round(geo, 6), hits
    # greedy(默认):hits×10 + 命中口径(≥thresh)加权平均分位(数量偏好,共识优先)
    if not hit_pcts:
        return round(float(hits) * 10, 6), hits
    wsum, wtot = 0.0, 0.0
    for d, p in hit_pcts:
        wi = w.get(d, 1.0)
        wsum += p * wi
        wtot += wi
    avg = (wsum / wtot) if wtot > 0 else 0.0
    return round(hits * 10 + avg, 6), hits


def _board_of(code, universe, df_spot, board_map=None):
    """best-effort 取行业/板块：spot 的 board 列 → board_map(预加载) → industry_board 成分兜底。
    board_map=预加载的 {code:board} 映射，避免每候选查一次 industry_board 全表。"""
    if "board" in df_spot.columns:
        row = df_spot[df_spot["code"].astype(str) == str(code)]
        if not row.empty and pd.notna(row["board"].iloc[0]):
            return str(row["board"].iloc[0])
    if board_map and str(code) in board_map:
        return board_map[str(code)]
    if universe == "stock":
        try:
            for b in db.query_rows("industry_board"):
                members = b.get("members") or b.get("stocks") or []
                if str(code) in [str(m) for m in members]:
                    return b.get("name", "未知")
        except Exception:
            pass
    return "未知"


def _corr_matrix(universe, codes, close=None):
    """候选集收益率相关矩阵。无历史返回 None。
    close=预加载(quality_rank 传，避免重载)。"""
    try:
        import backtest.eval as bt_eval
        if close is None:
            close = bt_eval.load_panel(universe, codes, "1990-01-01", "2099-12-31", "close")
        else:
            close = close.reindex(columns=codes)
        if close is None or close.empty:
            return None
        return close.pct_change().corr()
    except Exception:
        return None


def _min_var_weights(codes: list[str], universe: str, close=None) -> list[float]:
    """最小方差权重解析解 w = Σ⁻¹·1 / (1ᵀ·Σ⁻¹·1)。Σ 奇异降级 1/方差。long-only 归零归一。
    close=预加载(quality_rank 传，避免重载)。
    合规：风险预算机械分配，非推荐仓位。
    额外防护：numpy 对奇异矩阵可能不抛 LinAlgError 而返回含 inf/极大值，
    此时一并降级 1/方差。
    """
    try:
        import backtest.eval as bt_eval
        if close is None:
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
                 combo_method="greedy", close=None, board_map=None):
    """贪心：按 resonance 降序，行业≤max_per_board + 相关性≤max_corr。
    combo_method: "greedy" 等权（默认）；"min_var" 最小方差权重（风险预算机械分配，非推荐仓位）。
    close/board_map=预加载(quality_rank 传，避免 _corr_matrix/_min_var_weights 重载历史、
    _board_of 每候选查 industry_board 全表)。"""
    corr = _corr_matrix(universe, [r["code"] for r in main], close=close) if max_corr > 0 else None
    kept, board_cnt = [], {}
    for it in main:
        b = _board_of(it["code"], universe, df_spot, board_map)
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
        ws = _min_var_weights([it["code"] for it in kept], universe, close=close)
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
                 dim_thresh=0.7, min_turnover=5e7, max_per_board=3,
                 max_corr=0.85, limit=20, min_signals=2, limit_pct=9.9,
                 combo_method: str = "greedy",
                 resonance_mode: str = "greedy",
                 refine: bool = True, refine_pool: int = 50) -> dict:
    """优质筛选主入口。返回 {main, by_dim, dims_available, dim_status,
    min_dims, refine_status, cand_disclaimer, error}。口径分位见 _dim_scores；
    共振/组合见 _resonance/_apply_combo。combo_method: "greedy" 等权（默认），
    "min_var" 最小方差权重（风险预算机械分配，非推荐仓位）。
    resonance_mode: "greedy"(默认,hits×10+加权均值,数量偏好) / "penalize"(几何均值,短板惩罚)。
    dim_thresh 默认 0.7(提区分度)。weights 默认 _DEFAULT_DIM_WEIGHTS(经验,非IC校准)。
    refine: 仅个股，盘口精排（盘中按流动性+共振重排 refine_pool 只，盘后仅附 quote 不重排）。"""
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
    # 结果缓存：盘中 30s TTL（盘口精排需高频刷新），盘后 5min（计算重避免重复重算）
    import time as _time
    in_session = _is_in_session()
    _key = (universe, days, min_dims, dim_thresh, min_turnover, max_per_board,
            max_corr, limit, min_signals, limit_pct, combo_method, resonance_mode,
            refine, refine_pool, in_session)
    _now = _time.time()
    _ttl = 30.0 if in_session else _RESULT_TTL
    _hit = _RESULT_CACHE.get(_key)
    if _hit and _now - _hit[0] < _ttl:
        return _hit[1]
    codes = df["code"].astype(str).tolist() if "code" in df.columns else []
    # 性能：历史收盘只加载一次，供口径1/相关性/最小方差共用(原3次→1次)
    try:
        import backtest.eval as bt_eval
        close = bt_eval.load_panel(universe, codes, "1990-01-01", "2099-12-31", "close")
    except Exception:
        close = None
    # 性能：预加载 industry_board 映射，避免 _board_of 每候选查全表(N→1)
    board_map = {}
    if universe == "stock":
        try:
            for b in db.query_rows("industry_board"):
                members = b.get("members") or b.get("stocks") or []
                nm = b.get("name", "未知")
                for m in members:
                    board_map.setdefault(str(m), nm)
        except Exception:
            pass
    scores, dims_avail, dim_status = _dim_scores(df, universe, days, min_signals, close=close)
    eff_min_dims = min(min_dims, len(dims_avail)) if dims_avail else 0
    enriched, by_dim = [], {d: [] for d in (1, 2, 3, 4, 5)}
    for c in codes:
        ds = scores.get(c, {})
        res, hits = _resonance(ds, dim_thresh, weights, resonance_mode)
        name = df.loc[df["code"].astype(str) == c, "name"].iloc[0] \
            if "name" in df.columns else c
        item = {"code": c, "name": name, "resonance": _to_float(res),
                "hits": hits, "dim_scores": ds, "reasons": []}
        enriched.append(item)
        for d in (1, 2, 3, 4, 5):
            if ds.get(d) is not None:
                by_dim[d].append({**item, "_pct": ds[d]})
    for d in by_dim:
        by_dim[d].sort(key=lambda x: x.get("_pct") or 0, reverse=True)
        by_dim[d] = [{k: v for k, v in x.items() if k != "_pct"} for x in by_dim[d]]
    main = [it for it in enriched if it["hits"] >= eff_min_dims]
    main.sort(key=lambda x: x["resonance"] or 0, reverse=True)

    # 盘口精排阶段（仅个股 + refine）
    if not refine:
        refine_status = "skip(refine=False)"
    elif universe != "stock":
        refine_status = "skip(ETF不精排)"
    else:
        refine_status = "skip(无可精排候选)"  # stock+refine 但 main 空
    quote_by_code = {}
    if universe == "stock" and refine and main:
        pool = main[:refine_pool]
        pool, refine_status, quote_by_code = _refine_by_quote(
            pool, df, in_session=in_session)
        main = pool  # 精排重排后的 top refine_pool 直接作为组合层输入

    main = _apply_combo(main, universe, df, max_per_board, max_corr, limit,
                        combo_method=combo_method, close=close, board_map=board_map)

    def _clean_item(it):
        it["reasons"] = _build_reasons(it)
        it["dim_scores"] = {d: _to_float(v) for d, v in it.get("dim_scores", {}).items()}
        it["resonance"] = _to_float(it.get("resonance"))
        it.pop("_refine_score", None)  # 内部键清理
        return it

    main = [_clean_item(it) for it in main]
    by_dim = {d: [_clean_item(it) for it in lst] for d, lst in by_dim.items()}
    result = {"main": main, "by_dim": by_dim, "dims_available": dims_avail,
              "dim_status": dim_status, "min_dims": eff_min_dims,
              "refine_status": refine_status,
              "source_health": {str(d): dim_status.get(str(d), "") for d in (1, 2, 3, 4, 5)},
              "cand_disclaimer": _CAND_DISCLAIMER, "error": None}
    _RESULT_CACHE[_key] = (_now, result)
    return result
