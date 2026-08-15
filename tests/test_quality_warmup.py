# tests/test_quality_warmup.py
# -*- coding: utf-8 -*-
"""refresh 后 quality 缓存预热测试:调用链 + _AK_OK 跳过 + 非阻塞锁防叠加。
mock buffett/quality,不触网。仓库根目录跑。"""
import sys, time, threading
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from api import server


def test_warm_quality_cache_calls_chain(monkeypatch):
    """_warm_quality_cache: shortlist→analyze_many(deadline=120)→quality_rank(默认+refine=True)。"""
    monkeypatch.setattr(server.bt_buf, "_AK_OK", True)
    sl_calls = {}
    def _fake_sl(**kw):
        sl_calls.update(kw)
        return ["000001", "600519"]
    monkeypatch.setattr(server.bt_buf, "shortlist_by_turnover", _fake_sl)

    am_args = {}
    def _fake_am(codes, deadline_s=None):
        am_args["codes"] = list(codes)
        am_args["deadline"] = deadline_s
        return []
    monkeypatch.setattr(server.bt_buf, "analyze_many", _fake_am)

    qr_args = {}
    from backtest import quality
    def _fake_rank(**kw):
        qr_args.update(kw)
        return {"main": [], "cand_disclaimer": "x"}
    monkeypatch.setattr(quality, "quality_rank", _fake_rank)

    assert server._warm_quality_cache() is True
    assert sl_calls.get("k") == 80 and sl_calls.get("min_turnover") == 5e8
    assert am_args["deadline"] == 120.0
    assert am_args["codes"] == ["000001", "600519"]
    assert qr_args["universe"] == "stock" and qr_args["refine"] is True
    assert qr_args["days"] == 20 and qr_args["limit"] == 20  # 对齐前端默认


def test_warm_skips_when_ak_not_ok(monkeypatch):
    """_AK_OK=False(akshare 不可用)时不预热,直接返 False。"""
    monkeypatch.setattr(server.bt_buf, "_AK_OK", False)
    called = []
    monkeypatch.setattr(server.bt_buf, "shortlist_by_turnover",
                        lambda **kw: called.append(1) or [])
    assert server._warm_quality_cache() is False
    assert called == []  # 未调 shortlist


def test_warm_background_skips_when_lock_held(monkeypatch):
    """锁已持有时(上次预热在跑)不起新线程,避免叠加。"""
    server._warm_lock.acquire()  # 模拟上次预热仍持有锁
    started = []
    orig_thread = threading.Thread

    class _SpyThread(orig_thread):
        def __init__(self, *a, **k):
            started.append(1)
            super().__init__(*a, **k)
        def start(self):
            pass  # 不真启动,只记录是否尝试创建
    monkeypatch.setattr(server.threading, "Thread", _SpyThread)
    server._warm_quality_cache_background()
    assert started == [], "锁持有时不应起新线程"
    server._warm_lock.release()


def test_warm_background_starts_thread_when_idle(monkeypatch):
    """空闲时后台预热会起 daemon 线程(打桩 _warm_quality_cache 避免触网)。"""
    ran = []
    monkeypatch.setattr(server, "_warm_quality_cache", lambda: ran.append(1) or True)
    t_before = threading.active_count()
    server._warm_quality_cache_background()
    # 等 daemon 线程跑完(锁释放)
    for _ in range(50):
        if not server._warm_lock.locked():
            break
        time.sleep(0.02)
    assert ran == [1]


def test_startup_warm_fires_background(monkeypatch):
    """_startup_warm 启动即调 _warm_quality_cache_background(不依赖 refresh)。"""
    fired = []
    monkeypatch.setattr(server, "_warm_quality_cache_background",
                        lambda: fired.append(1))
    server._startup_warm()
    assert fired == [1]

