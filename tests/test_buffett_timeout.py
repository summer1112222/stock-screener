# -*- coding: utf-8 -*-
"""buffett 超时降级测试：akshare hang 时 fetch_abstract 不卡死，降级返回 (None, False)。"""
import threading
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


def test_analyze_many_deadline_no_zombie_threads(monkeypatch):
    """deadline 分支不残留非 daemon 僵尸线程（冷启动 /api/quality 超时根因）。

    根因：旧实现 8 worker ThreadPool + shutdown(wait=False, cancel_futures=True)
    在 worker 任务未完成时残留非 daemon 线程；僵尸线程占住 pytdx 单连接 Lock
    且不排队（cancel_futures 只取消未启动任务），使后续 /api/quality 请求的
    prefetch 全部堵在该锁上死等 → 冷启动后必超时(AbortError)。
    改串行后无线程池，不残留任何线程。"""
    bt_buf.db.init_db()
    # tdx 不可用→走 akshare 路径(每只 sleep 0.5 > deadline)，测残留线程
    monkeypatch.setattr(bt_buf.fundamentals, "parse_tdx_financial", lambda c: None)
    monkeypatch.setattr(bt_buf, "_AK_OK", True)
    monkeypatch.setattr(bt_buf, "_AK_TIMEOUT", 5)
    monkeypatch.setattr(bt_buf.ak, "stock_financial_abstract",
                        lambda symbol: time.sleep(0.5) or None)
    monkeypatch.setattr(bt_buf, "prefetch_financial", lambda codes, deadline_s=None: None)
    bt_buf.analyze_many(["900001", "900002", "900003", "900004"], deadline_s=0.005)
    time.sleep(0.2)  # 若旧实现残留 worker 线程此刻仍在 sleep 0.5s
    extra = [t for t in threading.enumerate()
             if t is not threading.main_thread() and not t.daemon]
    assert not extra, f"不应残留非 daemon 线程(残留 {len(extra)} 个: {extra})"


def test_prefetch_financial_respects_deadline(monkeypatch):
    """串行预取受 deadline_s 预算约束：到点即止，不遍历全部 code。

    修复：旧实现 80 只×~2s 串行预取无截止，冷缓存首次 /api/quality 可耗
    >160s 远超前端 75s/90s 超时(AbortError)。"""
    bt_buf.db.init_db()
    calls = []

    def slow_parse(c):
        calls.append(c)
        time.sleep(0.2)  # 模拟单只 tdx 解析耗时
        return {"abstract": None, "balance": None, "cashflow": None, "profit": None}

    monkeypatch.setattr(bt_buf.fundamentals, "_parse_tdx_with_timeout", slow_parse)
    codes = [f"9{i:05d}" for i in range(10)]  # 10 只 × 0.2s = 2s 全跑完
    t0 = time.time()
    bt_buf.prefetch_financial(codes, deadline_s=0.45)
    dt = time.time() - t0
    assert len(calls) < len(codes), "deadline 应截断预取，不遍历全部 code"
    assert dt < 1.2, f"应提前停止(实际 {dt:.2f}s，全跑需 2s)"


def test_analyze_many_deadline_returns_partial_not_empty(monkeypatch):
    """deadline 路径返回非空部分结果（优质筛选项口径2 拉数据 bug 回归守卫）。

    根因：旧实现 deadline 分支先调 prefetch_financial(codes, deadline_s=deadline_s)
    耗尽全部预算,prefetch 返回后 analyze 串行循环 ``if monotonic-started>=budget``
    立即 break→返回空列表。quality 口径2 调 analyze_many(deadline_s=40.0) 永远
    拿到 []，始终降级 spot 估值代理,优质筛选永远拉不到真实财报数据。
    修复：deadline 路径不调 prefetch,让串行 analyze 循环用满预算自己解析+缓存。

    本测试用慢 _parse_tdx_with_timeout(0.2s/只)精确复现冷缓存 tdx parse 耗时场景:
    若 analyze_many 错误地调 prefetch 并把全部 deadline 给它,prefetch 会逐只慢解析
    耗光预算→analyze 循环 0 预算→空结果。修复后 prefetch 不被调用,预算全给 analyze。"""
    bt_buf.db.init_db()
    parse_calls = []
    # 慢 parse:模拟真实 tdx 解析 0.2s/只。旧 bug 下 prefetch 会耗光 deadline 预算。
    def slow_parse(c):
        parse_calls.append(c)
        time.sleep(0.2)
        return None  # tdx 无此代码→返 None(走 akshare 备援或 None)
    monkeypatch.setattr(bt_buf.fundamentals, "_parse_tdx_with_timeout", slow_parse)
    # analyze 走 fetch_abstract:tdx 慢解析返 None→akshare 关(_AK_OK=False)→返 None,
    # analyze 返 None,_one 过滤掉。为测"预算用于 analyze 能产出结果",直接 mock analyze
    # 为快路径(模拟缓存命中场景),验证 analyze_many 不把预算全给 prefetch。
    monkeypatch.setattr(bt_buf, "_AK_OK", False)
    monkeypatch.setattr(bt_buf, "analyze", lambda c: {"code": c, "pe": 10.0, "ratios": {}})
    codes = [f"7{i:05d}" for i in range(10)]  # 10 只
    t0 = time.time()
    res = bt_buf.analyze_many(codes, deadline_s=0.5)  # 0.5s 预算
    dt = time.time() - t0
    # 修复后:deadline 路径不调 prefetch→slow_parse 不应被 analyze_many 触发;
    # analyze 每只即时返结果,0.5s 内应完成全部 10 只(至少 >0 只)。
    assert len(res) > 0, (f"deadline 路径应返回非空结果(实际 {len(res)}, "
                          f"prefetch 调用 {len(parse_calls)} 次,耗时 {dt:.2f}s)")
    assert len(parse_calls) == 0, ("deadline 路径不应调 prefetch(慢解析会耗光预算)"
                                   f"(实际 parse {len(parse_calls)} 次)")
    assert dt < 1.5, f"应受 deadline 约束(实际 {dt:.2f}s)"
