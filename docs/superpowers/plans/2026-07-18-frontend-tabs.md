# 前端多 tab 页架构 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 `web/index.html` 单页 11 个纵向 card 重组为 4 tab（实时筛选/历史回测/主力动向/优质筛选）+ 持仓右上浮窗 + K线跨 tab 跳转，纯前端重构不动后端。

**Architecture:** 方案 A——按后端域分组 4 tab，每个 tab 一个 `.tab-panel`，导航 `.tabs` 切换显隐（原生 JS，状态保持）。K 线在回测 tab，全局行点击 handler（`document.addEventListener('click', tr.clk)` 467-470 调 `hkRun`）加 `switchTab('历史回测')` 实现跨 tab 跳转。持仓 card 提到 fixed 右上抽屉，header 加按钮 toggle。

**Tech Stack:** 原生 HTML/CSS/JS，无构建步骤，单文件 `web/index.html`。

**Spec:** `docs/superpowers/specs/2026-07-18-frontend-tabs-design.md`

## Global Constraints

- **纯前端**：不动 Python/表/路由/采集源；`tests/` 不受影响（应仍 44 passed）。
- **合规措辞沿用**：各 card 的 `dis`/disclaimer 文案原样保留，tab 标题中性（"实时筛选/历史回测/主力动向/优质筛选"），不引入"推荐/买卖点"。
- **无构建步骤**：原生 JS + 内联 `<style>`，不引框架。
- **状态保持**：tab 切换显隐不重新 fetch，各 card 初始化 `xxxLoad()` 维持现状（页面加载时仍跑）。
- **card-t 锚点**（grep 已确认）：筛选条件(78)/结果(99)/回测研究(108)/候选池(134)/信号扫描(151)/持仓跟踪(162)/主力动向(177)/优质筛选(188)/历史K线(200)/巴菲特分析(210)；行点击 handler 467-470 调 `hkRun(c)`；持仓 `pfLoad`/`pfAdd`/`pfClose`/`#pfAdd`。

---

## File Structure

- **Modify:** `web/index.html` — 加 `.tabs`/`.tab-panel`/`.portfolio-drawer` CSS + nav + switchTab + 把现有 card 包进对应 panel + 持仓抽屉化 + 行点击加 switchTab。
- **Modify:** `CLAUDE.md` — 架构小节补 tab 结构说明。

**接口（跨 task 一致）：**
```javascript
function switchTab(name){ /* 给 [data-tab=name] 的 panel 加 .active，其余移除；导航按钮同理 */ }
function togglePortfolio(){ /* 抽屉显隐 toggle */ }
// 行点击 handler 内加: switchTab('历史回测');
```

---

### Task 1: tab 框架（CSS + nav + switchTab + 4 空 panel 骨架）

**Files:** Modify `web/index.html`

- [ ] **Step 1: 加 CSS**（在 `<style>` 内 `.card-t{...}` 规则后追加）

```css
  .tabs{display:flex;gap:4px;border-bottom:1px solid var(--border);margin-bottom:16px;flex-wrap:wrap}
  .tabs button{background:transparent;border:none;color:var(--muted);font-size:13px;
    font-weight:600;padding:8px 14px;cursor:pointer;border-bottom:2px solid transparent;
    font-family:var(--sans);letter-spacing:.3px}
  .tabs button.active{color:var(--text);border-bottom-color:var(--primary)}
  .tabs button:hover{color:var(--text)}
  .tab-panel{display:none}
  .tab-panel.active{display:block}
  .portfolio-drawer{position:fixed;top:0;right:-420px;width:400px;max-width:92vw;height:100vh;
    background:var(--surface);border-left:1px solid var(--border);box-shadow:-8px 0 24px rgba(0,0,0,.4);
    z-index:50;overflow-y:auto;padding:16px 18px;transition:right .2s}
  .portfolio-drawer.open{right:0}
  .portfolio-drawer .pf-head{display:flex;justify-content:space-between;align-items:center;
    margin-bottom:12px;padding-bottom:10px;border-bottom:1px solid var(--border)}
  .pf-btn{background:rgba(59,130,246,.12);color:var(--primary);border:1px solid rgba(59,130,246,.3);
    border-radius:6px;padding:5px 10px;font-size:12px;cursor:pointer;font-family:var(--sans)}
```

- [ ] **Step 2: 加 nav + 4 空 panel + 持仓按钮 + 抽屉壳**

在 `header` 闭合标签后、第一个 `.card` 之前插入：
```html
<nav class="tabs">
  <button class="active" data-tab="screen">实时筛选</button>
  <button data-tab="backtest">历史回测</button>
  <button data-tab="smart">主力动向</button>
  <button data-tab="quality">优质筛选</button>
  <button class="pf-btn" style="margin-left:auto" id="pfOpen" type="button">持仓</button>
</nav>
<section class="tab-panel active" data-tab="screen"></section>
<section class="tab-panel" data-tab="backtest"></section>
<section class="tab-panel" data-tab="smart"></section>
<section class="tab-panel" data-tab="quality"></section>
<aside class="portfolio-drawer" id="pfDrawer">
  <div class="pf-head"><b>持仓跟踪</b><button class="pf-btn" id="pfClose2" type="button">关闭</button></div>
  <div id="pfDrawerBody"></div>
</aside>
```
（Task 2 把现有 card 移入对应 panel；Task 4 把持仓 card 内容移入 `#pfDrawerBody`。）

- [ ] **Step 3: 加 switchTab + togglePortfolio JS**（在 `</script>` 前的初始化区）

```javascript
function switchTab(name){
  document.querySelectorAll('.tabs button[data-tab]').forEach(b=>b.classList.toggle('active',b.dataset.tab===name));
  document.querySelectorAll('.tab-panel').forEach(p=>p.classList.toggle('active',p.dataset.tab===name));
}
document.querySelectorAll('.tabs button[data-tab]').forEach(b=>b.onclick=()=>switchTab(b.dataset.tab));
function togglePortfolio(){document.getElementById('pfDrawer').classList.toggle('open');}
document.getElementById('pfOpen').onclick=togglePortfolio;
document.getElementById('pfClose2').onclick=()=>document.getElementById('pfDrawer').classList.remove('open');
```

- [ ] **Step 4: Verify 框架**

Run: `docker compose up --build -d` 后浏览器开 `http://localhost:8000/web/index.html`
Expected: 4 tab 按钮 + 持仓按钮可见，点 tab 切换 panel 显隐（panel 空，Task 2 填），持仓按钮开闭抽屉。

- [ ] **Step 5: Commit**

```bash
git add web/index.html
git commit -m "feat(web): tab framework (css + nav + switchTab + 4 panels + portfolio drawer shell)"
```

---

### Task 2: 迁移 card 进对应 panel（含 K线/巴菲特重排）

**Files:** Modify `web/index.html`

**顺序问题**：现有 card 顺序为 筛选(78)→结果(99)→回测(108)→候选池(134)→信号(151)→持仓(162)→主力(177)→优质(188)→历史K线(200)→巴菲特(210)。backtest panel 要含 K线+巴菲特，但它们在优质之后，不重排则 panel 切分断裂。

- [ ] **Step 1: 重排——把 历史K线+巴菲特 两 card 剪到 信号 之后、持仓 之前**

Edit：剪切 `历史K线`(200) 到 `巴菲特分析`(210) 整块（含两者之间的内容），粘贴到 `信号扫描`(151) card `</div>` 之后、`持仓跟踪`(162) card 之前。

重排后顺序：筛选→结果→回测→候选池→信号→[历史K线→巴菲特]→持仓→主力→优质。

- [ ] **Step 2: 包 screen panel**

`筛选条件` card 前：把空 `<section class="tab-panel active" data-tab="screen"></section>` 改为 `<section class="tab-panel active" data-tab="screen">`；`结果` card `</div>`（`回测研究` 前）后改为 `</section>`（替换原空 `</section>`）。

- [ ] **Step 3: 包 backtest panel**

`回测研究` card 前：空 `<section data-tab="backtest"></section>` → `<section class="tab-panel" data-tab="backtest">`；`巴菲特分析` card `</div>`（`持仓跟踪` 前）后加 `</section>`。

- [ ] **Step 4: 包 smart + quality panel**

`主力动向` card 前：空 `<section data-tab="smart"></section>` → `<section class="tab-panel" data-tab="smart">`；`主力动向` card `</div>` 后加 `</section>`。
`优质筛选` card 前：空 `<section data-tab="quality"></section>` → `<section class="tab-panel" data-tab="quality">`；`优质筛选` card `</div>`（文件末尾前）后加 `</section>`。

注：`持仓跟踪`(162) card 此刻夹在 backtest panel `</section>` 之后、smart panel 之前——它暂在所有 panel 之外（Task 4 进抽屉）。

- [ ] **Step 5: Verify 迁移**

Run: `grep -n 'class="card-t"' web/index.html | head -20`
Expected: card 顺序 筛选/结果/回测/候选池/信号/历史K线/巴菲特/持仓/主力/优质；各 card 被正确 `<section data-tab=...>` 包裹。
Docker 浏览器：4 tab 各显对应 card，切换正常，持仓 card 暂游离（Task 4 处理）。

- [ ] **Step 6: Commit**

```bash
git add web/index.html
git commit -m "feat(web): migrate cards into 4 tab panels + reorder kline/buffett"
```

---

### Task 3: K 线跨 tab 跳转

**Files:** Modify `web/index.html`

- [ ] **Step 1: 行点击 handler 加 switchTab**

Edit 行 467-470 handler，在 `hkRun(c)` 前加 `switchTab('backtest')`：
```javascript
document.addEventListener('click',e=>{
  const tr=e.target.closest('tr.clk'); if(!tr) return;
  const c=tr.dataset.code;
  if(c){ document.getElementById('hkUni').value = c.startsWith('sh')||c.startsWith('sz')||c.startsWith('bj')?'stock':'ETF';
    switchTab('backtest'); hkRun(c);}
});
```

- [ ] **Step 2: Verify 跳转**

Docker 浏览器：在"优质筛选"tab 点任一行 → 自动跳"历史回测"tab + K线加载该 code。在"主力动向"tab 点行同理。

- [ ] **Step 3: Commit**

```bash
git add web/index.html
git commit -m "feat(web): cross-tab kline jump on row click"
```

---

### Task 4: 持仓跟踪抽屉化

**Files:** Modify `web/index.html`

- [ ] **Step 1: 持仓 card 内容移入抽屉**

把游离的 `持仓跟踪`(162) card 整块（含 `记录买入` 表单 `#pfAdd`、`pfOut`、dis）剪切到 `#pfDrawerBody`（Task 1 抽屉壳内）。原位置删除。

Edit：`<div id="pfDrawerBody"></div>` 替换为持仓 card 的全部内容（去掉外层 `.card` 包装，直接放 card-t 标题 + 表单 + 输出 + dis）。

- [ ] **Step 2: 验持仓函数仍工作**

`pfLoad`/`pfAdd`/`pfClose` JS 不变（仍 `fetch /api/portfolio`）。确认 `document.getElementById('pfAdd').onclick=pfAdd`（444）仍指向抽屉内按钮（id 不变即可）。

- [ ] **Step 3: Verify 抽屉**

Docker 浏览器：点 header"持仓"按钮 → 抽屉滑入，显示记录买入表 + 持仓列表 + close 按钮；记录买入 POST 正常；close 滑出。

- [ ] **Step 4: Commit**

```bash
git add web/index.html
git commit -m "feat(web): portfolio card -> fixed drawer"
```

---

### Task 5: CLAUDE.md + 全量验证

**Files:** Modify `CLAUDE.md`

- [ ] **Step 1: 更新 CLAUDE.md 架构小节**

`web/index.html 单页前端` 那句补：`4 tab 分组（实时筛选/历史回测/主力动向/优质筛选）+ 持仓右上浮窗 + K线跨tab跳转（行点击 switchTab('历史回测')+hkRun）`。

- [ ] **Step 2: 后端单测不受影响**

Run: `python -m pytest tests/ -q`
Expected: 44 passed（未动 Python）

- [ ] **Step 3: Docker 全量运行时验证**

Run: `docker compose up --build -d` → `http://localhost:8000/web/index.html`
验证清单：
- 4 tab 切换显隐、状态保持（切回不丢数据）
- 默认 实时筛选 active
- 优质/主力 tab 行点击 → 跳回测 tab + K线加载
- 持仓按钮 → 抽屉显隐 + 记录买入 + close
- 各 disclaimer 文案原样保留
- 筛选/回测/候选池/信号/巴菲特/主力/优质 各功能正常

- [ ] **Step 4: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: tab structure in CLAUDE.md"
```

---

## Self-Review Notes

- **Spec coverage**: §2架构→T1框架+T2迁移; §3card归属→T2; §4切换→T1switchTab; §5K线跳转→T3; §6持仓浮窗→T4; §7样式→T1 CSS; §8验证→T5; §9检查清单→T5.
- **Card 重排必要性**: T2 Step 1 明确 K线+巴菲特须移到信号后才能连续包进 backtest panel（现有顺序它们在优质后，不重排 panel 切分断裂）。
- **不动后端**: 全 task 仅 web/index.html + CLAUDE.md，tests/ 不受影响。
- **Runtime verify**: 前端无单测，每 task 靠 Docker 浏览器手验；后端 pytest T5 确认 44 passed。
