# tests/test_board_stocks.py
# -*- coding: utf-8 -*-
"""板块成分股解析单测（mock requests，不触网）。
锁住：read_html flavor='lxml'、代码列前导零 zfill(6) 恢复、_prefix 交易所前缀、列映射。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from data import board_stocks as bs


import pytest


@pytest.fixture(autouse=True)
def _reset_em(monkeypatch):
    """每个测试前重置东财可用性缓存(模块级 _EM_AVAILABLE 跨测试会污染)。"""
    monkeypatch.setattr(bs, "_EM_AVAILABLE", None)
    yield


class _FakeResp:
    def __init__(self, text):
        self.text = text
        self.status_code = 200
        self.apparent_encoding = "utf-8"
        self.encoding = "utf-8"


# 模拟 THS 详情页静态表(序号/代码/名称/现价/涨跌幅/换手/量比/振幅/流通市值/市盈率)
# 002966 read_html 会读成 int 2966 丢前导零 → 考验 zfill(6)
_SAMPLE_HTML = """<html><body><table>
<tr><th>序号</th><th>代码</th><th>名称</th><th>现价</th><th>涨跌幅(%)</th>
<th>换手(%)</th><th>量比</th><th>振幅(%)</th><th>流通市值</th><th>市盈率</th></tr>
<tr><td>1</td><td>002966</td><td>苏州银行</td><td>10.5</td><td>2.3</td>
<td>1.2</td><td>0.8</td><td>3.1</td><td>50亿</td><td>15.2</td></tr>
<tr><td>2</td><td>600519</td><td>贵州茅台</td><td>1500</td><td>-1.2</td>
<td>0.5</td><td>0.3</td><td>1.1</td><td>20000亿</td><td>30.0</td></tr>
<tr><td>3</td><td>920526</td><td>北证股</td><td>19.7</td><td>-2.4</td>
<td>3.2</td><td>0.6</td><td>4.2</td><td>10亿</td><td>89.3</td></tr>
</table></body></html>"""


def _setup(monkeypatch, map_=None, pages=None):
    """mock _fetch_map(返固定映射) + requests.get(返样例页)。pages 控制多页(末页<20行)。"""
    monkeypatch.setattr(bs, "_fetch_map",
                        lambda cat: map_ if map_ is not None else {"测试板块": "881121"})
    seq = (pages or [_SAMPLE_HTML])
    it = iter(seq)

    def _fake_get(url, headers=None, timeout=15):
        try:
            return _FakeResp(next(it))
        except StopIteration:
            return _FakeResp("<html></html>")  # 末页空
    monkeypatch.setattr(bs.requests, "get", _fake_get)


def test_fetch_constituents_parses_codes_and_prefix(monkeypatch):
    """zfill(6) 恢复前导零 + _prefix 加交易所前缀 + 列映射正确。"""
    _setup(monkeypatch)
    rows = bs.fetch_constituents("测试板块", "行业", max_pages=1)
    assert len(rows) == 3
    by_raw = {r["raw_code"]: r for r in rows}
    # 002966 read_html 丢前导零为 2966，zfill(6) 恢复；0 开头→深市 sz
    assert "002966" in by_raw
    assert by_raw["002966"]["code"] == "sz002966"
    assert by_raw["002966"]["name"] == "苏州银行"
    assert by_raw["002966"]["price"] == 10.5
    # 600519 → 沪市 sh
    assert by_raw["600519"]["code"] == "sh600519"
    assert by_raw["600519"]["change_pct"] == -1.2
    # 920526 → 北交所 bj
    assert by_raw["920526"]["code"] == "bj920526"
    assert by_raw["920526"]["pe"] == 89.3
    assert by_raw["920526"]["circulating_market_cap"] == "10亿"


def test_fetch_constituents_pagination_stops_at_short_page(monkeypatch):
    """末页行数<20 即停(不足一页=末页判定)。"""
    # 第1页 20+行(用样例3行<20会立刻停，故给两页：第1页造满20行假数据、第2页3行)
    big = _SAMPLE_HTML.replace(
        "</table>",
        "".join(f"<tr><td>{i}</td><td>00000{i%10}</td><td>N{i}</td><td>1</td><td>0</td>"
                f"<td>0</td><td>0</td><td>0</td><td>1亿</td><td>10</td></tr>"
                for i in range(4, 24)) + "</table>",
    )
    _setup(monkeypatch, pages=[big, _SAMPLE_HTML])
    rows = bs.fetch_constituents("测试板块", "行业", max_pages=5)
    # 第1页 23 行 + 第2页 3 行(因 3<20 判末页停)，共 26
    assert len(rows) == 26


def test_fetch_constituents_unknown_board_raises(monkeypatch):
    """板块名不在 THS 映射中→抛 ValueError，由路由 catch 降级标 cons_error。"""
    _setup(monkeypatch, map_={"别的板块": "881121"})
    try:
        bs.fetch_constituents("不存在板块", "行业")
    except ValueError as e:
        assert "不存在板块" in str(e)
        return
    raise AssertionError("应抛 ValueError(板块名未匹配)")


def test_prefix_exchange_assignment():
    """_prefix 交易所前缀规则覆盖各代号段。"""
    assert bs._prefix("600519") == "sh600519"   # 沪市主板
    assert bs._prefix("688981") == "sh688981"   # 科创板
    assert bs._prefix("000001") == "sz000001"   # 深市主板
    assert bs._prefix("300750") == "sz300750"   # 创业板
    assert bs._prefix("920526") == "bj920526"   # 北交新代号
    assert bs._prefix("830799") == "bj830799"   # 北交老代号
    assert bs._prefix("600519") == "sh600519"
    # 非数字原样返回
    assert bs._prefix("sh600519") == "sh600519"


def test_nan_to_none(monkeypatch):
    """NaN 值经 _f → None(防 allow_nan=False 500)。"""
    html = _SAMPLE_HTML.replace(">10.5<", ">-<").replace(">15.2<", ">nan<")
    _setup(monkeypatch, pages=[html])
    rows = bs.fetch_constituents("测试板块", "行业", max_pages=1)
    r = next(r for r in rows if r["raw_code"] == "002966")
    assert r["price"] is None      # "-" 非数 → None
    assert r["pe"] is None         # "nan" → None


# ---------------- 东财直取 + 降级 + 模糊匹配 ----------------

def test_normalize_em_column_mapping_and_zfill():
    """东财 DataFrame → 统一记录：列名关键字容错 + int 代码 zfill(6) + source=em。"""
    import pandas as pd
    df = pd.DataFrame([
        # 代码为 int(2966)→zfill(6) 恢复 002966；列名用东财实际名(最新价/换手/振幅/流通市值/市盈率)
        {"序号": 1, "代码": 2966, "名称": "苏州银行", "最新价": 10.5, "涨跌幅": 2.3,
         "换手": 1.2, "量比": 0.8, "振幅": 3.1, "流通市值": "50亿", "市盈率": 15.2},
        {"序号": 2, "代码": "600519", "名称": "贵州茅台", "最新价": 1500, "涨跌幅": -1.2,
         "换手": 0.5, "量比": 0.3, "振幅": 1.1, "流通市值": "20000亿", "市盈率": 30.0},
    ])
    rows = bs._normalize_em(df)
    assert len(rows) == 2
    assert rows[0]["code"] == "sz002966"   # int 2966 zfill → 002966 → 深市
    assert rows[0]["source"] == "em"
    assert rows[0]["price"] == 10.5
    assert rows[0]["pe"] == 15.2
    assert rows[0]["circulating_market_cap"] == "50亿"
    assert rows[1]["code"] == "sh600519"
    assert rows[1]["change_pct"] == -1.2


def test_fetch_constituents_prefers_em_when_available(monkeypatch):
    """东财路径返回数据 → 直接返回，不查 THS map(电动乘用车这类东财名命中)。"""
    em_rows = [{"code": "sh600519", "raw_code": "600519", "source": "em", "name": "贵州茅台"}]
    monkeypatch.setattr(bs, "_fetch_constituents_em", lambda b, c: em_rows)

    def _no_ths(cat):
        raise AssertionError("东财命中不应查 THS map")
    monkeypatch.setattr(bs, "_fetch_map", _no_ths)

    rows = bs.fetch_constituents("电动乘用车", "行业")
    assert rows is em_rows
    assert rows[0]["source"] == "em"


def test_fetch_constituents_em_empty_falls_to_ths(monkeypatch):
    """东财返回空(板名东财也无)→降级 THS 路径。"""
    monkeypatch.setattr(bs, "_fetch_constituents_em", lambda b, c: [])
    _setup(monkeypatch)  # THS map + requests mock
    rows = bs.fetch_constituents("测试板块", "行业", max_pages=1)
    assert len(rows) == 3
    assert all(r["source"] == "ths" for r in rows)


def test_fetch_constituents_em_raises_falls_to_ths(monkeypatch):
    """东财异常(被封/超时)→降级 THS 路径，不崩。"""
    def _em_err(b, c):
        raise ConnectionError("RemoteDisconnected")
    monkeypatch.setattr(bs, "_fetch_constituents_em", _em_err)
    _setup(monkeypatch)
    rows = bs.fetch_constituents("测试板块", "行业", max_pages=1)
    assert len(rows) == 3
    assert rows[0]["source"] == "ths"


def test_fetch_constituents_em_importerror_falls_to_ths(monkeypatch):
    """akshare 未装(本地开发环境)→ ImportError 静默降级 THS。"""
    import builtins
    real_import = builtins.__import__

    def _block_akshare(name, *a, **k):
        if name == "akshare":
            raise ModuleNotFoundError("No module named 'akshare'")
        return real_import(name, *a, **k)
    monkeypatch.setattr(builtins, "__import__", _block_aksearch if False else _block_akshare)
    _setup(monkeypatch)
    rows = bs.fetch_constituents("测试板块", "行业", max_pages=1)
    assert len(rows) == 3


def test_fuzzy_match_containment():
    """精确失败时按包含/被包含取最长命中。"""
    m = {"汽车": "881001", "汽车整车": "881002", "新能源": "881003"}
    # 板名"汽车整车"精确命中
    assert bs._fuzzy_match(m, "汽车整车") == "881002"
    # 板名"汽车零部件"含"汽车"但不含"汽车整车"→取"汽车"(最长包含子串)
    assert bs._fuzzy_match(m, "汽车零部件") == "881001"
    # 板名是 THS 名的超集→反向包含
    assert bs._fuzzy_match({"白酒": "881010"}, "白酒概念") == "881010"
    # 无任何交集
    assert bs._fuzzy_match(m, "电动乘用车") is None
    assert bs._fuzzy_match({}, "x") is None
