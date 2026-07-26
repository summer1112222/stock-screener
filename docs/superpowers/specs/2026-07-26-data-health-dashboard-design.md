# 数据健康仪表盘 · 设计文档

**日期**: 2026-07-26
**状态**: 设计已确认，待转实施计划
**范围**: `api/server.py`（+1 聚合路由 `/api/health`）+ `web/index.html`（顶部 banner + `#healthDrawer` 抽屉 + 60s 轮询）+ `tests/test_health.py`（新）+ `README.md`（重写门面）。**不改 `data/` 层**（只读聚合）。

---

## 1. 背景与产品判断

用户最痛是"这数据是几点的？可靠吗？"——北向刚补备援、stale 刚落地，但健康状态散落在主力动向 tab 的通道灯里，无全局视图。本设计收口所有数据域新鲜度到一个**跨 tab 始终可见**的顶部 banner + 展开抽屉，作为用户信任根基。顺带重写 README（CLAUDE.md 自陈"仍停留阶段1"）补产品门面。

**非目标**：不做采集调度自动化（仍手动 `/api/refresh`）；不做历史成功率趋势图（P2）；不新增表/采集源。

---

## 2. 合规边界（不变）

- 只读聚合，措辞"数据健康/新鲜度/观察清单"，禁"推荐/买入信号"。
- banner/抽屉不输出买卖点；默认 disclaimer 经 `_wrap` 附。
- stale 域显式标"非实时/回退日期"，与 §4.2 stale 降级一致。

---

## 3. 架构

- **不新增表/列**。`/api/health` 聚合既有 `db.last_update_time()`/`smart_money.channel_status()`/各表 `db.query_rows`/meta。
- **只读**：不触发采集，不写表。手动刷新由 banner `[手动刷新]` 按钮显式调 `/api/refresh` + `/api/smart-money/refresh`。
- 前端 banner + 抽屉复用现有 `#pfDrawer`（持仓抽屉）模式，新增 `#healthDrawer`。

---

## 4. 后端 `/api/health`

### 4.1 路由

```python
@app.get("/api/health")
def health():
    from data import db, smart_money
    return _wrap(_collect_health(db, smart_money))
```

`_collect_health` 在 `api/server.py` 内（或独立小模块）聚合，单域查询 try/except 不崩。

### 4.2 返回结构

```python
{
  "domains": {
    "spot": {"stock": {"rows": N, "latest": "YYYY-MM-DD"}, "etf": {...}, "status": "green|yellow|red"},
    "history": {"codes": N, "date_range": ["min","max"], "rows": N, "status": "..."},
    "smart_money": {"channels": <channel_status()>, "latest_date": str, "rows": N, "status": "..."},
    "fundamentals": {"abstract": {"hit": N, "stale": N}, "full": {"hit": N}, "status": "..."},
    "research": {"reports": {"rows": N, "latest": str}, "comments_cached": N, "status": "..."},
    "st_list": {"rows": N, "status": "..."},
    "portfolio": {"positions": N, "status": "..."},
  },
  "update_time": <db.last_update_time()>,
  "last_refresh_time": <db.get_meta("last_refresh","")>,
  "overall": "green|yellow|red"
}
```

### 4.3 status 派生规则

- **green**：当日有数据且最新（spot/smart_money/research/st 的 `latest`==今日或最近交易日；history/fundamentals 看覆盖非空）。
- **yellow**：stale（复用 `channel_status` 的 `stale=true`）或非当日但有旧数据。
- **red**：空表 / 查询异常 / 通道 `ok=false` 且无旧数据。
- `overall` = 最差域（red>yellow>green）。

### 4.4 各域聚合要点

- **spot**：`db.query_rows("stock_spot", limit=0)`/`("etf_spot",...)` len；spot 无 date 列，latest 取 `db.last_update_time()`。
- **history**：`stock_daily`/`etf_daily`/`board_daily` 各 `SELECT COUNT(*), MIN(date), MAX(date), COUNT(DISTINCT code)`。
- **smart_money**：`smart_money.channel_status()`（已含 stale/last_ok_date）+ `smart_money_action` MAX(date)/COUNT。
- **fundamentals**：`financial_abstract_cache` + `fundamentals_cache` 各 COUNT；stale 数 = 缓存日期超 7 天的行数。
- **research/st_list/portfolio**：各表 COUNT + MAX(date)。
- 单域异常 → 该域 `status="red", err=str(e)`，不阻塞其他域。

---

## 5. 前端 `web/index.html`

### 5.1 banner（顶部固定，所有 tab 可见）

```html
<div id="healthBar" class="health-bar">
  <span id="hbOverall">●</span><span id="hbTime">数据 …</span>
  <span id="hbDots"></span>
  <button id="hbDetail" class="btn ghost" type="button">详情▾</button>
</div>
```

- `hbOverall` 整体灯（绿/黄/红）+ `hbTime` = `update_time`。
- `hbDots` 各域小灯串：`●spot ●历史 ◐北向 ●财报 …`（复用三态色）。
- 60s 轮询 `setInterval(healthLoad, 60000)` + tab 切换时调 `healthLoad()`。

### 5.2 `#healthDrawer` 抽屉（右侧，复用 #pfDrawer 模式）

```html
<aside id="healthDrawer" class="drawer" hidden>
  <div class="card-t">数据健康 <button onclick="toggleHealth()">×</button></div>
  <div id="healthBody"></div>
  <div class="disc">数据健康为新鲜度观察，非投资建议。</div>
</aside>
```

- `healthBody` 渲染各域卡片：`名称 | 行数 | 最近日期 | 状态灯 | 失败/stale 提示`。
- 底部：上次全量采集时间 + `[手动刷新]`（调 `/api/refresh` + `/api/smart-money/refresh`，完成后 `healthLoad()`）。
- `toggleHealth()` 切换显隐，仿 `togglePortfolio()`。

### 5.3 JS

- `healthLoad()`：`fetch /api/health` → 更新 banner + 抽屉（若已展开）。
- 错误：banner 显灰"数据健康检查失败"，不阻塞 tab 操作。

---

## 6. README 重写

`README.md` 从"阶段1"更新为当前全貌（对齐 CLAUDE.md 路由速查/架构，但面向**用户**）：
- 产品定位（一段）+ 合规边界（一段）。
- 功能清单（4 tab + 持仓 + 数据健康）。
- 快速开始（Docker 一行 + 前端入口 + `/docs` Swagger）。
- 路由速查表（精简，按域分组）。
- 架构图（四层 + 领域模块，文字版）。
- 数据源说明（AKShare + 备援链 + 已知限制如北向盘后口径）。
- 不复制 CLAUDE.md 的"关键设计决策"（那是给 Claude 的）；README 面向人。

---

## 7. 错误处理

- `/api/health` 每域 try/except，单域失败标 red 不崩。
- banner 轮询失败显灰，不阻塞。
- 手动刷新按钮 disabled 期间显"采集中…"。
- NaN→None：聚合 count/date 均标量，无 DataFrame 序列化风险；`_wrap` 兜底。

---

## 8. 测试 `tests/test_health.py`

合成数据 mock `db.query_rows`/`db.last_update_time`/`smart_money.channel_status`，不触网：

| 测试 | 覆盖 |
|---|---|
| `test_health_returns_all_domains` | 7 域卡片字段齐 |
| `test_health_status_derivation` | green(当日)/yellow(stale)/red(空) 派生正确 |
| `test_health_overall_worst_domain` | overall = 最差域（red>yellow>green） |
| `test_health_partial_failure` | 某表查询抛异常不崩、该域 red+err、其他域正常 |
| `test_health_smart_money_reuses_channel_status` | smart_money 域复用 channel_status 的 stale/last_ok_date |

---

## 9. 改动检查清单（对齐 CLAUDE.md）

- 不新增表/列 → `models.SCHEMA_SQL`/`TABLE_FIELDS` 无需改。
- 新增路由 `/api/health` → 用 `_wrap()`，默认 disclaimer（数据聚合，非荐股）。
- 前端 → 新增 banner + `#healthDrawer` + `healthLoad` JS；`LABEL` 字典无需改。
- 单测放 `tests/test_health.py`，合成数据 mock，不依赖网络。
- README 重写对齐 CLAUDE.md 路由速查但面向用户。

## 10. 分期

- **P1**：`/api/health` 聚合路由 + 5 测试。
- **P2**：前端 banner + `#healthDrawer` + 60s 轮询 + 手动刷新。
- **P3**：README 重写。
