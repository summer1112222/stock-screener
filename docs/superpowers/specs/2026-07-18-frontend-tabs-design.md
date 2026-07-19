# 前端多 tab 页架构 · 设计文档

**日期**: 2026-07-18
**状态**: 设计已确认（分组方案 A + K线/持仓跨域决策），待转实施计划
**范围**: `web/index.html` 单文件重构（原生 JS，无构建步骤，不引入框架）

---

## 1. 背景与合规边界

现状：`web/index.html` 单页纵向堆叠 11 个 card（筛选条件/结果/回测/候选池/信号/持仓/主力动向/优质筛选/K线/巴菲特 + 动态净值/风控/IC），滚动过长，功能域混杂。

本重构是**纯前端 UI 结构调整**：

- **不新增功能、不新增后端路由、不新增表/采集源**——所有 card 内容与 JS 逻辑不变，仅重组容器。
- **合规措辞沿用现有**：各 card 的 disclaimer/dis 标签、`观察清单·非推荐·非买卖点` 等文案**原样保留**，tab 标题中性（"实时筛选/历史回测/主力动向/优质筛选"），不引入"推荐/买卖点"等措辞。
- 单文件 HTML，原生 JS，无构建步骤（沿用现有模式，CLAUDE.md 已记）。

---

## 2. 架构：4 tab + 持仓浮窗 + K 线跨 tab 跳转

```
header（标题 + 全局 update_time + 持仓浮窗触发按钮 ）
  └─ .tabs 导航：[ 实时筛选 ] [ 历史回测 ] [ 主力动向 ] [ 优质筛选 ]
      ├─ tab-panel "实时筛选"：筛选条件 + 结果
      ├─ tab-panel "历史回测"：回测研究 + 候选池 + 信号扫描 + 历史K线 + 巴菲特分析 + 动态净值/风控/IC
      ├─ tab-panel "主力动向"：主力动向
      └─ tab-panel "优质筛选"：优质筛选
  └─ .portfolio-drawer（持仓浮窗，固定右上，任意 tab 可开）
```

**分组依据**：与后端模块边界对齐——实时筛选↔`screener`、历史回测↔`backtest`、主力动向↔`data/smart_money`+`screener/smart_money`、优质筛选↔`backtest/quality`。功能内聚、切换不互相干扰。

---

## 3. tab 结构与 card 归属

| Tab | 归属 card | 后端域 |
|---|---|---|
| 实时筛选 | 筛选条件、结果 | screener |
| 历史回测 | 回测研究、候选池、信号扫描、历史K线、巴菲特分析、（动态：净值曲线/风控/IC-IR/Bootstrap） | backtest |
| 主力动向 | 主力动向 | smart_money |
| 优质筛选 | 优质筛选 | quality |

**动态 card**（回测结果渲染出的净值曲线/风控指标/IC-IR/Bootstrap）归属"历史回测"tab，随回测动作渲染。

---

## 4. tab 切换机制

- **DOM**：每个 tab 一个 `<section class="tab-panel" data-tab="...">`，含其 card。导航 `<nav class="tabs">` 一组按钮 `data-tab="..."`。
- **显隐**：点击导航按钮 → 给对应 panel 加 `.active`（`display:block`），其余 `display:none`。原生 JS，无路由。
- **状态保持**：显隐切换保留各 panel 的 DOM 与已加载数据（不重新 fetch），切换即显隐。各 card 的初始化 `xxxLoad()` 调用维持现状（页面加载时仍全跑，保兼容）。
- **默认 tab**：`实时筛选` active。
- **URL hash 同步**（可选，P2）：`#tab=回测` 深链。P1 不做。

---

## 5. K 线跨 tab 跳转

现状：候选池/信号任一行（`.clk` `data-code`）点击 → 加载历史 K 线到 K 线 card。tab 化后 K 线在"历史回测"tab，跨 tab 点行需跳转：

- 全局行点击 handler（已委托在容器层）→ 触发 `loadKline(code, universe)` + `switchTab('历史回测')`。
- `switchTab` 切到回测 tab 后，K 线 card 已在 DOM，`loadKline` 直接渲染。
- **来源 tab 不限**：优质筛选/主力动向/候选池任一行点击都跳回测 tab 看 K 线。
- 跳转后用户手动切回原 tab（状态保留，不丢数据）。

---

## 6. 持仓浮窗（抽屉）

现状：持仓跟踪 card 在主体流里。改为固定右上浮窗/抽屉：

- **触发**：header 右侧加"持仓"按钮（常驻），点击 toggle `.portfolio-drawer` 显隐。
- **抽屉**：fixed 定位右上，含原持仓 card 的全部内容（记录买入表 + close 按钮 + disclaimer）。
- **跨域记录**：任意 tab 的行右键/按钮"记录买入"→ 打开抽屉预填 code → 用户确认入库。P1 复用现有 portfolio POST 逻辑，仅迁移到抽屉。
- **浮层**：z-index 高于 tab panel，半透明遮罩可选（P1 不加遮罩，直接 fixed 覆盖）。

---

## 7. 样式与无构建步骤

- 新增 `.tabs`/`.tab-panel`/`.portfolio-drawer` CSS，沿用现有 `--surface/--border/--primary` 变量与 `.card` 圆角风格。
- 导航按钮 active 态用 `--primary` 高亮。
- **不引入**框架/打包工具，原生 JS + `<style>` 内联（CLAUDE.md 约束）。
- 各 card 的 `LABEL`/`fmtNum`/`sparkline` 等全局工具函数不变。

---

## 8. 测试与验证

前端无单测（沿用现有模式，`tests/` 只测 Python 后端）。

- **py_compile**：不适用（纯 HTML/JS）。
- **运行时验证（Docker）**：`docker compose up --build -d` → `http://localhost:8000/web/index.html` 手动验证：
  - 4 tab 切换显隐正确、状态保持（切回不丢数据）。
  - 优质筛选/主力动向行点击 → 跳回测 tab + K 线加载。
  - 持仓按钮 → 抽屉显隐 + 记录买入 + close。
  - 各 disclaimer 文案原样保留。
- **后端单测**不受影响（不动 Python），`python -m pytest tests/ -q` 应仍 44 passed。

---

## 9. 改动检查清单（对齐 CLAUDE.md）

- **不新增表/采集源/SQLite 列/路由**——纯前端 `web/index.html` 重构。
- 新增 card/字段 → 无（仅重组容器）。
- 合规 disclaimer → 原样保留各 card 的 dis/disclaimer，tab 标题中性。
- CLAUDE.md → 架构小节"web/index.html 单页前端"补一句"4 tab 分组（实时筛选/历史回测/主力动向/优质筛选）+ 持仓浮窗 + K线跨tab跳转"。
- 不破坏现有 NaN→None、pandas 3.0 等后端约束（不触后端）。
