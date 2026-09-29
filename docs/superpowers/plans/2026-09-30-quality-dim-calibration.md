# quality 共振维度权重 IC 校准研究 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 给 quality 共振维度权重提供首次量化依据——对可全历史重建的口径1/4 做 rank-IC/分档实证校准，产出只读研究报告 + 权重建议；口径2/3/5 诚实标注不可校准。

**Architecture:** 扩展现有 `scripts/quality_diag.py`（已含口径1 全历史 rank-IC 管线），新增口径4「当日多信号触发数 hit_count」的滚动面板 rank-IC 校准段，渲染进报告；对真实库 `./stock.db` 运行产出 `docs/quality-dim-calibration-2026-09-30.md`。不改生产权重。

**Tech Stack:** Python 3.12 / pandas / numpy / sqlite3(只读) / `backtest.eval`(forward_returns/ic_series/ic_summary/decile_backtest) / `backtest.signals._rsi`。

**Spec:** `docs/superpowers/specs/2026-09-30-quality-dim-calibration-design.md`

## Global Constraints

- **只读研究**：绝不写库、不触网、不改任何排序签名、不新增路由/采集。脚本只用 `sqlite3.connect(file:...?mode=ro, uri=True)`。
- **不动生产权重**：`backtest/quality.py::_DEFAULT_DIM_WEIGHTS` 一律不改。报告只给建议，落地另走 plan。
- **诚实标注**：口径2/3/5 无长历史分位，报告中明确"数据不可历史重建，维持经验先验"，绝不臆造校准数字。
- **措辞合规**：报告用语"机械统计诊断/研究优先级/非买卖信号"，不荐股不承诺收益。
- **测试不触网**：纯函数合成数据 mock，测试文件放 `tests/`。
- **NaN→None**：任何要进 JSON 的输出用 `df.astype(object).where(pd.notna(df), None)`（本任务无 JSON 输出，但 report 渲染遇到 NaN 用 "—" 占位，防 `ic=None` 崩）。
- 现有 `stock.db`（52 只 × 1665 日）为真实数据源；截面偏薄（52 codes）须在报告「局限」注明。

## Review Focus

- **空/过短面板**：close 为空或 `<25+k` 日时 `backtest_signals` 会返 error——quality_diag 口径4 段必须优雅降级输出"无触发/历史不足"，不崩。→ Task 1 测试 `test_signal4_empty_close`
- **hit_count 全零窗口**（窗口内无任何信号触发）：ic_series 对全零因子应返回 ic=None 而非 NaN 崩溃。→ Task 1 测试 `test_signal4_no_triggers`
- **截面太薄**（52 codes）rank-IC 噪声大：报告必须打印/注明 codes 数，避免把薄截面的弱 IC 当强证据。→ Task 1 stderr 打印 + Task 4 报告局限段
- **amount NaN**：`_signal_hits_panel` 里 volume_surge 依赖 amount，`amount` 含 NaN 时掩码要 `.notna()` 守卫。→ Task 1 实现内联守卫
- **k∈{5,20} 双窗口**：单 k 结论不可靠，两窗口都报。→ Task 1 按 ks 循环

---

### Task 1: 口径4 多信号触发数 rank-IC 校准段

**Files:**
- Modify: `scripts/quality_diag.py`（在 `panel_diag` 后新增两个纯函数）
- Test: `tests/test_quality_diag.py`

**Interfaces:**
- Consumes: `backtest.eval.forward_returns/ic_series/ic_summary/decile_backtest`（签名已在 quality_diag 现有 `panel_diag` 用到）、`backtest.signals._rsi(close, 14)`。
- Produces: `_signal_hits_panel(close, amount) -> pd.DataFrame`（code×date 的当日触发信号数 0-5，日期索引）；`panel_diag_signal4(close, amount, ks=(5,20)) -> dict`（结构 `{hit_count: {"by_k": {k: {"ic":..., "decile":...}}}}`，复用 `panel_diag` 返回结构）。

- [ ] **Step 1: 写口径4 纯函数测试（先红）**

在 `tests/test_quality_diag.py` 追加：

```python
from scripts import quality_diag as qd
import pandas as pd
import numpy as np


def _tiny_panel():
    # 5 只 × 60 日合成价/量，人工构造 ma_breakout + volume_surge 触发
    idx = pd.date_range("2026-01-01", periods=60, freq="B")
    codes = [f"S{i}" for i in range(5)]
    rng = np.random.default_rng(0)
    close = pd.DataFrame({
        c: 10 + np.cumsum(rng.normal(0, 0.1, 60)) for c in codes
    }, index=idx)
    amount = pd.DataFrame({
        c: np.where(rng.random(60) > 0.7, 3e8, 1e8) for c in codes
    }, index=idx)
    return close, amount


def test_signal_hits_panel_shape_and_range():
    close, amount = _tiny_panel()
    hits = qd._signal_hits_panel(close, amount)
    assert hits.shape == close.shape
    assert (hits.values >= 0).all() and (hits.values <= 5).all()


def test_signal4_no_triggers_all_zero():
    # 平盘（无 ma_breakout/momentum 等触发）→ hit_count 全 0，ic 应返 None 不崩
    idx = pd.date_range("2026-01-01", periods=60, freq="B")
    close = pd.DataFrame({c: 10.0 for c in ["A", "B"]}, index=idx)
    amount = pd.DataFrame({c: 1e8 for c in ["A", "B"]}, index=idx)
    hits = qd._signal_hits_panel(close, amount)
    assert (hits.values == 0).all()
    out = qd.panel_diag_signal4(close, amount, ks=(5,))
    ic = out["hit_count"]["by_k"][5]["ic"]
    assert ic["ic"] is None or ic["ic"] == 0.0  # 全零因子不产出 NaN


def test_signal4_empty_close():
    close = pd.DataFrame()
    amount = pd.DataFrame()
    out = qd.panel_diag_signal4(close, amount, ks=(5,))
    assert out is None or out == {}
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_quality_diag.py -v -k signal`
Expected: FAIL（`AttributeError: module 'scripts.quality_diag' has no attribute '_signal_hits_panel'`）

- [ ] **Step 3: 实现 `_signal_hits_panel` + `panel_diag_signal4`**

在 `scripts/quality_diag.py` 的 `panel_diag` 函数之后追加（公式镜像 `backtest/signals.py:206-216`，须保持同步）：

```python
def _signal_hits_panel(close: pd.DataFrame, amount: pd.DataFrame | None) -> pd.DataFrame:
    """口径4「当日多信号触发数」滚动面板：逐 code×date 统计当日触发的 5 类
    OHLCV 信号数(0-5)。公式镜像 backtest/signals.py 的 masks,须保持同步。
    供 rank-IC 判定「触发信号越多 → 前视收益是否越好」的单调性。前视安全。"""
    from backtest.signals import _rsi
    if close is None or close.empty:
        return pd.DataFrame()
    ma5 = close.rolling(5).mean()
    ma20 = close.rolling(20).mean()
    rsi = _rsi(close, 14)
    hits = pd.DataFrame(0, index=close.index, columns=close.columns)
    hits += ((close.shift(1) <= ma20.shift(1)) & (close > ma20)).astype(int)          # ma_breakout
    hits += ((ma5.shift(1) <= ma20.shift(1)) & (ma5 > ma20)).astype(int)              # golden_cross
    if amount is not None and not amount.empty:
        vol_avg5 = amount.rolling(5).mean()
        hits += ((amount > vol_avg5 * 2) & amount.notna() & (vol_avg5 > 0)).astype(int)  # volume_surge
    hits += (rsi < 30).astype(int)                                                    # rsi_oversold
    hits += ((close.pct_change(20) > 0) & (close > close.shift(1))).astype(int)       # momentum_up
    return hits


def panel_diag_signal4(close: pd.DataFrame, amount: pd.DataFrame | None,
                       ks=(5, 20)) -> dict | None:
    """对口径4 触发数面板跑滚动 rank-IC + 5 档。复用 eval 工具链。
    空/过短面板返 None(由调用方降级)。返回 {factor: {decile, ic, by_k}}，同 panel_diag。"""
    from backtest import eval as bt_eval
    hits = _signal_hits_panel(close, amount)
    if hits is None or hits.empty or len(hits) < 25 + max(ks):
        return None
    out: dict = {"hit_count": {"by_k": {}}}
    for k in ks:
        fwd = bt_eval.forward_returns(close, k)
        ic = bt_eval.ic_summary(bt_eval.ic_series(hits, fwd))
        dbt = bt_eval.decile_backtest(hits, fwd, 5)
        out["hit_count"]["by_k"][k] = {"ic": ic, "decile": dbt}
    main_k = max(ks)
    out["hit_count"]["decile"] = out["hit_count"]["by_k"][main_k]["decile"]
    out["hit_count"]["ic"] = out["hit_count"]["by_k"][main_k]["ic"]
    return out
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/test_quality_diag.py -v -k signal`
Expected: PASS

- [ ] **Step 5: 编译检查**

Run: `python -m compileall scripts/quality_diag.py`
Expected: 无错误

- [ ] **Step 6: Commit**

```bash
git add scripts/quality_diag.py tests/test_quality_diag.py
git commit -m "feat(scripts): quality_diag 新增口径4 多信号触发数 rank-IC 校准段(镜像 signals.py masks,前视安全)"
```

---

### Task 2: 报告渲染扩展口径4 段

**Files:**
- Modify: `scripts/quality_diag.py::render_report`
- Test: `tests/test_quality_diag.py`

**Interfaces:**
- Consumes: Task 1 的 `panel_diag_signal4` 返回结构（`{"hit_count": {"by_k": {k: {"ic","decile"}}, "decile","ic"}}`）。
- Produces: 报告 markdown 新增「口径4 多信号」小节。

- [ ] **Step 1: 加渲染测试**

```python
def test_render_report_includes_signal4():
    panel = {
        "momentum_20": {"by_k": {5: {"ic": {"ic": 0.02, "win_rate": 0.5},
                                    "decile": {"long_short": {5: 0.1}}}},
                        "decile": {"long_short": {5: 0.1}},
                        "ic": {"ic": 0.02, "win_rate": 0.5}},
        "hit_count": {"by_k": {5: {"ic": {"ic": 0.03, "win_rate": 0.6},
                                   "decile": {"long_short": {5: 0.2}}}},
                      "decile": {"long_short": {5: 0.2}},
                      "ic": {"ic": 0.03, "win_rate": 0.6}},
    }
    report = qd.render_report(panel, None)
    assert "口径4" in report and "hit_count" in report
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_quality_diag.py -k render_report_includes_signal4`
Expected: FAIL（报告无口径4 段）

- [ ] **Step 3: 扩展 `render_report`**

在 `render_report` 里「一、口径1」表格之后插入「二、口径4 多信号触发数」小节（复用同一渲染循环，因子名为 `hit_count`）：

```python
def render_report(panel: dict, audit: dict | None) -> str:
    lines = ["# quality 筛选逻辑诊断报告", ""]
    lines.append("## 一、口径1(风险调整) 构成因子 · 滚动分档 + Rank IC")
    lines.append("")
    lines.append("| 因子 | k | IC | IC胜率 | Q5/Q1多空 | 单调性 |")
    lines.append("|---|---|---|---|---|---|")
    for fname, fout in panel.items():
        if fname == "hit_count":
            continue  # 口径4 单独成节
        for k, byk in fout["by_k"].items():
            ic = byk["ic"]; ls = byk["decile"].get("long_short") or {}
            ls_val = list(ls.values())[-1] if ls else None
            ic_txt = f"{ic['ic']:+.3f}" if ic.get("ic") is not None else "—"
            wr_txt = f"{ic['win_rate']:.0%}" if ic.get("win_rate") is not None else "—"
            ls_txt = f"{ls_val:.3f}" if ls_val is not None else "—"
            lines.append(f"| {fname} | {k} | {ic_txt} | {wr_txt} | {ls_txt} | — |")
    # 口径4 小节
    if "hit_count" in panel:
        lines.append("")
        lines.append("## 二、口径4(多信号) 当日触发数 · Rank IC")
        lines.append("")
        lines.append("| 因子 | k | IC | IC胜率 | Q5/Q1多空 | 单调性 |")
        lines.append("|---|---|---|---|---|---|")
        fout = panel["hit_count"]
        for k, byk in fout["by_k"].items():
            ic = byk["ic"]; ls = byk["decile"].get("long_short") or {}
            ls_val = list(ls.values())[-1] if ls else None
            ic_txt = f"{ic['ic']:+.3f}" if ic.get("ic") is not None else "—"
            wr_txt = f"{ic['win_rate']:.0%}" if ic.get("win_rate") is not None else "—"
            ls_txt = f"{ls_val:.3f}" if ls_val is not None else "—"
            lines.append(f"| hit_count | {k} | {ic_txt} | {wr_txt} | {ls_txt} | — |")
    # 后续 audit 小节编号顺延为「三」
    ...
```

> 注意：把原「二、当前 main 清单审计」改为「三、…」，原「局限」保持末节。

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/test_quality_diag.py -v`
Expected: PASS（原口径1 测试 + 新渲染测试全绿）

- [ ] **Step 5: Commit**

```bash
git add scripts/quality_diag.py tests/test_quality_diag.py
git commit -m "feat(scripts): quality_diag 报告新增口径4 多信号触发数小节"
```

---

### Task 3: 主流程接入口径4 + 运行全量测试

**Files:**
- Modify: `scripts/quality_diag.py::main`

**Interfaces:**
- Consumes: `_load_daily_panels`(已有) + Task 1 的 `panel_diag_signal4` + Task 2 的 `render_report`。
- Produces: `panel_diag` + `panel_diag_signal4` 合并的 dict 传给 `render_report`；stderr 打印样本规模与不可校准口径提示。

- [ ] **Step 1: `main` 合并口径4**

在 `main` 的 `panel = panel_diag(close, amount, ks=ks)` 之后追加：

```python
    panel4 = panel_diag_signal4(close, amount, ks=ks)
    if panel4:
        panel.update(panel4)
    # stderr 打印样本规模 + 不可校准口径提示(诚实标注)
    print(f"[quality_diag] 口径4 触发数校准: {len(codes)} 票 × {len(close)} 日", file=sys.stderr)
    print("[quality_diag] 口径2/3/5 无长历史分位,不可历史重建,保留经验先验(见报告局限)", file=sys.stderr)
```

- [ ] **Step 2: 运行全量测试**

Run: `python -m pytest tests/test_quality_diag.py -v`
Expected: PASS

- [ ] **Step 3: 编译 + 全仓静态检查**

Run: `python -m compileall scripts backtest`
Expected: 无错误

- [ ] **Step 4: Commit**

```bash
git add scripts/quality_diag.py
git commit -m "feat(scripts): quality_diag main 合并口径4 校准段,stderr 诚实标注口径2/3/5 不可重建"
```

---

### Task 4: 跑真实库生成研究报告

**Files:**
- Create: `docs/quality-dim-calibration-2026-09-30.md`（脚本对真实库运行后捕获 stdout）

**Interfaces:**
- Consumes: `scripts/quality_diag.py` 全流程 + 真实库 `./stock.db`。

- [ ] **Step 1: 运行校准脚本对真实库**

Run: `python -m scripts.quality_diag --db ./stock.db --days 2000 --ks 5,20 --out docs/quality-dim-calibration-2026-09-30.md`
Expected: stderr 打印样本规模；`docs/quality-dim-calibration-2026-09-30.md` 生成，含口径1 因子表 + 口径4 hit_count 表 + 局限。

- [ ] **Step 2: 审读报告真实数字，写「权重建议」段**

打开生成的 `docs/quality-dim-calibration-2026-09-30.md`，核对：
- 口径1：哪些因子 rank-IC 显著为正（|ic|≥0.02 且胜率≥0.53）？方向是否符合"反转为好"？
- 口径4：hit_count 与 k 前视收益是正相关还是噪声？触发越多是否真的越好？
- 据此在报告末尾追加「五、权重建议」，逐条标「有实证依据 / 经验先验保留」。
- 若 ic 微弱/噪声（52 codes 薄截面），建议措辞保守："方向性参考，不构成强证据，权重维持经验先验或仅微调"。

- [ ] **Step 3: 补「局限」段样本注明**

确保报告「局限」含：入选池按当前 `stock_spot`、仅 52 codes 薄截面、口径2/3/5 不可历史重建维持先验。

- [ ] **Step 4: Commit**

```bash
git add docs/quality-dim-calibration-2026-09-30.md
git commit -m "docs: quality 口径1/4 实证校准研究报告(rank-IC+权重建议,口径2/3/5 诚实标注不可重建)"
```

---

## Self-Review

**Spec coverage:**
- §3.1 口径1 校准 → 既有 `panel_diag`，Task 1/4 保留 ✅
- §3.2 口径4 事件式校准 → Task 1（hit_count 面板 rank-IC，非逐信号表，更贴合"触发数单调性"）✅
- §3.3 口径2/3/5 诚实标注 → Task 3 stderr + Task 4 局限段 ✅
- §4 产出物五条 → Task 4 报告（1/2 表、3 实证/先验标注、4 权重建议、5 局限免责）✅
- §5 落地文件 → Task 1-4 ✅；`quality.py` 权重明确不动 ✅
- §6 合规 → Global Constraints + 报告局限 ✅

**Placeholder scan:** 无 TBD/TODO；Task 4 Step 2 是"审读真实数字写建议"——因数字依赖运行结果，属诚实的研究流程而非占位，报告中会落具体数字。✅

**Type consistency:** `panel_diag_signal4` 返回结构同 `panel_diag`（`{factor:{"by_k","decile","ic"}}`），`render_report` 循环消费一致；`hits` 为 DataFrame(索引 date × 列 code)，与 `forward_returns`/`ic_series` 接受面板一致。✅

**Review Focus 覆盖：** 空面板(`test_signal4_empty_close`)、全零触发(`test_signal4_no_triggers`)、薄截面(stderr 打印 + Task 4 局限)、amount NaN(内联 `.notna()` 守卫)、双 k(按 ks 循环)。✅

> 说明：真实报告数字依赖运行时的 `stock.db` 数据，Task 4 Step 2 由执行者读生成文件后据实填写建议——这是研究流程的必然步骤，非计划占位。
