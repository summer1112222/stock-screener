# -*- coding: utf-8 -*-
"""pytdx_client 单测：纯函数映射 + mock _get_api 验证字段映射/分页/NaN→None。
不依赖网络。"""
import math
from datetime import datetime, timedelta
import pandas as pd
import pytest
from data import pytdx_client as t


# ---------- 纯函数 ----------
def test_market_mapping():
    assert t._pure_code("sh600519") == "600519"
    assert t._pure_code("sz000001") == "000001"
    assert t._pure_code("bj830799") == "830799"
    assert t._market("sh600519") == 1   # 带前缀沪个股
    assert t._market("sz000001") == 0   # 带前缀深个股
    assert t._market("000001") == 0   # 深个股
    assert t._market("600519") == 1   # 沪个股
    assert t._market("510300") == 1   # 沪 ETF
    assert t._market("159915") == 0   # 深 ETF
    assert t._market("300750") == 0   # 深创业板
    assert t._market("830799") == 2   # 北交(未实测,标 2)
    assert t._market("123") is None   # 非法长度
    assert t._market("abcdef") is None


def test_project_symbol():
    assert t._project_symbol("000001") == "sz000001"
    assert t._project_symbol("600519") == "sh600519"
    assert t._project_symbol("510300") == "sh510300"
    assert t._project_symbol("159915") == "sz159915"


# ---------- get_quote mock ----------
class _FakeApi:
    def __init__(self, quotes_df):
        self._qdf = quotes_df

    def to_df(self, data):
        return self._qdf

    def get_security_quotes(self, pairs):
        return self._qdf.to_dict("records")


class _RetryApi(_FakeApi):
    def __init__(self, quotes_df):
        super().__init__(quotes_df)
        self.calls = []

    def get_security_quotes(self, pairs):
        self.calls.append(pairs)
        if len(self.calls) == 1:
            raise RuntimeError("batch endpoint unavailable")
        return self._qdf.to_dict("records")


def _quote_df():
    return pd.DataFrame([
        {"code": "000001", "price": 11.25, "last_close": 11.26,
         "open": 11.26, "high": 11.29, "low": 11.20, "vol": 632950,
         "amount": 711612864.0, "bid1": 11.25, "ask1": 11.26,
         "bid2": 11.24, "ask2": 11.27, "bid3": 11.23, "ask3": 11.28,
         "bid4": 11.22, "ask4": 11.29, "bid5": 11.21, "ask5": 11.30,
         "bid_vol1": 1521, "ask_vol1": 264, "bid_vol2": 6431, "ask_vol2": 7948,
         "bid_vol3": 4026, "ask_vol3": 2288, "bid_vol4": 11910, "ask_vol4": 3893,
         "bid_vol5": 6575, "ask_vol5": 6227},
    ])


def test_get_quote(monkeypatch):
    monkeypatch.setattr(t, "_TDX_OK", True)
    monkeypatch.setattr(t, "_get_api", lambda: _FakeApi(_quote_df()))
    q = t.get_quote(["sz000001"])
    assert len(q) == 1
    r = q[0]
    assert r["code"] == "000001"
    assert r["price"] == 11.25
    assert r["bid1"] == 11.25 and r["ask1"] == 11.26
    assert r["bid_vol5"] == 6575


def test_get_quote_tdx_off(monkeypatch):
    monkeypatch.setattr(t, "_TDX_OK", False)
    assert t.get_quote(["000001"]) == []


def test_get_quote_nan_to_none(monkeypatch):
    monkeypatch.setattr(t, "_TDX_OK", True)
    df = _quote_df()
    df.loc[0, "price"] = float("nan")
    monkeypatch.setattr(t, "_get_api", lambda: _FakeApi(df))
    q = t.get_quote(["000001"])
    assert q[0]["price"] is None  # NaN→None,防 allow_nan=False 500


def test_get_quote_drops_empty_rows(monkeypatch):
    """TDX 批量接口偶发返回 code=None 空行，不能泄漏给调用方。"""
    monkeypatch.setattr(t, "_TDX_OK", True)
    df = pd.concat([
        pd.DataFrame([{"code": None, "price": None}]),
        _quote_df(),
    ], ignore_index=True)
    monkeypatch.setattr(t, "_get_api", lambda: _FakeApi(df))
    q = t.get_quote(["000001"])
    assert len(q) == 1
    assert q[0]["code"] == "000001"


def test_get_quote_retries_each_code_after_batch_failure(monkeypatch):
    """批量端点异常后逐只重试，避免整批行情静默丢失。"""
    monkeypatch.setattr(t, "_TDX_OK", True)
    api = _RetryApi(_quote_df())
    monkeypatch.setattr(t, "_get_api", lambda: api)
    q = t.get_quote(["000001"])
    assert len(q) == 1
    assert q[0]["code"] == "000001"
    assert len(api.calls) == 2


class _ProbeApi:
    def __init__(self, probe, connect_ok=True):
        self.probe = probe
        self.connect_ok = connect_ok
        self.disconnected = False

    def connect(self, host, port, time_out):
        return self.connect_ok

    def get_security_quotes(self, pairs):
        return self.probe

    def disconnect(self):
        self.disconnected = True


def test_get_api_reconnects_when_existing_probe_is_empty(monkeypatch):
    """TCP 尚存但探针空响应时，必须丢弃旧连接并重连。"""
    old = _ProbeApi([])
    new = _ProbeApi([{"code": "000001"}])
    created = []

    def _new_api():
        created.append(True)
        return new

    monkeypatch.setattr(t, "_TDX_OK", True)
    monkeypatch.setattr(t, "TdxHq_API", _new_api)
    monkeypatch.setattr(t, "_api", old)
    monkeypatch.setattr(t, "_connected_host", "old")
    monkeypatch.setattr(t, "_SERVERS", [("new", 7709)])
    api = t._get_api()
    assert api is new
    assert old.disconnected is True
    assert created == [True]
    assert t._connected_host == "new"


# ---------- get_daily_bars mock(分页) ----------
class _BarsApi:
    def __init__(self, frames):
        self._frames = frames  # list[df]，按调用顺序返回
        self._i = 0

    def to_df(self, data):
        return pd.DataFrame(data)  # 转 get_security_bars 返回的 records

    def get_security_bars(self, cat, mkt, code, start, want):
        f = self._frames[min(self._i, len(self._frames) - 1)]
        self._i += 1
        return f.to_dict("records")


def _bars_df(n, offset=0):
    """n 行合成日 K，offset 控制日期段，保证多页不重叠。"""
    return pd.DataFrame([
        {"open": 11.0, "close": 11.1, "high": 11.2, "low": 10.9,
         "vol": 10000.0, "amount": 1.1e8,
         "datetime": (datetime(2026, 1, 1) + timedelta(days=i + offset)).strftime("%Y-%m-%d 15:00")}
        for i in range(n)
    ])


def test_get_daily_bars_pagination(monkeypatch):
    monkeypatch.setattr(t, "_TDX_OK", True)
    monkeypatch.setattr(t, "_BATCH", 800)
    # count=1500,两次 800+700,验证分页累加(两页日期段不重叠)
    monkeypatch.setattr(t, "_get_api", lambda: _BarsApi([_bars_df(800, 0), _bars_df(700, 800)]))
    df = t.get_daily_bars("000001", 1500)
    assert len(df) == 1500
    assert set(["date", "open", "high", "low", "close",
                "volume", "amount", "symbol"]).issubset(df.columns)
    assert df["symbol"].iloc[0] == "sz000001"
    assert " 15:00" not in df["date"].iloc[0]  # datetime 截成 YYYY-MM-DD
    assert len(df["date"].unique()) == 1500  # 无重复


def test_get_daily_bars_off(monkeypatch):
    monkeypatch.setattr(t, "_TDX_OK", False)
    assert t.get_daily_bars("000001", 100).empty


# ---------- history 备援 ----------
def test_fetch_stock_hist_tdx_fallback(monkeypatch, tmp_path):
    """akshare 不可用 → tdx 主源(raw+本地 qfq),落 stock_daily,symbol=sz000001。"""
    import data.history as H
    monkeypatch.setattr(H, "_AK_OK", False)  # akshare 不可用,直走 tdx 主源
    monkeypatch.setattr(t, "_TDX_OK", True)
    monkeypatch.setattr(t, "_get_api", lambda: _BarsApi([_bars_df(5)]))
    df, ok, err = H.fetch_stock_hist("sz000001", "20260101", "20260812")
    assert ok, f"应 tdx 主源成功: {err}"
    assert "tdx主源" in err and "本地qfq" in err
    assert (df["symbol"] == "sz000001").all()


# ---------- get_company_info mock ----------
class _InfoApi:
    def __init__(self, cats, content):
        self._cats = cats
        self._content = content

    def get_company_info_category(self, mkt, code):
        return self._cats

    def get_company_info_content(self, mkt, code, fn, start, length):
        return self._content


def test_get_company_info(monkeypatch):
    monkeypatch.setattr(t, "_TDX_OK", True)
    cats = [{"name": "龙虎榜单", "filename": "000001.txt",
             "start": 100, "length": 50}]
    monkeypatch.setattr(t, "_get_api",
                        lambda: _InfoApi(cats, "☆龙虎榜单☆ 融资融券信息..."))
    r = t.get_company_info("000001", "龙虎榜单")
    assert r["ok"]
    assert "融资融券" in r["content"]


def test_get_company_info_missing_category(monkeypatch):
    monkeypatch.setattr(t, "_TDX_OK", True)
    monkeypatch.setattr(t, "_get_api",
                        lambda: _InfoApi([{"name": "财务分析"}], ""))
    r = t.get_company_info("000001", "龙虎榜单")
    assert not r["ok"]
    assert "无此类别" in r["err"]


class _BlockApi:
    def __init__(self):
        self.calls = []

    def get_block_info_meta(self, filename):
        self.calls.append(("meta", filename))
        return {"size": 3}

    def get_block_info(self, filename, start, size):
        self.calls.append(("data", filename, start, size))
        return b"raw"


def test_get_block_members_reads_full_file_and_normalizes(monkeypatch):
    """TDX 板块文件按 meta size 读取并归一前缀代码。"""
    monkeypatch.setattr(t, "_TDX_OK", True)
    api = _BlockApi()
    monkeypatch.setattr(t, "_get_api", lambda: api)

    class _Reader:
        def get_data(self, raw, reader_type):
            assert bytes(raw) == b"raw"
            return [{"blockname": "新能源", "code": "sh600001"},
                    {"blockname": "新能源", "code": "600001"},
                    {"blockname": "新能源", "code": "sz000002"},
                    {"blockname": "\x00坏组", "code": "600003"}]

    import sys
    import types
    reader = types.ModuleType("pytdx.reader.block_reader")
    reader.BlockReader = _Reader
    reader.BlockReader_TYPE_FLAT = object()
    monkeypatch.setitem(sys.modules, "pytdx.reader.block_reader", reader)
    result = t.get_block_members("industry")
    assert result == {"新能源": ["600001", "000002"]}
    # 行业板块读 block_zs.dat，而不是 block.dat（后者只有指数/自定义组）
    assert api.calls == [("meta", "block_zs.dat"), ("data", "block_zs.dat", 0, 3)]


def test_get_api_lock_timeout_on_contention(monkeypatch):
    """锁被卡死线程占住时，_get_api 应在 timeout 内降级返 None，不无限阻塞。

    根因：旧实现 with _lock 的 acquire 无 timeout，tdx 全挂时重连循环在锁内
    占满 5×_TIMEOUT(40s)，且若被卡死线程持锁则其他请求永久等待，冷 /api/quality
    并发请求全部堵死。加 acquire(timeout) 后超时即降级，不让请求无限排队。"""
    import threading
    import time

    class _HeldLock:
        def acquire(self, blocking=True, timeout=-1):
            return False  # 永不获取成功，模拟卡死线程持锁
        def release(self):
            pass

    held = _HeldLock()
    monkeypatch.setattr(t, "_lock", held)
    monkeypatch.setattr(t, "_TDX_OK", True)
    monkeypatch.setattr(t, "_TIMEOUT", 0.2)
    box = {}
    worker = threading.Thread(target=lambda: box.setdefault("r", t._get_api()),
                              daemon=True)
    t0 = time.time()
    worker.start()
    worker.join(timeout=1.0)
    dt = time.time() - t0
    assert not worker.is_alive(), "锁争用应在 timeout 内返回而非无限阻塞"
    assert box.get("r") is None, "锁超时应降级返 None"
    assert dt < 1.0, f"应快速降级(实际 {dt:.2f}s)"
