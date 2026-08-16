# -*- coding: utf-8 -*-
"""buffett 超时降级测试：akshare hang 时 fetch_abstract 不卡死，降级返回 (None, False)。"""
import time
import backtest.buffett as bt_buf


def test_fetch_abstract_timeout_degrades(monkeypatch):
    bt_buf.db.init_db()
    # tdx 不可用→测 akshare hang 超时降级路径(本测试意图)
    monkeypatch.setattr(bt_buf.fundamentals, "parse_tdx_financial", lambda c: None)
    monkeypatch.setattr(bt_buf, "_AK_OK", True)
    monkeypatch.setattr(bt_buf, "_AK_TIMEOUT", 0.1)  # 0.1s 超时
    # mock ak 拉取 hang 0.5s > 超时
    monkeypatch.setattr(bt_buf.ak, "stock_financial_abstract",
                        lambda symbol: time.sleep(0.5) or None)
    df, stale = bt_buf.fetch_abstract("999999")  # 无缓存
    assert df is None, "超时应降级返回 None"
    assert stale is False, "无缓存时 stale=False"


def test_analyze_many_concurrent(monkeypatch):
    """并发不串行：4 只各 sleep 0.2，串行 0.8s，并发 <0.5s。"""
    import time as _t
    bt_buf.db.init_db()
    # tdx 不可用→走 akshare 路径(每只 sleep 0.2),测并发不串行
    monkeypatch.setattr(bt_buf.fundamentals, "parse_tdx_financial", lambda c: None)
    monkeypatch.setattr(bt_buf, "_AK_OK", True)
    monkeypatch.setattr(bt_buf, "_AK_TIMEOUT", 5)
    monkeypatch.setattr(bt_buf.ak, "stock_financial_abstract",
                        lambda symbol: _t.sleep(0.2) or None)
    t0 = time.time()
    bt_buf.analyze_many(["900001", "900002", "900003", "900004"])
    dt = time.time() - t0
    # 4 只并发(8 worker)应 < 串行 0.8s；容差给 < 0.6s
    assert dt < 0.6, f"analyze_many 应并发(实际 {dt:.2f}s，串行需 0.8s)"
