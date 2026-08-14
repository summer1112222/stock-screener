# 批量自选监控（watchlist live monitor）设计

> 合规前置：本功能是"自选股观察清单"的盯盘增强，机械行情汇总 + 用户自设价位机械标记，**非买卖信号、非荐股、不自动下单**。所有响应挂 `cand_disclaimer`。

## 背景与缺口

现有 `data/watchlist.py` + `/api/watchlist` + 前端 `#wlDrawer` 抽屉已是"未买入观察清单"基础设施，但盯盘能力不足：

1. **价格非实时**：`list_items()` 取 `stock_spot/etf_spot` 的 `latest_price`——spot 快照仅 `/api/refresh` 后更新，盘中陈旧。用户盯盘要的是盘中实时价。
2. **无批量信号**：`scan_signals(universe, codes)` 已支持批量 codes，但 watchlist 未接通——用户须手动逐个去信号 tab 扫。
3. **watchlist 无提醒字段**：到价提醒（`alert_hi/alert_lo`）只在已买入的 portfolio 上，未买入的观察清单无法设目标买点价位。
4. **前端抽屉只展示快照价**：无轮询、无涨跌幅着色、无越线高亮、无信号徽章。

## 目标

- watchlist 每只盘中实时价 + 涨跌幅（tdx 批量直取，盘中秒级）。
- 批量信号扫描（watchlist 全体，个股/ETF 分拆）。
- watchlist 每只可设 `alert_hi/alert_lo`，越线机械标记 + 前端高亮。
- 盘中 30s 自动轮询行情；盘后自动降频省请求。

## 非目标

- 不做 SSE/WebSocket 推送（前端轮询够用，省连接管理）。
- 不做自动下单/弹窗告警（合规：不输出买卖点）。
- 不改 portfolio 现有提醒（已买入持仓提醒不变）。

## 架构：路由组织（方案 A · 关注点分离）

两路由 + 一 PATCH，频率各得其所：

| 路由 | 频率 | 内容 | 数据源 |
|---|---|---|---|
| `GET /api/watchlist/live` | 盘中 30s | 批量实时行情 + alert 越线标记 + in_session | tdx get_quote 批量（spot 降级） |
| `GET /api/watchlist/signals` | 开抽屉 + 每 5min | 批量信号扫描 | scan_signals（stock/etf 分拆） |
| `PATCH /api/watchlist/{wid}` | 失焦触发 | 设 alert_hi/alert_lo | watchlist 表 |

**为何分离**：行情要快（tdx 批量秒级）、信号扫描慢（scan_signals 遍历历史表，个股多时秒级）。单聚合路由会让慢信号扫描拖累 30s 行情轮询。

## 详细设计

### ① DB schema（data/models.py + db._BOARD_MIGRATIONS）

watchlist 表加两列：

```sql
CREATE TABLE IF NOT EXISTS watchlist(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  code TEXT NOT NULL,
  name TEXT DEFAULT '',
  note TEXT DEFAULT '',
  added_ts TEXT,
  alert_hi REAL,        -- 新增：用户自设上限提醒价
  alert_lo REAL         -- 新增：用户自设下限提醒价
);
```

旧库已建 watchlist 表（`CREATE TABLE IF NOT EXISTS` 不补列）→ 在 `db._BOARD_MIGRATIONS` 加两条 `ALTER TABLE watchlist ADD COLUMN alert_hi REAL` / `alert_lo REAL`（`try/except` 忽略 duplicate column，与现有迁移风格一致）。watchlist 不进 `TABLE_FIELDS`/`upsert_rows`（独立 INSERT，见 CLAUDE.md watchlist 约束）。

### ② data/watchlist.py

新增/改动：

- `list_codes() -> list[str]`：返回去重 code 列表（供 live/signals 路由，避免重复读全表）。
- `set_alert(wid: int, alert_hi: float|None, alert_lo: float|None) -> bool`：`UPDATE watchlist SET alert_hi=?, alert_lo=? WHERE id=?`，用 COALESCE 或显式 None 覆盖（None=清除提醒）。
- `list_items()`：在现有返回基础上每行加 `alert_hi`/`alert_lo`（SELECT 列加两列）。
- `_is_etf(code: str) -> bool`：代码类型推断——`51/52/15/16/50/56/58/11/12` 开头为 ETF/基金（沪深 ETF + LOF），其余为个股。供 signals 路由分拆 universe。

### ③ 路由（api/server.py 新增 3 个）

**`GET /api/watchlist/live`**

```
读 watchlist.list_codes() → pytdx_client.get_quote(codes) 批量(≤80/批自动分页)
→ 对每只：
  - tdx 有：price=q.price, prev_close=q.last_close, change=price-prev_close,
            change_pct=change/prev_close*100（prev_close=0 时 None 防 div0）
  - tdx 无(codes 超出/全空)：降级 stock_spot/etf_spot.latest_price, quote_source="spot陈旧",
            change/change_pct 用 spot 的 change_pct 字段(若有)否则 None
  - 皆空：quote_error="无行情数据"
  - alert_hit: 
      "hi"  if alert_hi is not None and price>=alert_hi
      "lo"  if alert_lo is not None and price<=alert_lo
      else None
  - name: watchlist.name 兜底(tdx 无 name 列)，code→stock_spot 按 code 补 name
返回 {rows:[...], in_session: <bool>, update_time, disclaimer}
```

`in_session` 复用 `backtest.quality._is_in_session()`（已注入 now 可测）；前端据之决定 30s vs 5min。挂 `cand_disclaimer`："自选股实时行情机械汇总+用户自设价位越线机械标记，观察清单非荐股非买卖信号，盈亏自负"。

**`GET /api/watchlist/signals`**

```
codes = watchlist.list_codes()
stock_codes = [c for c in codes if not _is_etf(c)]
etf_codes   = [c for c in codes if _is_etf(c)]
res = {}
if stock_codes: res_stock = bt_sig.scan_signals("stock", stock_codes); 合并
if etf_codes:   res_etf   = bt_sig.scan_signals("etf",   etf_codes);   合并
每行带 universe 标记(stock/etf)
返回 {rows:[...], n_scanned, error, disclaimer}
```

挂 `cand_disclaimer`："批量机械信号扫描，非AI推荐，不构成投资建议，盈亏自负"（复用 signals 既有措辞）。

**`PATCH /api/watchlist/{wid}`**

```
class WLAlertReq: alert_hi: float|None=None; alert_lo: float|None=None
ok = watchlist.set_alert(wid, req.alert_hi, req.alert_lo)
return _wrap({"updated": ok, "id": wid, "alert_hi": req.alert_hi, "alert_lo": req.alert_lo})
```

默认 `_wrap`（附 disclaimer）。

### ④ 前端 wlDrawer 重写（web/index.html）

复用第 1 期基础设施（`apiFetch`/`toast`/`persist`）。

- **抽屉打开** → 并行 `apiFetch('/api/watchlist/live')` + `apiFetch('/api/watchlist/signals')` → 渲染。
- **轮询**：盘中（live.in_session=true）`setInterval(30s)` 拉 live；in_session=false 降频 5min；抽屉关闭 `clearInterval`。轮询只拉 live（快），signals 不进 30s 轮询（开抽屉 + 每 5min 一次）。
- **每行布局**（单列，从左到右）：
  `代码 名称 | price change_pct(红涨绿跌) | ⚡N(信号徽章) | 上:[alert_hi] 下:[alert_lo]`
  - 信号徽章 `⚡N`：signals 结果按 code 归并计数，N=0 不显。
  - alert 输入框：失焦 → `apiFetch('/api/watchlist/{wid}', {method:PATCH, body JSON}, {btn})` → toast 成功。
  - `alert_hit` 非空：行边框高亮（hi=红、lo=橙），机械标记非买卖提示。
- **删除**：复用 `wlClose`（DELETE）。
- 持久化：轮询开关状态进 `_PERSIST_IDS`（可选）。

### ⑤ 合规措辞

- watchlist 全程"观察清单"，到价提醒=用户自设价位机械标记。
- `cand_disclaimer` 统一挂，不出现"推荐/买入/卖出"。
- alert_hit 高亮是"机械越线标记"，前端不弹"买入"建议。

## 测试（tests/test_watchlist_monitor.py）

全 mock，不触网。

1. **`set_alert` + `list_items` 返 alert 列**：add → set_alert(5,10) → list_items 含 alert_hi=5/alert_lo=10。
2. **`/api/watchlist/live` 行情+越线**：mock watchlist 2 只 + tdx get_quote 返批量 → 验证 change_pct 计算 + alert_hit（price≥hi→"hi"、price≤lo→"lo"、区间内→None）。
3. **`/api/watchlist/live` tdx 空降级**：mock tdx 返 [] + spot 有 latest_price → quote_source="spot陈旧"、price 来自 spot。
4. **`/api/watchlist/signals` 分拆**：mock watchlist 含个股 000001 + ETF 510300 → mock scan_signals 按 universe 返不同 → 验证两 universe 都被调、行带 universe 标记。
5. **DB migration**：旧库 watchlist 无 alert 列 → init_db 补列后 SELECT 不报 no such column。

## 风险与边界

- **tdx 批量上限**：get_quote ≤80/批自动分页（pytdx_client 已实现），watchlist 通常 <80 只无忧；超量自动分批。
- **ETF 代码推断**：`_is_etf` 按前缀，覆盖主流沪深 ETF/LOF；冷门品种误判→走 stock universe，scan_signals 对未知 code 返回空不崩。
- **盘后轮询**：in_session=false 降频 5min，盘后价=昨收不动，无大害且省请求。
- **alert_hit 非买卖**：高亮仅机械标记，措辞与 disclaimer 反复强调，不弹买卖建议。
- **watchlist 不进 upsert/TABLE_FIELDS**：保持独立 INSERT 风格（CLAUDE.md 约束），set_alert 用独立 UPDATE。

## 改动检查清单对齐

- 新增 SQLite 列 → `models.SCHEMA_SQL` + `db._BOARD_MIGRATIONS`（已纳入①）。
- 新增 API 路由 → `_wrap()` + 挂 `cand_disclaimer`（已纳入③）。
- 单测放 `tests/`，mock db/tdx/scan_signals（已纳入测试节）。
- 不新增采集源、不动 refresh 编排、不改既有 signals/portfolio 逻辑。
