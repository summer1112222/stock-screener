# -*- coding: utf-8 -*-
"""市场温度采集：涨跌停/炸板/连板/两融/估值分位。

合规：本层只采集公开市场状态事实（涨跌家数、涨停数、两融余额、估值分位），
      不做择时判断、不输出"该加仓减仓"、不承诺收益。措辞"市场状态机械观察"。
稳定性：每路采集返回 (df, ok, err)，网络/接口异常不抛崩；失败标不可用不崩。
NaN→None：legu/margin/pe 返回 DataFrame 有 NaN，出口统一 None。
数据源选择（可靠性优先，非东财源不被当前出口 IP 封禁）：
  - 涨跌家数比：stock_spot 表（已入库），零采集，SQL CASE 现算。
  - 涨停/跌停/炸板/连板高度：stock_market_activity_legu（乐估，非东财）；
    备援 stock_zt_pool_em / stock_zt_pool_dtgc_em / stock_zt_pool_zbgc_em（东财，可能被封）。
  - 两融余额：stock_margin_detail_sse + stock_margin_detail_szse（交易所官方）。
  - PE/PB 及分位：stock_market_pe_lg（乐估历史）。
注意：host 无 akshare，下列 AKShare 函数名需在 Docker 容器内 hasattr 校验；
      缺失/失败即该路 ok=False 灰显（复用 stale 降级模式，不崩）。
"""
from __future__ import annotations

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


# ------------------------------------------------------------------
# 涨跌家数比：纯 DB，零采集
# ------------------------------------------------------------------
def _updown_from_spot():
    """从已入库 stock_spot 表算当日上涨/下跌家数。spot 无当日则返回 None。"""
    try:
        with db.get_conn() as c:
            up = c.execute(
                "SELECT COUNT(*) AS n FROM stock_spot WHERE change_pct>0"
            ).fetchone()["n"]
            down = c.execute(
                "SELECT COUNT(*) AS n FROM stock_spot WHERE change_pct<0"
            ).fetchone()["n"]
        return {"up_count": up, "down_count": down, "_db_ok": True}
    except Exception:
        return {"up_count": None, "down_count": None, "_db_ok": False}


# ------------------------------------------------------------------
# 乐估市场活跃度（涨停/跌停/炸板/连板高度）
# ------------------------------------------------------------------
def _fetch_legu():
    """乐估综合市场活跃度。非东财域名。失败返回 (None, False, err)。"""
    if not _AK_OK:
        return None, False, _AK_ERR or "akshare 不可用"
    if not hasattr(ak, "stock_market_activity_legu"):
        return None, False, "akshare 无 stock_market_activity_legu"
    try:
        df = ak.stock_market_activity_legu()
        return df, True, ""
    except Exception as e:
        return None, False, f"legu: {e}"


def _scalar(df, keywords):
    """从 df 第一行按列名关键词取标量，NaN→None。列名容错：含任一关键词即命中。"""
    if df is None or len(df) == 0:
        return None
    try:
        for col in df.columns:
            if any(k in str(col) for k in keywords):
                v = df.iloc[0][col]
                if pd.isna(v):
                    return None
                # 去百分号/单位转 float
                if isinstance(v, str):
                    v = v.replace("%", "").replace("亿", "").strip()
                    try:
                        return float(v)
                    except ValueError:
                        return v
                return float(v)
    except Exception:
        return None
    return None


def _parse_legu(df):
    """解析乐估 df → zt/dt/zbgc/lb_max。列名需实测容错（host 无 akshare 无法预填）。"""
    if df is None:
        return {}
    return {
        "zt_count": _scalar(df, ["涨停数量", "涨停家数", "涨停"]),
        "dt_count": _scalar(df, ["跌停数量", "跌停家数", "跌停"]),
        "zbgc_count": _scalar(df, ["炸板", "开板"]),
        "lb_max": _scalar(df, ["连板数", "连板高度", "最高连板", "连板"]),
    }


# ------------------------------------------------------------------
# 两融余额（沪深合计 + 环比）
# ------------------------------------------------------------------
def _fetch_margin():
    """沪深两融余额合计。交易所官方域名。返回 (total, ok, err)。"""
    if not _AK_OK:
        return None, False, _AK_ERR or "akshare 不可用"
    total = 0.0
    got = 0
    errs = []
    for fn_name in ("stock_margin_detail_sse", "stock_margin_detail_szse"):
        if not hasattr(ak, fn_name):
            errs.append(f"无{fn_name}")
            continue
        try:
            df = getattr(ak, fn_name)()
            # 取最新一行的融资余额合计；列名容错
            v = _scalar(df, ["融资余额"])
            if v is not None:
                total += v
                got += 1
        except Exception as e:
            errs.append(f"{fn_name}: {e}")
    if got == 0:
        return None, False, ";".join(errs) or "margin 全失败"
    return total, True, ""


# ------------------------------------------------------------------
# 估值 PE/PB 及分位
# ------------------------------------------------------------------
def _fetch_valuation():
    """市场 PE/PB 历史分位（乐估）。返回 (pe, pb, pe_pct, ok, err)。"""
    if not _AK_OK:
        return None, None, None, False, _AK_ERR or "akshare 不可用"
    if not hasattr(ak, "stock_market_pe_lg"):
        return None, None, None, False, "akshare 无 stock_market_pe_lg"
    try:
        df = ak.stock_market_pe_lg()
        if df is None or len(df) == 0:
            return None, None, None, False, "pe_lg 空"
        pe = _scalar(df, ["市盈率", "PE"])
        pb = _scalar(df, ["市净率", "PB"])
        pe_pct = _calc_percentile(df, ["市盈率", "PE"], pe)
        return pe, pb, pe_pct, True, ""
    except Exception as e:
        return None, None, None, False, f"pe_lg: {e}"


def _calc_percentile(df, keywords, current):
    """当前值在 df 该列历史序列的分位(0-1)。NaN→None。"""
    if current is None or df is None:
        return None
    try:
        for col in df.columns:
            if any(k in str(col) for k in keywords):
                s = pd.to_numeric(df[col], errors="coerce").dropna()
                if len(s) == 0:
                    return None
                rank = (s <= current).sum()
                return round(rank / len(s), 4)
    except Exception:
        return None
    return None


# ------------------------------------------------------------------
# 编排
# ------------------------------------------------------------------
_MARKET_COLS = ("date", "up_count", "down_count", "zt_count", "dt_count",
                "zbgc_count", "lb_max", "margin_total", "margin_chg",
                "pe", "pb", "pe_pct", "src_ok", "err", "ts")


def collect_temperature():
    """编排 4 路采集，返回单日记录 dict（含 ok 标志）。单路失败不崩。"""
    today = datetime.now().strftime("%Y-%m-%d")
    rec = {"date": today,
           "up_count": None, "down_count": None,
           "zt_count": None, "dt_count": None, "zbgc_count": None, "lb_max": None,
           "margin_total": None, "margin_chg": None,
           "pe": None, "pb": None, "pe_pct": None,
           "src_ok": 1, "err": "", "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}

    errs = []

    # 路1：涨跌家数（纯 DB）
    up = _updown_from_spot()
    rec["up_count"] = up["up_count"]
    rec["down_count"] = up["down_count"]
    db_ok = up["_db_ok"]
    if not db_ok:
        errs.append("spot DB 失败")

    # 路2：乐估活跃度
    df, legu_ok, legu_err = _fetch_legu()
    if legu_ok:
        rec.update(_parse_legu(df))
    else:
        errs.append(legu_err)

    # 路3：两融
    margin, mgn_ok, mgn_err = _fetch_margin()
    if mgn_ok:
        rec["margin_total"] = margin
        prev = _prev_margin(today)
        rec["margin_chg"] = round(margin - prev, 2) if prev is not None else None
    else:
        errs.append(mgn_err)

    # 路4：估值
    pe, pb, pe_pct, val_ok, val_err = _fetch_valuation()
    if val_ok:
        rec["pe"], rec["pb"], rec["pe_pct"] = pe, pb, pe_pct
    else:
        errs.append(val_err)

    rec["ok"] = db_ok or legu_ok or mgn_ok or val_ok
    rec["err"] = " | ".join(e for e in errs if e)[:200]
    if not rec["ok"]:
        rec["src_ok"] = 0

    _upsert(rec)
    return rec


def _prev_margin(today):
    """读 market_daily 前一交易日的 margin_total（供环比）。"""
    try:
        with db.get_conn() as c:
            r = c.execute(
                "SELECT margin_total FROM market_daily "
                "WHERE date < ? AND margin_total IS NOT NULL "
                "ORDER BY date DESC LIMIT 1", (today,)
            ).fetchone()
        return r["margin_total"] if r else None
    except Exception:
        return None


def _upsert(rec):
    """写 market_daily（date 主键 ON CONFLICT 更新）。"""
    try:
        with db.get_conn() as c:
            c.execute(
                """INSERT INTO market_daily(date,up_count,down_count,zt_count,
                   dt_count,zbgc_count,lb_max,margin_total,margin_chg,pe,pb,pe_pct,
                   src_ok,err,ts)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(date) DO UPDATE SET
                   up_count=excluded.up_count, down_count=excluded.down_count,
                   zt_count=excluded.zt_count, dt_count=excluded.dt_count,
                   zbgc_count=excluded.zbgc_count, lb_max=excluded.lb_max,
                   margin_total=excluded.margin_total, margin_chg=excluded.margin_chg,
                   pe=excluded.pe, pb=excluded.pb, pe_pct=excluded.pe_pct,
                   src_ok=excluded.src_ok, err=excluded.err, ts=excluded.ts""",
                tuple(rec.get(k) for k in _MARKET_COLS))
            c.commit()
    except Exception:
        pass  # 写库失败不崩（采集本身已返回 rec）


# ------------------------------------------------------------------
# 只读查询
# ------------------------------------------------------------------
def latest():
    """最新一行 market_daily，无数据返回 None。"""
    try:
        with db.get_conn() as c:
            r = c.execute(
                "SELECT * FROM market_daily ORDER BY date DESC LIMIT 1"
            ).fetchone()
        return dict(r) if r else None
    except Exception:
        return None


def trend(days=30):
    """近 N 日 market_daily（升序，供 sparkline）。"""
    try:
        with db.get_conn() as c:
            rows = c.execute(
                f"SELECT * FROM market_daily ORDER BY date DESC LIMIT {int(days)}"
            ).fetchall()
        return list(reversed([dict(r) for r in rows]))
    except Exception:
        return []
