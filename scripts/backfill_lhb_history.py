# -*- coding: utf-8 -*-
"""一次性龙虎榜历史回填: finshare get_lhb 拉过去N月落 smart_money_action。
幂等(UNIQUE upsert 覆盖),可重复跑。失败不崩,标 source=finshare。
用法: python -m scripts.backfill_lhb_history  [months=6] [batch_days=30]
不进 refresh(一次性手动;以后每日 refresh_today 自然累积)。"""
from __future__ import annotations

import sys
import datetime as _dt

from data import db, smart_money as sm_data


def _get_finshare():
    try:
        import finshare as fs
        return fs
    except Exception:
        return None


def _norm_date(s):
    return sm_data._norm_date(s) if s else None


def backfill(months: int = 6, batch_days: int = 30) -> int:
    fs = _get_finshare()
    if fs is None:
        print("finshare 未安装/不可用,跳过回填")
        return 0
    end = _dt.date.today()
    start = end - _dt.timedelta(days=months * 30)
    total = 0
    cur = start
    while cur < end:
        nxt = min(cur + _dt.timedelta(days=batch_days), end)
        try:
            df = fs.get_lhb(cur.strftime("%Y%m%d"), nxt.strftime("%Y%m%d"))
        except Exception as e:
            print(f"  {cur}~{nxt} 失败:{e}")
            cur = nxt
            continue
        if df is None or getattr(df, "empty", True):
            cur = nxt
            continue
        recs = []
        for _, row in df.iterrows():
            d = _norm_date(row.get("trade_date") or row.get("日期"))
            code = str(row.get("code") or row.get("股票代码") or "").zfill(6)
            if not d or not code:
                continue
            amt = row.get("net_buy") or row.get("净买额") or row.get("净额")
            try:
                amt = float(amt)
            except (TypeError, ValueError):
                amt = None
            recs.append(sm_data._rec(
                d, code, row.get("name") or row.get("股票简称"), "股票",
                "龙虎榜", "游资", "净买入", amt,
                raw={"source": "finshare"}))
        if recs:
            try:
                db.upsert_rows("smart_money_action", recs)
                total += len(recs)
            except Exception as e:
                print(f"  upsert {cur}~{nxt} 失败:{e}")
        cur = nxt
    print(f"回填完成,写入 {total} 条(标 source=finshare)")
    return total


if __name__ == "__main__":
    months = int(sys.argv[1]) if len(sys.argv) > 1 else 6
    bd = int(sys.argv[2]) if len(sys.argv) > 2 else 30
    backfill(months=months, batch_days=bd)
