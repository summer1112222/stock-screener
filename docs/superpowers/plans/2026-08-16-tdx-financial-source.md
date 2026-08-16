# 通达信财报 tdx 主源化 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 buffett/fundamentals 的财报数据源从 akshare(东财被封/慢,致优质筛选超时)切到通达信 pytdx `get_company_info` "财务分析"文本解析,完全脱 akshare 财报依赖。

**Architecture:** `parse_tdx_financial(code)` 解析 tdx"财务分析"文本,产出 abstract 摘要宽表(行=指标/列=报告期,兼容 `_row_pairs`)+ 三大表 df(转置为行=报告期/列=科目,兼容 `_pick_col_sum`/`_pick_row_fields`)。一次调用含 abstract+三大表,解析后分解缓存:abstract→`financial_abstract_cache`(buffett 域),三大表→`fundamentals_cache(code, source)`(fundamentals 域)。`fetch_abstract`/`fundamentals.fetch` 改 tdx 主源,缓存 miss 调 parse 分解预填,后续 source 命中秒回;akshare 降备援。

**Tech Stack:** Python 3.12 / pandas 3.0 / pytdx(已装,`data/pytdx_client.py` 已有 `get_company_info`)/ pytest。

**Spec:** `docs/superpowers/specs/2026-08-16-tdx-financial-spot-source-design.md` 子系统 A。

## Global Constraints

- 测试从仓库根目录跑(无 conftest/pytest.ini):`python -m pytest tests/xxx -q`。
- NaN→None:所有数值出口用 `float(x) if pd.notna(x) else None` 或 `_nan`(防 `JSONResponse allow_nan=False` 500)。
- 报告期带破折号"2024-12-31",判年报用 `.replace("-","").endswith("1231")`(裸 endswith 尾4字符是"2-31"漏判)。
- `_pick_row_fields`/`_pick_col_sum`/`_annual`/`_cagr` 假设序列降序(新→旧),tdx 解析产出须降序(文本本就最新在前,保持)。
- tdx 文本数值后缀"亿"×1e8/"万"×1e4,空值"-"→None,负数带负号。
- buffett abstract 缓存表 `financial_abstract_cache`(按 code 无 source 列);fundamentals 三大表缓存表 `fundamentals_cache`(按 code+source)——两张不同表,parse 分解缓存时各归其表。
- 合规:财报是公开数据机械汇总,不荐股不输出买卖点,措辞"机械评分/研究优先级",挂 `bt_disclaimer`(用户自用放松合规,但 disclaimer 管道保留)。

---

## 文件结构

- 修改 `data/fundamentals.py`:加 `_parse_cn_amount`/`parse_tdx_financial`;`fetch` 改 tdx 主源。
- 修改 `data/buffett.py`:`fetch_abstract` 改 tdx 主源(调 parse 分解缓存)。
- 新建 `tests/fixtures/tdx_financial_600519.json`:真实 600519 财务分析文本快照(golden test)。
- 新建 `tests/test_tdx_financial.py`:解析器 + 主源切换测试。
- 适配 `tests/test_buffett_value.py`/`tests/test_fundamentals.py`(若 mock 链受影响)。

---

### Task 1: `_parse_cn_amount` 单位解析工具

**Files:**
- Modify: `data/fundamentals.py`(顶部 import 后新增)
- Test: `tests/test_tdx_financial.py`(新建)

**Interfaces:**
- Produces: `_parse_cn_amount(s: str) -> float | None` — 解析"445.1688亿"→44516800000.0,"57.0895万"→570895.0,"5234.12"→5234.12,"-"→None,""-→None,"-1.2亿"→-120000000.0,含"%"→去%符号返数值。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_tdx_financial.py
# -*- coding: utf-8 -*-
from data.fundamentals import _parse_cn_amount

def test_parse_cn_amount():
    assert _parse_cn_amount("445.1688亿") == 44516800000.0
    assert _parse_cn_amount("57.0895万") == 570895.0
    assert _parse_cn_amount("5234.12") == 5234.12
    assert _parse_cn_amount("-1.2亿") == -120000000.0
    assert _parse_cn_amount("-") is None
    assert _parse_cn_amount("") is None
    assert _parse_cn_amount("89.5552%") == 89.5552
    assert _parse_cn_amount(None) is None
    assert _parse_cn_amount("  16.75 ") == 16.75  # 空白容忍
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_tdx_financial.py::test_parse_cn_amount -q`
Expected: FAIL(ImportError: cannot import _parse_cn_amount)

- [ ] **Step 3: 实现**

```python
# data/fundamentals.py 顶部 import 后
def _parse_cn_amount(s):
    """解析中文金额/百分比字符串→float。亿×1e8/万×1e4/纯数字/含%去符号,空/异常→None。"""
    if s is None:
        return None
    t = str(s).strip().replace("%", "")
    if not t or t == "-":
        return None
    neg = False
    if t.startswith("-"):
        neg = True
        t = t[1:].strip()
    mult = 1.0
    if t.endswith("亿"):
        t = t[:-1]; mult = 1e8
    elif t.endswith("万"):
        t = t[:-1]; mult = 1e4
    try:
        v = float(t) * mult
    except (ValueError, TypeError):
        return None
    return -v if neg else v
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_tdx_financial.py::test_parse_cn_amount -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add data/fundamentals.py tests/test_tdx_financial.py
git commit -m "feat(fundamentals): _parse_cn_amount 中文金额解析(亿/万/%/负/空)"
```

---

### Task 2: 真实文本 fixture + `parse_tdx_financial` 解析器

**Files:**
- New: `tests/fixtures/tdx_financial_600519.json`(600519 财务分析文本快照)
- Modify: `data/fundamentals.py`(加 `parse_tdx_financial` + 依赖 `pytdx_client`)
- Test: `tests/test_tdx_financial.py`(加 golden test)

**Interfaces:**
- Produces: `parse_tdx_financial(code: str) -> dict[str, pd.DataFrame | None]` — 返 `{"abstract":df|None, "balance":df|None, "cashflow":df|None, "profit":df|None}`。
  - abstract: 宽表,列含"指标"(首列)+报告期列(YYYY-MM-DD 降序最新在前),行=各财务指标(净利润/营业总收入/加权净资产收益率/资产负债比率/基本每股收益/每股净资产/每股经营现金流量/营业毛利率 等),数值 float。兼容 `buffett._row_pairs`(按"指标"列 contains 取行)。
  - balance/cashflow/profit: 行=报告期,列=科目(含"报告期"日期列,降序最新在前),数值 float。兼容 `_pick_col_sum`/`_pick_row_fields`(按"报告期"列定位年报行 endswith 1231,列名 contains 取科目)。
  - tdx `get_company_info` ok=False/空 content→全 None,不抛崩。

- [ ] **Step 1: 抓 fixture(真实文本)**

```bash
# 从运行中的容器抓 600519 财务分析文本存 fixture(用 /api/tdx/company-info 已验证返回完整文本)
curl -s "http://localhost:8000/api/tdx/company-info?code=600519&category=%E8%B4%A2%E5%8A%A1%E5%88%86%E6%9E%90" \
  | python -c "import sys,json; d=json.load(sys.stdin); open('tests/fixtures/tdx_financial_600519.json','w',encoding='utf-8').write(json.dumps(d['data']['content'],ensure_ascii=False))"
```
确认 fixture 文件含"【1.财务指标】""【资产负债表摘要】""【利润表摘要】""【现金流量表摘要】"。

- [ ] **Step 2: 写失败 golden test**

```python
# tests/test_tdx_financial.py 追加
import json, io, os
import pandas as pd
from data import fundamentals

FIX = os.path.join(os.path.dirname(__file__), "fixtures", "tdx_financial_600519.json")

def _load_fix():
    with open(FIX, encoding="utf-8") as f:
        return json.load(f)

def test_parse_tdx_financial_golden(monkeypatch):
    content = _load_fix()
    # mock pytdx_client.get_company_info 返 fixture
    monkeypatch.setattr(fundamentals, "get_company_info",
                        lambda code, category: {"code": code, "category": category,
                                                "content": content, "ok": True, "err": ""})
    r = fundamentals.parse_tdx_financial("600519")
    # 1) 四键齐全
    assert set(r.keys()) == {"abstract", "balance", "cashflow", "profit"}
    # 2) abstract 摘要宽表结构
    abs_df = r["abstract"]
    assert abs_df is not None and not abs_df.empty
    assert "指标" in abs_df.columns
    # 报告期列含 2025-12-31
    assert any("2025-12-31" in str(c) for c in abs_df.columns)
    # 含关键指标行(净利润/营业总收入/加权净资产收益率)
    names = abs_df["指标"].astype(str).tolist()
    assert any("净利润" in n for n in names)
    assert any("营业总收入" in n for n in names)
    assert any("加权净资产收益率" in n for n in names)
    # 3) 三大表 df:行=报告期 列含"报告期"
    for k in ("balance", "cashflow", "profit"):
        df = r[k]
        assert df is not None and not df.empty, f"{k} empty"
        assert "报告期" in df.columns, f"{k} 无报告期列"
        # 报告期降序(首行最新)
        first = str(df.iloc[0]["报告期"])
        assert "2026" in first or "2025" in first
    # 4) balance 含资产总额/负债总额/股东权益合计
    bcols = [str(c) for c in r["balance"].columns]
    assert any("资产总额" in c for c in bcols)
    assert any("负债总额" in c for c in bcols)
    # 5) cashflow 含经营活动现金净额
    ccols = [str(c) for c in r["cashflow"].columns]
    assert any("经营活动现金净额" in c for c in ccols)
    # 6) profit 含营业收入/净利润
    pcols = [str(c) for c in r["profit"].columns]
    assert any("营业收入" in c for c in pcols)
    # 7) 数值类型:净利润行某报告期值为 float 非 str
    ni_row = abs_df[abs_df["指标"].astype(str).str.contains("净利润", na=False)].iloc[0]
    val = None
    for c in abs_df.columns:
        if c == "指标": continue
        if "2025-12-31" in str(c):
            val = ni_row[c]; break
    assert isinstance(val, float), f"净利润值非float: {val}"

def test_parse_tdx_financial_fail_returns_none(monkeypatch):
    monkeypatch.setattr(fundamentals, "get_company_info",
                        lambda code, category: {"code": code, "category": category,
                                                "content": "", "ok": False, "err": "连不上"})
    r = fundamentals.parse_tdx_financial("600519")
    assert r == {"abstract": None, "balance": None, "cashflow": None, "profit": None}
```

- [ ] **Step 3: 跑测试确认失败**

Run: `python -m pytest tests/test_tdx_financial.py::test_parse_tdx_financial_golden tests/test_tdx_financial.py::test_parse_tdx_financial_fail_returns_none -q`
Expected: FAIL(parse_tdx_financial 不存在)

- [ ] **Step 4: 实现 parse_tdx_financial**

```python
# data/fundamentals.py
# 顶部加 import(同文件已 import pandas/db/collector)
from . import pytdx_client  # 复用 get_company_info

_TDX_FIN_CATEGORY = "财务分析"

def _split_table_rows(content: str, start_marker: str):
    """从 content 定位 start_marker(如'【资产负债表摘要】')后的第一个表格,
    返回 [行, ...], 每行=[单元, ...](已去首尾空白与表格边框符)。
    表格以 ┌ 开头、└ 结尾;行以 ｜ 分列。无表返 []。"""
    idx = content.find(start_marker)
    if idx < 0:
        return []
    seg = content[idx:]
    # 跳到第一个 ┌(表格起点)
    p = seg.find("┌")
    if p < 0:
        return []
    seg = seg[p:]
    end = seg.find("└")  # 表底(└...┘);注意 └ 可能紧接该行
    # 找到 └ 所在行末的 ┘
    q = seg.find("┘")
    if q < 0:
        return []
    table = seg[: q + 1]
    rows = []
    for line in table.splitlines():
        line = line.strip()
        if not line or line.startswith("┌") or line.startswith("├") or line.startswith("└"):
            continue
        # 数据行以 ｜ 起
        if "｜" not in line:
            continue
        cells = [c.strip() for c in line.split("｜")]
        # 去首尾空单元(split 首尾 ｜ 产生空串)
        if cells and cells[0] == "":
            cells = cells[1:]
        if cells and cells[-1] == "":
            cells = cells[:-1]
        if cells:
            rows.append(cells)
    return rows

def _build_wide_df(rows: list[list[str]]):
    """rows[0]=表头(报告期列), rows[1:]=科目行(首单元=指标名,余=数值)。
    返宽表 df:列=['指标', 报告期1, ...];数值经 _parse_cn_amount。"""
    if not rows or len(rows) < 2:
        return None
    header = rows[0]
    cols = ["指标"] + header[1:]
    data = []
    for r in rows[1:]:
        if len(r) < 2:
            continue
        name = r[0]
        vals = [_parse_cn_amount(v) for v in r[1:]]
        # 补齐列数
        while len(vals) < len(cols) - 1:
            vals.append(None)
        data.append([name] + vals[: len(cols) - 1])
    if not data:
        return None
    return pd.DataFrame(data, columns=cols)

def _transpose_three_table(rows: list[list[str]]):
    """rows[0]=表头(报告期列,首单元='指标(单位:元)'), rows[1:]=科目行。
    转置为行=报告期 列=科目(含'报告期'列),数值经 _parse_cn_amount。降序保持(表头本最新在前)。"""
    if not rows or len(rows) < 2:
        return None
    header = rows[0][1:]  # 报告期
    body = rows[1:]
    # 构 {科目: [值...]}
    recs = []
    for i, period in enumerate(header):
        period_clean = period.strip()
        rec = {"报告期": period_clean}
        for r in body:
            if len(r) < 2 + i:
                continue
            label = r[0].strip()
            rec[label] = _parse_cn_amount(r[1 + i])
        recs.append(rec)
    if not recs:
        return None
    return pd.DataFrame(recs)

def parse_tdx_financial(code: str) -> dict:
    """解析 tdx '财务分析' 文本。返 {abstract, balance, cashflow, profit}。
    abstract=摘要宽表(行=指标列=报告期,兼容 _row_pairs);
    balance/cashflow/profit=三大表(行=报告期列=科目含'报告期',兼容 _pick_col_sum/_pick_row_fields)。
    tdx 取失败/空→全 None,不抛崩。"""
    base = {"abstract": None, "balance": None, "cashflow": None, "profit": None}
    c = str(code).strip()
    info = pytdx_client.get_company_info(c, _TDX_FIN_CATEGORY)
    if not info.get("ok") or not info.get("content"):
        return base
    content = info["content"]
    # abstract: 合并财务指标各子表(主要/偿债/运营/盈利/发展)为单宽表
    abs_frames = []
    for marker in ("【主要财务指标】", "【盈利能力指标】", "【偿债能力指标】",
                   "【运营能力指标】", "【发展能力指标】"):
        rows = _split_table_rows(content, marker)
        df = _build_wide_df(rows)
        if df is not None:
            abs_frames.append(df)
    if abs_frames:
        abstract = pd.concat(abs_frames, ignore_index=True).drop_duplicates(subset=["指标"])
        base["abstract"] = abstract
    # 三大表摘要
    b_rows = _split_table_rows(content, "【资产负债表摘要】")
    base["balance"] = _transpose_three_table(b_rows)
    p_rows = _split_table_rows(content, "【利润表摘要】")
    base["profit"] = _transpose_three_table(p_rows)
    c_rows = _split_table_rows(content, "【现金流量表摘要】")
    base["cashflow"] = _transpose_three_table(c_rows)
    return base
```

- [ ] **Step 5: 跑测试确认通过**

Run: `python -m pytest tests/test_tdx_financial.py::test_parse_tdx_financial_golden tests/test_tdx_financial.py::test_parse_tdx_financial_fail_returns_none -q`
Expected: PASS。若 golden test 某断言失败,用 `print(r["abstract"].columns.tolist())` 等调试文本实际结构,修正解析器(文本格式可能微调,以 fixture 实际为准)。

- [ ] **Step 6: 提交**

```bash
git add data/fundamentals.py tests/test_tdx_financial.py tests/fixtures/tdx_financial_600519.json
git commit -m "feat(fundamentals): parse_tdx_financial 解析tdx财务分析文本(abstract宽表+三大表转置)"
```

---

### Task 3: `buffett.fetch_abstract` 改 tdx 主源

**Files:**
- Modify: `data/buffett.py`(`fetch_abstract` line 140)
- Test: `tests/test_tdx_financial.py`(追加)

**Interfaces:**
- Consumes: `fundamentals.parse_tdx_financial`(Task 2 产出)。
- Produces: `fetch_abstract(code)` 改优先 tdx:缓存命中→返;miss→`parse_tdx_financial`(解析后 abstract 缓存到 `financial_abstract_cache` via `_cache_set`,三大表预填 `fundamentals_cache` via `fundamentals._cache_set`+`_note_fetch(True)`)→返 abstract;tdx 返 None→`_note_fetch(False)`+akshare 备援(原 `ak.stock_financial_abstract`)+stale。
- 注意 buffett 的 `_cache_get/_cache_set`(line 73-101)操作 `financial_abstract_cache` 表(无 source);预填三大表须调 `fundamentals._cache_set(code, source, df)`(fundamentals_cache 表)。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_tdx_financial.py 追加
import data.buffett as buffett
import pandas as pd

def test_fetch_abstract_tdx_primary(monkeypatch):
    """tdx 解析成功→走 tdx,不调 akshare;abstract+三大表缓存被预填。"""
    called_ak = {"v": False}
    abs_df = pd.DataFrame({"指标": ["净利润"], "2025-12-31": [82320000000.0]})
    bal_df = pd.DataFrame({"报告期": ["2025-12-31"], "资产总额": [3e11]})
    parsed = {"abstract": abs_df, "balance": bal_df,
              "cashflow": None, "profit": None}
    monkeypatch.setattr(buffett.fundamentals, "parse_tdx_financial",
                        lambda code: dict(parsed))
    monkeypatch.setattr(buffett, "_cache_get", lambda code, allow_stale=False: (None, "miss"))
    set_calls = []
    monkeypatch.setattr(buffett, "_cache_set", lambda code, df: set_calls.append(("abstract", code)))
    fset_calls = []
    monkeypatch.setattr(buffett.fundamentals, "_cache_set",
                        lambda code, source, df: fset_calls.append((source, code)))
    monkeypatch.setattr(buffett, "_fetch_net", lambda code: (_ for _ in ()).throw(AssertionError("不应调 akshare")))
    df, stale = buffett.fetch_abstract("600519")
    assert stale is False
    assert df is abs_df
    assert ("abstract", "600519") in set_calls  # abstract 缓存
    assert ("balance", "600519") in fset_calls  # 三大表预填

def test_fetch_abstract_akshare_fallback(monkeypatch):
    """tdx 返全 None→走 akshare 备援。"""
    monkeypatch.setattr(buffett.fundamentals, "parse_tdx_financial",
                        lambda code: {"abstract": None, "balance": None, "cashflow": None, "profit": None})
    monkeypatch.setattr(buffett, "_cache_get", lambda code, allow_stale=False: (None, "miss"))
    monkeypatch.setattr(buffett, "_cache_get", lambda code, allow_stale=False: (None, "miss"))
    ak_df = pd.DataFrame({"指标": ["净利润"], "2024-12-31": [1.0]})
    monkeypatch.setattr(buffett, "_fetch_net", lambda code: ak_df)
    monkeypatch.setattr(buffett, "_cache_set", lambda code, df: None)
    df, stale = buffett.fetch_abstract("600519")
    assert df is ak_df
    assert stale is False
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_tdx_financial.py::test_fetch_abstract_tdx_primary tests/test_tdx_financial.py::test_fetch_abstract_akshare_fallback -q`
Expected: FAIL(fetch_abstract 未走 tdx)

- [ ] **Step 3: 改 fetch_abstract**

```python
# data/buffett.py  fetch_abstract 替换(line 140)
def fetch_abstract(code: str) -> tuple[pd.DataFrame | None, bool]:
    """缓存7天TTL;tdx 主源(parse_tdx_financial 解析财务分析文本)→akshare 备援。
    tdx 解析成功时 abstract 缓存本表(financial_abstract_cache)+三大表预填
    fundamentals_cache(供 fundamentals.fetch 命中秒回)。熔断:_note_fetch 记 tdx/akshare 结果,
    连续≥3失败→akshare_blocked() 熔断30min(quality 口径2 跳 buffett 省 deadline)。"""
    code = str(code).strip()
    df, status = _cache_get(code, allow_stale=False)
    if status == "hit":
        return df, False
    # tdx 主源
    try:
        parsed = fundamentals.parse_tdx_financial(code)
    except Exception:
        parsed = None
    if parsed:
        abs_df = parsed.get("abstract")
        if abs_df is not None and not abs_df.empty:
            try:
                _cache_set(code, abs_df)
            except Exception:
                pass
            # 预填三大表缓存(fundamentals 域)
            for s in ("balance", "cashflow", "profit"):
                tdf = parsed.get(s)
                if tdf is not None and not tdf.empty:
                    try:
                        fundamentals._cache_set(code, s, tdf)
                    except Exception:
                        pass
            _note_fetch(True)
            return abs_df, False
    _note_fetch(False)  # tdx 失败计熔断
    # akshare 备援(原逻辑)
    if _AK_OK:
        try:
            with ThreadPoolExecutor(max_workers=1) as ex:
                net = ex.submit(_fetch_net, code).result(timeout=_AK_TIMEOUT)
            if net is not None and not net.empty:
                _cache_set(code, net)
                _note_fetch(True)
                return net, False
        except (FuturesTimeout, Exception):
            pass
    df_s, _ = _cache_get(code, allow_stale=True)
    if df_s is not None:
        return df_s, True
    return None, False
```

注:`fundamentals` 已在 buffett.py 顶部 import(line 27 `from . import db, collector` 旁,确认 `from . import fundamentals` 存在;若无则加)。检查 buffett.py 顶部 import,`fundamentals` 当前通过 `fundamentals.fetch` 调用(line 285)说明已 import,确认 import 路径。

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_tdx_financial.py::test_fetch_abstract_tdx_primary tests/test_tdx_financial.py::test_fetch_abstract_akshare_fallback -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add data/buffett.py tests/test_tdx_financial.py
git commit -m "feat(buffett): fetch_abstract 改tdx主源(解析财务分析文本+预填三大表缓存),akshare降备援"
```

---

### Task 4: `fundamentals.fetch` 改 tdx 主源

**Files:**
- Modify: `data/fundamentals.py`(`fetch` line 85)
- Test: `tests/test_tdx_financial.py`(追加)

**Interfaces:**
- Consumes: `parse_tdx_financial`(Task 2,本文件内)。
- Produces: `fetch(code, source)` 改:缓存命中→返;miss→`parse_tdx_financial`(解析,命中本 source 缓存全部,返本 source df)→akshare 备援(原 `_fetch_net`)+stale。
- 关键:buffett.analyze 内 fetch_abstract 先调(Task3)已预填三大表,此处命中秒回;独立调 fundamentals.fetch 时 miss→自 parse 一次。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_tdx_financial.py 追加
from data import fundamentals

def test_fundamentals_fetch_tdx_primary(monkeypatch):
    """缓存 miss→parse_tdx_financial 返本 source,不调 akshare。"""
    bal_df = pd.DataFrame({"报告期": ["2025-12-31"], "资产总额": [3e11]})
    monkeypatch.setattr(fundamentals, "_cache_get",
                        lambda code, source, allow_stale=False: (None, "miss"))
    parsed = {"abstract": None, "balance": bal_df, "cashflow": None, "profit": None}
    monkeypatch.setattr(fundamentals, "parse_tdx_financial", lambda code: dict(parsed))
    monkeypatch.setattr(fundamentals, "_cache_set", lambda code, source, df: None)
    monkeypatch.setattr(fundamentals, "_fetch_net",
                        lambda code, source: (_ for _ in ()).throw(AssertionError("不应调 akshare")))
    df, stale = fundamentals.fetch("600519", "balance")
    assert df is bal_df
    assert stale is False

def test_fundamentals_fetch_cache_hit(monkeypatch):
    """缓存命中→不 parse 不 akshare。"""
    bal_df = pd.DataFrame({"报告期": ["2025-12-31"], "资产总额": [3e11]})
    monkeypatch.setattr(fundamentals, "_cache_get",
                        lambda code, source, allow_stale=False: (bal_df, "hit"))
    monkeypatch.setattr(fundamentals, "parse_tdx_financial",
                        lambda code: (_ for _ in ()).throw(AssertionError("不应 parse")))
    df, stale = fundamentals.fetch("600519", "balance")
    assert df is bal_df
    assert stale is False

def test_fundamentals_fetch_akshare_fallback(monkeypatch):
    """tdx 无本 source→akshare 备援。"""
    monkeypatch.setattr(fundamentals, "_cache_get",
                        lambda code, source, allow_stale=False: (None, "miss"))
    monkeypatch.setattr(fundamentals, "parse_tdx_financial",
                        lambda code: {"abstract": None, "balance": None, "cashflow": None, "profit": None})
    monkeypatch.setattr(fundamentals, "_cache_set", lambda code, source, df: None)
    ak_df = pd.DataFrame({"报告期": ["2024-12-31"], "资产总额": [1.0]})
    monkeypatch.setattr(fundamentals, "_fetch_net", lambda code, source: ak_df)
    df, stale = fundamentals.fetch("600519", "balance")
    assert df is ak_df
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_tdx_financial.py::test_fundamentals_fetch_tdx_primary tests/test_tdx_financial.py::test_fundamentals_fetch_cache_hit tests/test_tdx_financial.py::test_fundamentals_fetch_akshare_fallback -q`
Expected: FAIL(fetch 未走 tdx)

- [ ] **Step 3: 改 fetch**

```python
# data/fundamentals.py  fetch 替换(line 85)
def fetch(code: str, source: str) -> tuple[pd.DataFrame | None, bool]:
    """缓存7天TTL;tdx 主源(parse_tdx_financial 解析后命中本 source)→akshare 备援。
    buffett.analyze 内 fetch_abstract 已预填三大表缓存时命中秒回;独立调时 miss→自 parse。"""
    c = _strip_prefix(code)
    df, status = _cache_get(c, source, allow_stale=False)
    if status == "hit":
        return df, False
    # tdx 主源
    try:
        parsed = parse_tdx_financial(c)
    except Exception:
        parsed = None
    if parsed:
        tdf = parsed.get(source)
        if tdf is not None and not tdf.empty:
            try:
                _cache_set(c, source, tdf)
            except Exception:
                pass
            return tdf, False
    # akshare 备援
    if _AK_OK:
        try:
            with ThreadPoolExecutor(max_workers=1) as ex:
                net = ex.submit(_fetch_net, c, source).result(timeout=_AK_TIMEOUT)
            if net is not None and not net.empty:
                _cache_set(c, source, net)
                return net, False
        except (FuturesTimeout, Exception):
            pass
    df_s, _ = _cache_get(c, source, allow_stale=True)
    if df_s is not None:
        return df_s, True
    return None, False
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_tdx_financial.py::test_fundamentals_fetch_tdx_primary tests/test_tdx_financial.py::test_fundamentals_fetch_cache_hit tests/test_tdx_financial.py::test_fundamentals_fetch_akshare_fallback -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add data/fundamentals.py tests/test_tdx_financial.py
git commit -m "feat(fundamentals): fetch 改tdx主源(parse命中本source),akshare降备援"
```

---

### Task 5: 回归适配 + 全量测试 + docker 手测优质筛选超时

**Files:**
- Modify(若需): `tests/test_buffett_value.py`/`tests/test_fundamentals.py`(mock 链适配)
- 验证:`tests/` 全量 + `http://localhost:8000/web/index.html` 优质筛选页

**目标**:确认 tdx 财报主源不破坏现有 buffett/fundamentals 测试(它们 mock akshare `stock_financial_abstract`/三大表);tdx 路径在 mock 下不误触发;优质筛选首次 cache-miss 从 >75s 降至 ~20s 内。

- [ ] **Step 1: 跑 buffett/fundamentals 全量,看是否有 mock 假败**

Run: `python -m pytest tests/test_buffett_value.py tests/test_fundamentals.py tests/test_tdx_financial.py -q`
Expected: 全 PASS。若有失败,是因为测试 mock 了 `ak.stock_financial_abstract` 但未 mock `parse_tdx_financial`,导致 fetch_abstract 先走 tdx 真连网。修复:测试里 monkeypatch `fundamentals.parse_tdx_financial` 返 None(强制走 akshare mock 路径),或 mock 返与原 akshare mock 等价的数据。以最小改动让既有 mock 测试仍测 akshare 备援路径;tdx 路径由 Task1-4 的 test_tdx_financial 覆盖。

- [ ] **Step 2: 跑全量回归**

Run: `python -m pytest tests/ -q`
Expected: 全 PASS(304+ tests)。

- [ ] **Step 3: 部署 + 手测优质筛选超时**

```bash
bash deploy.sh  # 重建+健康+模块自检
# 浏览器 http://localhost:8000/web/index.html → 优质筛选 tab → 筛选优质清单
# 首次 cache-miss 应 < 75s 前端超时(tdx 财报 ~1-2s/只 × 80 / 8worker ≈ 20s),
# 不再双超时。二次重算走结果缓存秒回。
```
若仍超时:检查 `parse_tdx_financial` 是否单只 >5s(get_company_info TCP 慢),或 warmup deadline 不足;记录日志诊断。

- [ ] **Step 4: 提交回归修复(若有)**

```bash
git add tests/
git commit -m "test: 适配tdx财报主源(mock parse_tdx_financial返None保akshare备援测试路径)"
```

- [ ] **Step 5: 更新 CLAUDE.md 文档**

```bash
# CLAUDE.md:buffett/fundamentals 段补"tdx 财务分析文本解析为主源(abstract宽表+三大表转置),
# 一次解析分解缓存(financial_abstract_cache+fundamentals_cache),akshare 降备援";
# signals/portfolio/buffett 数据依赖链段更新 buffett.fetch_abstract 主源改 tdx。
git add CLAUDE.md
git commit -m "docs: CLAUDE.md 同步tdx财报主源化"
```

---

## Self-Review

1. **Spec 覆盖**:子系统 A 全覆盖(parse/主源切换/缓存/熔断/测试)。子系统 B spot 独立 plan。
2. **占位符**:无 TBD;所有代码块完整。
3. **类型一致**:`parse_tdx_financial`→dict[str,df|None];`fetch_abstract`/`fetch`→(df,stale) 不变;`_parse_cn_amount`→float|None。Task 间接口键名一致(abstract/balance/cashflow/profit)。
4. **缓存表区分**:Task3 用 buffett._cache_set(financial_abstract_cache) + fundamentals._cache_set(fundamentals_cache) 预填;Task4 用 fundamentals._cache_get/set —— 两表不混。

## Execution Handoff

Plan 1(财报)完成并验证后,再写 Plan 2(spot 全市场 tdx,collector.py+tdx_name_map)。两 plan 独立,Plan 2 不依赖 Plan 1。

执行方式:subagent-driven-development(每 task 全新 implementer 子代理 + task review)或 inline 执行。
