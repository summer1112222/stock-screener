# 蓬蒿者小程序（stock-screener 随身客户端）设计

日期：2026-11-18
状态：待用户批准

## 意图与动因

用户已注册个人主体微信小程序"蓬蒿者"，想把现有 stock-screener（FastAPI 后端 + 网页单页前端）以**自用**形态搬到手机上：在外面掏出手机就能看当天的三张清单。首期范围：**主力动向、优质筛选、次日强势**三个模块；其余（持仓/自选/个股分析/回测/ETF/信号）继续用电脑网页版，后续按需追加。

## 目标

- 三个原生小程序页面，数据来自现有 FastAPI 路由，**后端零改动**。
- 每页顶部复刻网页版"跨清单摘要条"（头部前 20，只读穿透字段不触网）。
- 手机友好的卡片式列表（网页版是 10+ 列宽表，手机上放不下）。
- 下拉刷新 + 进入页面自动加载；失败/降级诚实标注。

## 非目标（首期明确不做）

- 不做持仓/自选/个股分析/历史回测/ETF/信号扫描/研报页面。
- 不做用户登录、不做分享、不做推送。
- 不公开上架（个人主体金融类目过不了审）；只走**开发版/体验版**自用。
- 不改 `api/server.py` 任何路由与逻辑（`_wrap` 的 disclaimer 管道照常生效）。

## 反目标

- 引入 npm/构建链/跨端框架（uni-app/Taro）——三个页面的自用工具不值得，且违背本仓库"无构建步骤"的风格。
- 把网页版宽表原样塞进小程序（横向滚动表格在手机上不可用）。

## 约束

- **个人主体小程序无 `web-view` 组件权限** → 必须原生 WXML/WXSS/JS 重写前端。
- **域名校验**：开发版/体验版开启"调试模式"可跳过"合法域名/TLS/HTTPS 证书"校验，可直连 `http://<本机或穿透IP>:8000`。正式域名备案方案留给以后。
- **请求超时**：`/api/quality` 冷缓存实测 ~67s（前端 75s 超时+重试）；`wx.request` 需把 `app.json` 的 `networkTimeout.request` 调到 ≥180000（默认 60000 会提前断），quality 请求照网页版逻辑做一次自动重试。
- **合规**：后端响应自带 `cand_disclaimer`；小程序每页页脚固定显示对应免责文案（"机械排序观察清单，非荐股非买卖信号，盈亏自负"），措辞不新造。

## 方案选择

- **方案 A（选定）：原生小程序**——`miniprogram/` 目录纯 WXML/WXSS/JS，零依赖零构建，微信开发者工具直接导入。三页面规模下手写最快、最好维护。
- 方案 B（弃）：uni-app/Taro 跨端重写。仅当未来要"一套代码同时出网页+小程序"才值得；自用三页面是纯负担。

## 结构

```
miniprogram/
  app.js / app.json / app.wxss      # tabBar 三页、networkTimeout、全局样式(深浅色变量)
  config.js                          # BASE_URL 单点配置(唯一要手改的文件)
  utils/api.js                       # wx.request 封装:超时/重试/错误文案/disclaimer 透传
  utils/fmt.js                       # fmtNum 等数字格式化(从 web/index.html 移植)
  components/cross-bar/              # 跨清单摘要条(chips 横向滚动,只读穿透字段)
  pages/smart/                       # 主力动向
  pages/quality/                     # 优质筛选
  pages/nextday/                     # 次日强势
  project.config.json                # 用户自己的 appid(蓬蒿者)
```

后端：`uvicorn api.server:app --host 0.0.0.0 --port 8000`（或现有 Docker），手机开发者工具/真机调试模式直连。BASE_URL 写 `http://<局域网IP>:8000`（在家）或内网穿透地址（在外）。

## 页面设计（字段对照网页版）

### pages/smart/ 主力动向
- 数据：`GET /api/smart-money/today?days=7`（默认参数）+ `GET /api/smart-money/channels`（通道灯）。
- 顶部：通道灯行（●绿 ok/◐黄 stale/灰失败，显示 `:行数`，点击弹出 err 详情）。
- 视图切换：**按个股聚合**（默认，同网页 code 视图）/按席位/明细流。通道 chips 过滤 + 顶部搜索框（code/name/actor）。
- 个股卡片：代码/名称/市场 · `net_intensity` 强度 · 通道数 · 正向占比 · 六通道分列净额（股数通道标"股"不与金额混比，同网页 `_SHARES_CH` 单位隔离）· `quality_pct` 徽章（有则显示）。
- 首屏 50 行 + "展开全部"（同网页 2d7bbed 截断策略）。
- 不搬：单通道刷新按钮（⟳ 走 `/api/smart-money/refresh`，手机上误触代价高，放"更多"菜单里再做——首期不做全量 refresh，采集仍在电脑上做）。

### pages/quality/ 优质筛选
- 数据：`GET /api/quality?universe=stock&days=20&min_dims=2&limit=10&combo_method=greedy&resonance_mode=greedy&dim_thresh=0.7&max_per_board=3&max_corr=0.85`（**真实默认**，与 `server._warm_quality_cache`、`tracker.DEFAULT_PARAMS["quality"]`、网页版下拉默认三处一致——命中预热缓存概率最大，且手机请求落 `list_track` 默认组与电脑共用排序诊断统计）。
- 参数区：折叠面板（默认收起），两档预设：
  - **严格（默认）**：上述默认参数，照原文请求。
  - **宽松**：`strict_quality=false` + `dim_thresh=0.6`（其余不动）——低可信度/硬门槛被排除的标的放回主清单（行上带红色排除原因标记，字段 `eligibility`/`exclusion_reasons`），共振命中口径放宽。代价：偏离缓存键，每次必自己冷算（~1 分钟级），加载文案需预告。
- 状态条：口径状态灯（dim_status 五个）· 主清单 N 只 · 耗时（<2s 标"缓存命中"）· `refine_status` 非则以降级提示条（同网页 `_renderQsDegrade`）。
- 摘要：请求/严格合格/硬门槛排除/低可信度排除/口径不足（`excluded_summary`）。
- 主清单卡片：代码/名称 · `resonance` 共振分 · `hits` 命中数 · 五口径分位 `dim_scores`（1-5 横排小字）· `mf_phase` 徽章+`streak_inflow/cum_net`（主力行为行，出货红/吸筹绿）· `data_confidence`/`risk_flags`（有则显示）· `reasons` 展开查看。
- `by_dim` 各口径诊断列表：折叠区（默认收起），行含排除原因中文映射（同网页 `reasonZh`）。
- 加载文案照网页版："首次约 1-2 分钟，超时自动重试一次"。

### pages/nextday/ 次日强势
- 数据：`GET /api/nextday-strong`（默认 strict 模式；宽松 preset wide/all 同网页 `ndFilter` 三档参数）。
- 摘要行：候选 N · 五步全过 N · 含缺数据 N · 平均覆盖率% · `market_median_chg` 大盘中位。
- 卡片：排名/代码/名称 · `score` 综合分（下小字 `base_score`）· 雷达三列（`radar_resonance_in` 共振 0-3 / `radar_boost` ×N.NN / `risk_flag` 红）· 五步徽章（✓绿 ×红 ?橙，首个失败步骤差值小字，同 2d7bbed）· 五因子分（`factor_scores` 横排，缺失橙色 —）· 覆盖率%+`data_quality.source` · `board` 板块 · 步骤原因折叠。
- mode 切换：strict/score 两个 tab 式按钮；filter 三档（严格/宽松/全部）。

### 公共
- 每页：下拉刷新（`enablePullDownRefresh`）+ `onShow` 若距上次加载 >5min 自动重拉。
- 页脚 disclaimer 固定小字。
- 数字格式化 `fmtNum`（万/亿缩写）移植自网页版。
- 日期显示：响应的 `update_time`/`date` 顶部条。

## 网络层（utils/api.js）

- `request(path, {timeout, retry})` 封装：默认 90s；quality 180s+失败自动重试一次（对齐网页 `_qsFetch` 行为）。
- 错误统一文案："后端不可达（本机服务开着吗？）"——自用场景最常见的失败是后端没开。
- 响应结构透传 `_wrap` 的字段（`data/update_time/disclaimer`）。

## 部署与联调（自用）

1. 电脑跑后端（现有 Docker 或 uvicorn，`--host 0.0.0.0`）。
2. 微信开发者工具导入 `miniprogram/`，填"蓬蒿者" appid，勾选"不校验合法域名"。
3. 真机预览：手机连同一 Wi-Fi 时 BASE_URL 用局域网 IP；在外用内网穿透（cpolar/花生壳免费版即可，HTTP 即可——调试模式不校验证书）。**前提是这台电脑开着**；常开机器/云服务器问题待用户确认（见开放问题）。

## 测试策略

- 小程序端无自动化测试框架（不引入 miniprogram-simulate）：以微信开发者工具手动验证 + 真机预览为主。
- 后端零改动，现有 625 项 pytest 不受影响；联调后跑 `python -m pytest tests/ -q` 确认无回归。

## 开放问题（已裁决/挂起）

1. **已裁决（2026-11-18）**：quality 主清单 `limit=10/max_corr=0.85` 按真实默认（秒回+追踪对齐），不用 50 只；宽松档 = `strict_quality=false`+`dim_thresh=0.6`。
2. 挂起（不阻塞构建，只影响 BASE_URL 指哪）：后端常开部署形态——这台 Windows 电脑常开吗？还是后续弄台常开机器/云服务器+内网穿透？
3. 挂起：appid 用户导入开发者工具时自填，`project.config.json` 留占位。

## 留给构建者的决定

- 页面具体配色/字号（遵循 app.wxss 全局变量，默认深浅色双主题，参照网页版 CSS 变量）。
- 卡片内字段排布细节与折叠交互。
- `utils/` 内部实现细节（Promise 封装风格等）。
