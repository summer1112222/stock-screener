# -*- coding: utf-8 -*-
import scripts.backfill_lhb_history as bf


def test_backfill_idempotent(monkeypatch):
    # finshare 返回两日榜单, upsert 应被调用, 失败不崩
    calls = []

    class _FS:
        @staticmethod
        def get_lhb(start_date, end_date):
            import pandas as pd
            return pd.DataFrame([
                {"code": "000001", "trade_date": "20260701", "name": "A", "net_buy": 1e8},
                {"code": "000002", "trade_date": "20260702", "name": "B", "net_buy": 2e8},
            ])

    monkeypatch.setattr(bf, "_get_finshare", lambda: _FS())
    monkeypatch.setattr(bf.sm_data, "_rec",
                        lambda *a, **k: {"date": "2026-07-01", "code": "000001",
                                         "channel": "龙虎榜"})
    monkeypatch.setattr(bf.db, "upsert_rows",
                        lambda table, recs: calls.append(len(recs)))
    bf.backfill(months=1, batch_days=30)
    assert calls  # upsert 至少调用一次


def test_backfill_finshare_missing(monkeypatch):
    monkeypatch.setattr(bf, "_get_finshare", lambda: None)
    monkeypatch.setattr(bf.db, "upsert_rows", lambda table, recs: None)
    bf.backfill(months=1, batch_days=30)  # 不抛异常
