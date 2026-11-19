# 蓬蒿者小程序（三清单随身客户端）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为已批准设计（spec）实现微信小程序"蓬蒿者"首期三页面（主力动向/优质筛选/次日强势），原生 WXML/WXSS/JS，后端零改动。

**Architecture:** 新建 `miniprogram/` 目录：纯 CommonJS 模块（小程序原生支持 `require`），聚合/格式化/视图逻辑下沉到 `utils/*.js` 纯函数，用 Node 内建 `node --test` 做 TDD；`.wxml` 页面壳保持薄（只做 setData 与事件绑定）。后端不碰一行。

**Tech Stack:** 微信小程序原生（WXML/WXSS/JS，无 npm 无框架）；Node 20 `node --test`（仅测试用，宿主已装 v20.19.4）；现有 FastAPI 后端（HTTP 直连）。

**Spec:** `docs/superpowers/specs/2026-11-18-miniprogram-penghaozhe-design.md`（含页面字段清单、参数定案、部署方式——执行每个任务前先读对应小节）

## Global Constraints

- **后端零改动**：`api/`、`data/`、`screener/`、`backtest/`、`scripts/`、`web/` 不许改（收尾跑全量 pytest 证明无回归）。
- **个人主体小程序**：禁用 `web-view` 组件；不上架，走开发版/体验版调试模式。
- **无 npm/无框架/无构建**：不引入 uni-app/Taro/组件库；纯手写。
- `BASE_URL` 只存在于 `miniprogram/config.js` 一处。
- disclaimer 文案照后端返回口径（"机械排序观察清单，非荐股非买卖信号，盈亏自负"），不新造措辞；每页页脚固定显示。
- 提交风格：conventional commits，`feat(miniprogram):`/`docs(CLAUDE.md):`，中文主题+正文动机，trailer `Co-Authored-By: Claude Code <noreply@anthropic.com>`。
- 小程序 JS 用 CommonJS（`require`/`module.exports`）——这是 node 测试能直接 require 的前提，不许写 ESM。
- 测试命令：`node --test tests/miniprogram/`（所有 JS 纯函数测试）；JSON/WXML 语法用 node 单行校验；DevTools/真机验证归 Task 8 用户联调。

## Review Focus

（spec 未明说、但最可能坑到使用者的五类输入；每行后标注归属任务的测试）

1. **后端没开/网络不可达**（手机自用最高频故障）：`request` 失败必须给出"后端不可达（本机服务开着吗？）"文案并让页面显示空态，不得白屏或未捕获异常 → Task 2 测试。
2. **股数通道与金额通道单位混比**：十大股东/高管/限售是"股"，混入金额聚合会把 17 万行股数当净额求和，数字爆炸 → Task 3 测试（ SHARES 通道不入金额/强度聚合）。
3. **quality 冷缓存 60-67s 被 60s 默认超时掐断**：`app.json` `networkTimeout.request` 必须 ≥180000，且 quality 请求失败重试一次 → Task 2（重试逻辑测试）+ Task 6（app.json 值校验步骤）。
4. **字段缺失**（`quality_pct`/`mf_phase`/`factor_scores.mom_5_1`/`dim_scores[3]` 等为 null/undefined）：必须显示 "—"，不得渲染出 `NaN`/`undefined`/`0` 伪造值 → Task 3/4/7 纯函数测试断言缺失路径。
5. **主力上千行全量 setData 卡死**：必须首屏 50 行截断+展开按钮 → Task 3（`capRows` 测试）+ Task 5（页面只用截断后数组 setData）。

---

### Task 1: 脚手架 + fmt 工具（含 node 测试基建）

**Files:**
- Create: `miniprogram/app.js`、`miniprogram/app.json`、`miniprogram/app.wxss`、`miniprogram/config.js`、`miniprogram/project.config.json`
- Create: `miniprogram/pages/smart/`、`miniprogram/pages/quality/`、`miniprogram/pages/nextday/` 各含 `.js/.wxml/.wxss/.json` 占位（标题对、内容 "建设中"）
- Create: `miniprogram/utils/fmt.js`
- Test: `tests/miniprogram/test_fmt.js`

**Interfaces:**
- Produces: `fmtNum(v: number|null): string`——万/亿缩写（移植 `web/index.html` 的 fmtNum 语义：|v|≥1e8 → "1.63亿"，≥1e4 → "822.74万"，否则原值两位小数；null/undefined/NaN → "—"）；`fmtFixed(v: number|null, n=2): string`（定长小数，缺失→"—"）；`config.js` 导出 `{ BASE_URL: 'http://192.168.1.100:8000' }`（占位 IP，Task 8 用户改）。
- Produces: `app.json` 含 3 pages + tabBar（text-only，不配 iconPath）+ `"networkTimeout": {"request": 180000}` + `window.navigationBarTitleText: "蓬蒿者"`；`app.wxss` 定义全局 CSS 变量（深浅色双主题，参照 `web/index.html` 的 `--border/--muted/--bg` 色板）。

- [ ] **Step 1: 写失败测试** `tests/miniprogram/test_fmt.js`

```js
const test = require('node:test');
const assert = require('node:assert');
const { fmtNum, fmtFixed } = require('../../miniprogram/utils/fmt.js');

test('fmtNum 万亿缩写', () => {
  assert.equal(fmtNum(163000000), '1.63亿');
  assert.equal(fmtNum(8227400), '822.74万');
  assert.equal(fmtNum(-8227400), '-822.74万');
  assert.equal(fmtNum(123.456), '123.46');
});
test('fmtNum 缺失诚实 —', () => {
  assert.equal(fmtNum(null), '—');
  assert.equal(fmtNum(undefined), '—');
  assert.equal(fmtNum(NaN), '—');
});
test('fmtFixed', () => {
  assert.equal(fmtFixed(0.7123), '0.71');
  assert.equal(fmtFixed(null), '—');
});
```

- [ ] **Step 2: 跑测试确认失败**：`node --test tests/miniprogram/test_fmt.js` → FAIL（Cannot find module fmt.js）
- [ ] **Step 3: 实现脚手架 + `fmt.js`**：上面 5 个文件+3 页占位；`fmt.js` 是纯 CommonJS。
- [ ] **Step 4: 跑测试通过 + 语法校验**：`node --test tests/miniprogram/` PASS；`node -e "for (const f of ['miniprogram/app.json','miniprogram/project.config.json']) JSON.parse(require('fs').readFileSync(f,'utf8'))"` 无输出。
- [ ] **Step 5: 提交** `feat(miniprogram): 脚手架+数字格式化工具(node测试基建)`（git add miniprogram/ tests/miniprogram/）

### Task 2: api.js 请求封装（超时/重试/错误文案）

**Files:**
- Create: `miniprogram/utils/api.js`
- Test: `tests/miniprogram/test_api.js`

**Interfaces:**
- Consumes: `config.BASE_URL`（Task 1）。
- Produces: `setTransport(fn)`（注入传输函数供测试，生产默认用全局 `wx.request`）；`request(opts): Promise<响应对象>`，`opts = {path: string, timeout?: number=90000, retry?: boolean=false, method?: string='GET'}`。resolve 整个 `_wrap` 后的 JSON（含 `data/update_time/disclaimer`）；网络层失败 reject `Error('后端不可达（本机服务开着吗？）')`。`retry=true` 时同一请求失败自动重发**一次**（对齐网页 `_qsFetch` 行为）。

- [ ] **Step 1: 写失败测试** `tests/miniprogram/test_api.js`——断言三件事：① 传输抛错 → reject 文案为"后端不可达（本机服务开着吗？）"；② `retry:true` + 第一次失败第二次成功 → resolve 第二次结果（fake transport 计数）；③ `retry:true` + 两次都失败 → reject 同文案（重试恰一次，计数=2）。另断言传给 transport 的参数含 `{url: BASE_URL+path, timeout}`。
- [ ] **Step 2: 跑测试确认失败**：`node --test tests/miniprogram/test_api.js` → FAIL
- [ ] **Step 3: 实现 `api.js`**：transport 签名 `(options) => Promise<wx.request 成功结果>`；不捕获 HTTP 状态码 4xx/5xx 之外的东西（`statusCode>=400` 也按失败处理走重试/文案）。
- [ ] **Step 4: 跑测试通过**：`node --test tests/miniprogram/` 全 PASS
- [ ] **Step 5: 提交** `feat(miniprogram): wx.request 封装-超时/单次重试/后端不可达文案`

### Task 3: 主力动向聚合纯函数（单位隔离）

**Files:**
- Create: `miniprogram/utils/smart_agg.js`
- Test: `tests/miniprogram/test_smart_agg.js`

**Interfaces:**
- Consumes: 无（纯函数）。
- Produces:
  - `SHARES_CHANNELS = ['十大股东', '高管增减持', '限售解禁']`
  - `aggByCode(rows: object[]) -> Array<{code,name,market,byCh,chList}>`：按 code 聚合，`byCh[ch] = {amt, n, hasAmt, ints}`（语义照抄 `web/index.html` smRender 的 code 视图：金额通道才累计 amt/ints，SHARES 通道只计 n）。
  - `codeMetrics(entry) -> {intensity, abs, nch, avail, pos, ratio}`（强度=金额通道 net_intensity 最大值；abs=金额通道净额绝对值和；ratio=正向通道占比；缺强度为 null 不是 0）。
  - `sortCodeRows(arr, metric: 'intensity'|'abs'|'nch'|'ratio', desc=true)`（null 强度排有强度行之后）。
  - `aggByActor(rows) -> Array<{actor, amount, n}>`。
  - `filterRows(rows, {chip, q})`：chip=channel 过滤，q=code/name/actor 小写包含。
  - `capRows(rows, cap=50)`。
  - `channelCounts(rows) -> Array<[channel, n]>`（chips 用）。

- [ ] **Step 1: 写失败测试**——关键断言：① 构造混合行（龙虎榜 -100 元、十大股东 +5000 股、同 code 两行龙虎榜），`aggByCode` 后该 code 的 `byCh['龙虎榜'].amt=-100, n=2`，`byCh['十大股东'].amt=0` 且金额通道 metrics.abs=100、**SHARES 股数 5000 不出现在任何金额字段**；② `codeMetrics.intensity` 仅有 SHARES 通道记录时为 null（排有强度行之后）；③ `filterRows` chip='龙虎榜' 只剩龙虎榜行、q='600519' 命中 code/name/actor 任一；④ `capRows` 截 50；⑤ `aggByActor` 累计与次数正确。
- [ ] **Step 2: 跑测试确认失败**：`node --test tests/miniprogram/test_smart_agg.js` → FAIL
- [ ] **Step 3: 实现 `smart_agg.js`**（照 web 版语义逐条移植，本模块是 Review Focus #2 的唯一防线）
- [ ] **Step 4: 跑测试通过**：`node --test tests/miniprogram/` 全 PASS
- [ ] **Step 5: 提交** `feat(miniprogram): 主力动向聚合纯函数-股数金额单位隔离+强度排序`

### Task 4: 跨清单摘要条组件

**Files:**
- Create: `miniprogram/utils/cross.js`
- Create: `miniprogram/components/cross-bar/`（cross-bar.js/.wxml/.wxss/.json）
- Test: `tests/miniprogram/test_cross.js`

**Interfaces:**
- Consumes: 无。
- Produces: `crossChips(items: object[], n=20) -> Array<{code, name, text, extra}>`（extra=穿透字段串：quality_pct 两位小数/mf_phase/streak_inflow|streak_outflow"流入N日"/sector_heat——照 `web/index.html` `_crossBarHtml` 的 chips 组装语义，缺失字段跳过不伪造）；组件 `cross-bar` properties `{items: Array}`，横向 scroll-view 渲染 chips。

- [ ] **Step 1: 写失败测试**——断言：完整字段行 → text 含代码+名称、extra 含 "quality 0.72"/"流入3日"/"景气0.65"；全缺失行 → extra 为空数组；null `quality_pct` 不出现 "quality"。
- [ ] **Step 2: 跑测试确认失败** → FAIL
- [ ] **Step 3: 实现 `cross.js` + 组件**（组件 json 标 `"component": true`；wxml 用 scroll-view 横向）。
- [ ] **Step 4: 跑测试通过 + JSON 校验**（同 Task 1 Step 4 校验法覆盖组件 json）。
- [ ] **Step 5: 提交** `feat(miniprogram): 跨清单摘要条组件-头部前20只读穿透字段`

### Task 5: 主力动向页面

**Files:**
- Modify: `miniprogram/pages/smart/`（四个文件从占位变实页）
- Consumes: `request`（Task 2）、`aggByCode/codeMetrics/sortCodeRows/aggByActor/filterRows/capRows/channelCounts/SHARES_CHANNELS`（Task 3）、`cross-bar`（Task 4）、`fmtNum`（Task 1）。

**Interfaces:**
- Produces: 可用页面（无对后续任务的接口承诺）。

- [ ] **Step 1: 写页面 JS**——数据流：`onShow`（距上次加载>5min 才重拉）并行 `request({path:'/api/smart-money/channels'})`（通道灯）与 `request({path:'/api/smart-money/today?days=7'})`（默认参数，落 tracker 默认组）；today 失败→页面顶部错误条文案（Task 2 的 reject message）+ 空态。视图切换三态（`viewMode: 'code'|'actor'|'flow'`，默认 code）；chips=channelCounts；搜索框 input 绑定 filterRows；**setData 只喂 `capRows(...)` 结果（首屏 50）+"展开全部"按钮置 full=true 后喂全量**（Review Focus #5）。code 视图行渲染字段：code/name/market + `metrics.intensity`（缺→"—"）+nch+ratio+六通道 `byCh` 金额（SHARES 通道标"股"）+`quality_pct` 徽章。
- [ ] **Step 2: 写 WXML/WXSS**——顶部：通道灯行（●绿/◐黄/灰，`rows` 数，点击 wx.showModal 显 err 详情）→ cross-bar（today 响应前 20）→ 搜索框+chips 横向滚动+视图切换段控件 → 列表卡片。页脚 disclaimer 固定小字。`enablePullDownRefresh: true`（页面 json）+ `onPullDownRefresh` 重拉。
- [ ] **Step 3: 语法校验**：`node --test tests/miniprogram/` 仍全 PASS（页面 JS 不进测试但 require 链不能坏——utils 内不得反向 require 页面）；`node -e "JSON.parse(...)"` 校验页面/组件 json。
- [ ] **Step 4: 提交** `feat(miniprogram): 主力动向页-三视图/通道灯/首屏50行截断`

### Task 6: 优质筛选页面

**Files:**
- Create: `miniprogram/utils/quality_view.js`
- Modify: `miniprogram/pages/quality/`
- Test: `tests/miniprogram/test_quality_view.js`
- Consumes: `request`（quality 调用必须 `timeout=170000, retry=true`）、cross-bar、fmtNum。

**Interfaces:**
- Produces: `QUALITY_DEFAULT_PATH = '/api/quality?universe=stock&days=20&min_dims=2&limit=10&combo_method=greedy&resonance_mode=greedy&dim_thresh=0.7&max_per_board=3&max_corr=0.85'`；`buildQualityPath(preset: 'strict'|'loose') -> string`（loose = 默认串上追加 `&strict_quality=false&dim_thresh=0.6`，替换原 dim_thresh）；`REASON_ZH = {hard_gate:'硬门槛', confidence:'低可信度', risk_flags:'风险标记', min_dims:'口径不足', no_resonance:'无共振分'}`。

- [ ] **Step 1: 写失败测试**——断言：① `buildQualityPath('strict') === QUALITY_DEFAULT_PATH`（含 `limit=10`、`max_corr=0.85`——Review Focus #3 的参数防线，写死防回归 50/0.6）；② `buildQualityPath('loose')` 含 `strict_quality=false` 与 `dim_thresh=0.6` 且不含 `dim_thresh=0.7`、其余参数与默认一致。
- [ ] **Step 2: 跑测试确认失败** → FAIL
- [ ] **Step 3: 实现 `quality_view.js` + 页面**——页面：预设切换（严格默认/宽松，切宽松时 wx.showModal 预告"宽松档偏离缓存键，每次需 1 分钟级冷算"再发请求）；状态条（dim_status 五灯、主清单 N、耗时 s、<2s 标"缓存命中"）；`refine_status` 非 ok 开头 → 降级提示条；摘要（requested_limit/eligible_count/excluded_summary 四项）；主清单卡片（code/name/resonance/hits/dim_scores 1-5 横排缺失—/mf_phase 徽章出货红吸筹绿/streak_inflow·cum_net 小字/data_confidence/risk_flags 有则显/reasons 折叠）；by_dim 折叠区（排除原因经 REASON_ZH）；`app.json` 已含 `networkTimeout.request:180000`（Task 1 建的，此处读值断言：`node -e` 校验 ≥180000）。页面 JS 不得出现写死的 50/0.6。
- [ ] **Step 4: 跑测试通过 + json 校验**：`node --test tests/miniprogram/` 全 PASS
- [ ] **Step 5: 提交** `feat(miniprogram): 优质筛选页-严格/宽松预设+降级提示+by_dim诊断`

### Task 7: 次日强势页面

**Files:**
- Create: `miniprogram/utils/nextday_view.js`
- Modify: `miniprogram/pages/nextday/`
- Test: `tests/miniprogram/test_nextday_view.js`
- Consumes: `request`（默认 90s 即可——30s 进程缓存+首次约 45s）、cross-bar、fmtNum。

**Interfaces:**
- Produces: `STEP_LABELS = ['步1量价','步2规模','步3趋势','步4软分','步5板块']`；`stepState(x, i) -> 'pass'|'fail'|'missing'`（i=0..4；照 web 版：`x.step_status['step'+(i+1)]` 缺省时 failed_steps 含之→'fail'，否则 'missing'）；`firstFail(x) -> {idx, label, reason} | null`；`ndSummary(items) -> {pass, missing, coverPct}`（pass=五步全过数；coverPct=平均 score_coverage 百分比取整）。

- [ ] **Step 1: 写失败测试**——断言：① `stepState` 三态推导（step_status 有 pass / 无但 failed_steps 含 step2 → 'fail' / 都无 → 'missing'）；② `firstFail` 取第一个非 pass 步骤，reason 缺省→'未达门槛'，全过→null；③ `ndSummary` 统计正确且空数组不除零（coverPct=0）；④ `factor_scores` 缺键 → factorCell 输出 '—'（若 cell 组装进此模块则测之，否则在页面遵循同一约定，测试断言模块导出的 `factorCell(x, key)` 行为）。
- [ ] **Step 2: 跑测试确认失败** → FAIL
- [ ] **Step 3: 实现 `nextday_view.js` + 页面**——页面：mode 段控件 strict/score（URL 参数 `selection_mode`）；filter 三档（严格/宽松/全部——参数照抄 `web/index.html` ndLoad 的 `flt==='wide'`/`'all'` 映射：`min_change_pct/min_turnover/max_price/min_mv/max_mv/max_pe/exclude_st`）；摘要行（候选/五步全过/含缺数据/平均覆盖率/market_median_chg）；卡片（rank/code/name、score 主分+base_score 小字、雷达三列 `radar_resonance_in/boost>1 才显 ×N.NN 绿/risk_flag 红`、五步徽章 ✓×?+首失败步骤小字（firstFail）、五因子分横排缺失橙—、覆盖率%+source、board、步骤原因折叠）；页脚 disclaimer；下拉刷新+onShow 5min 节流。
- [ ] **Step 4: 跑测试通过**：`node --test tests/miniprogram/` 全 PASS
- [ ] **Step 5: 提交** `feat(miniprogram): 次日强势页-五步徽章/雷达三列/首失败步骤`

### Task 8: 收尾——文档 + 用户联调清单 + 全量回归

**Files:**
- Modify: `CLAUDE.md`（架构节 `miniprogram/` 条目 + 常用命令加 `node --test tests/miniprogram/` + 改动检查清单加"新增/修改小程序页面"项）
- Create: `docs/superpowers/plans/` 本文档已存在；联调清单写进 CLAUDE.md 或随提交正文。

- [ ] **Step 1: 全量后端回归**：`python -m pytest tests/ -q` → 625 passed（证明零改动）
- [ ] **Step 2: 更新 CLAUDE.md**：新增条目要点——`miniprogram/` 是"蓬蒿者"个人主体自用小程序（无 web-view/不上架/调试模式直连）；BASE_URL 单点 `config.js`；纯函数测试 `node --test tests/miniprogram/`（CommonJS 非 ESM 的原因）；quality 默认参数 10/0.85 对齐 warmup/tracker 的约束同样约束小程序端。
- [ ] **Step 3: 用户联调清单**（写进提交正文或 CLAUDE.md）：
  1. 微信开发者工具导入 `miniprogram/`，appid 填"蓬蒿者"的（`project.config.json` 占位 touristappid）；
  2. 详情→本地设置→勾"不校验合法域名…"；
  3. 后端 `uvicorn api.server:app --host 0.0.0.0 --port 8000` 或现有 Docker；
  4. `config.js` BASE_URL 改本机局域网 IP；先 /api/refresh 保证 spot 有数据；
  5. 真机预览：手机同 Wi-Fi 直连；出门改内网穿透地址（HTTP 即可，调试模式不校验证书）。
- [ ] **Step 4: 提交** `docs(CLAUDE.md): 小程序目录/命令/联调清单同步`（+`docs(plan):` 若计划文档尚未提交，一并补 `docs(spec):`——spec 在设计阶段已建）。

---

## 自审记录

- spec 覆盖：三页面（T5/6/7）、跨清单摘要条（T4）、网络层（T2）、部署联调（T8）、合规页脚（T5/6/7 各步）、放宽档与默认参数（T6 写死测试）、quality 超时重试（T2+T6）。spec"非目标"未进任务——正确。
- 类型一致：`aggByCode/codeMetrics/sortCodeRows` 名字在 T3 产、T5 消费一致；`buildQualityPath` T6 产、页面同任务消费。
- Review Focus 五条均有归属测试（见各条标注）。
- 比例：计划长度与 spec 相当，代码块仅测试断言与参数串，无实现转写。
