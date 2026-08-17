# 每日强势股筛选(daily-strong) 设计

> 合规边界不变：工具提供数据+机械统计，用户自做买卖决策；所有响应挂 `cand_disclaimer`，不输出买卖点。个人自用放松合规（memory: personal-use-compliance-relax）已定调，措辞可用"每日强势/板块助攻"等操盘语言，但 disclaimer 管道保留不拆。

## 背景

用户 2026-08-17 提供一套5步"每日强势股"选股方法论（涨幅>5%选入 → 剔除市值/PE/ST 雷区 → 多头排列/放量突破形态 → 量价验证 → 板块助攻）。仓库已落地 `screener/nextday.py`（次日强势8条件，5因子打分式，`/api/nextday-strong`）。两者范式不同：

- **nextday**：次日强势**概率打分**（5因子×0.25/0.20/0.20/0.15/0.20 → score∈[0,100] 降序）
- **daily-strong**：当日强势**漏斗过滤**（step1/2/3/5 硬剔除 + step4 软打分排序）

经澄清确认：**新建独立模块**（与 nextday 并列，不合并），**混合编排**（硬剔除+软打分，忠于原文"删除/pass"语义）。

## 目标

- 落地 `screener/daily_strong.py` + `GET /api/daily-strong`，5步方法论机械编排。
- **零触网零新表**：全复用 `stock_spot`/`sector_fund_flow`/`stock_daily`，复用 `board_money_link` 的 code→board 反查套路。
- 分时项(step4"早盘30分钟带量")永久降级（无分时数据源，同 nextday 先例）。
- 同 nextday：粗筛 top K(≤200) + 30s 进程缓存 + codes 限定 + NaN→None 守卫 + 出货/不通过沉底。

## 数据可得性矩阵（探索已确认）

| 5步条件 | 字段/源 | 可得性 |
|---|---|---|
| 涨幅>5% | `stock_spot.change_pct` | ✅ |
| 换手>3% | `stock_spot.turnover_rate` | ✅ |
| 股价<50 | `stock_spot.latest_price` | ✅ |
| 流通市值 10–200亿 | `stock_spot.circulating_market_cap` | ✅（nextday 已用） |
| PE≤150 且非亏损 | `stock_spot.pe`（亏损=pe 为空或负，pe>150 剔除） | ✅ |
| 非 ST | `stock_spot.st_type` | ✅ |
| 量比>2.5 | `stock_spot.volume_ratio` | ✅ |
| 多头排列/放量突破60日线 | `stock_daily` 本地算 5/10/20/60 MA | ✅（依赖历史，无→降级） |
| 所属板块 | `stock_spot.board`（refresh 时 spot 已标行业板块） | ✅ |
| 板块热度排名 | `sector_fund_flow`（sector_type="行业",indicator="今日"）净流入降序 | ✅ |
| 板块内涨停股 | `[s for s in spots if s.get("board")==board]` 数 `change_pct>=9.8%` | ✅（复用 board_money_link 套路） |
| 早盘30分钟带量 | 分时数据 | ❌ 永久降级（该项0不崩） |

## 子系统 A：硬剔除层

step1/2/3/5 是通过/不通过的硬门槛，忠于原文"删除/pass"语义。

### A1. step1 入选门槛

```
change_pct > 5% AND turnover_rate > 3% AND latest_price < 50
```

### A2. step2 雷区剔除

```
circulating_market_cap ∈ [10, 200] 亿
pe 不为空 且 pe <= 150（pe 为空或 pe<=0 视为亏损→剔除；pe>150 剔除）
st_type 为空（非 ST/*ST）
```

震荡市参数可调（市值 50–100亿弹性最佳），通过 Query 参数 `min_mv/max_mv/min_pe` 暴露。

### A3. step3 形态过滤（本地算 MA，不复用 signals）

**不复用 `signals.scan_signals`**——"多头排列"是 MA 排列形态，非信号触发点；本地算更准且不引入 signals 的触发语义。

```python
def _ma_arrange(code) -> dict:
    """从 stock_daily 取近 60 日 close，算 5/10/20/60 MA。
    返 {ma5,ma10,ma20,ma60, bullish_align, volume_breakout, need_history}
    - bullish_align: ma5>ma10>ma20 且 ma20 较 5 日前上行（向上发散，5日窗口判趋势）
    - volume_breakout: close>ma60 且 当日 volume >= 2× 过去20日均量
    - need_history: <60 日历史→True，该股 step3 跳过不崩
    """
```

step3 通过条件：`bullish_align OR volume_breakout`；空头排列（ma5<ma10<ma20）或均线粘合（`(max(ma5,ma10,ma20)-min)/min(ma5,ma10,ma20) < 0.5%`）→ 剔除。

### A4. step5 板块助攻（行业口径）

复用 `board_money_link` 的反查套路：

```python
def _board_assist(code, spots, sff) -> dict:
    """返 {board, board_rank, board_zt_count, pass}
    - board: stock_spot.board（行业口径）
    - board_rank: sector_fund_flow(行业,今日) 净流入降序排名
    - board_zt_count: intra=[s for s in spots if s.get("board")==board] 数 change_pct>=9.8%
    - pass: board_rank<=5 AND board_zt_count>=2
    无 board 字段→pass=False,board=None
    """
```

板块口径固定**行业**（`stock_spot.board` 通常是行业板块；概念板块不查，避免一只股多概念歧义）。

## 子系统 B：软打分层（step4，排序用）

step4 不做硬门槛，仅产 0–100 软分用于排序：

```python
def _step4_score(s) -> float:
    """返 0-100 软分。
    - 量比强度: volume_ratio>2.5 满分，线性缩放（该项 0.5 权重）
    - 涨幅温和: change_pct<7% 满分，>7% 扣分至 0（避免追高，0.5 权重）
    - 分时项: 永久降级，0 不崩（无分时数据源）
    """
```

## 子系统 C：编排主函数

```python
def daily_strong_rank(universe="stock", codes=None, limit=50, days=30,
                      min_change_pct=5.0, min_turnover=3.0, max_price=50.0,
                      min_mv=10.0, max_mv=200.0, max_pe=150.0) -> dict:
    """5步混合编排。返 {rows, n_scanned, n_pass, need_history, cached, ts}
    1. 单次 query_rows("stock_spot",limit=0) 取全表（同 board_money_link 套路）
    2. 粗筛 top K: codes 限定→不粗筛；否则按 change_pct 降序取 top K(≤200)
    3. 逐股: step1→step2→step3→step5 硬剔除，step4 软打分
       - 任一硬步不通过仍保留行（带 pass 标记），但排序沉底
       - step3 无历史→need_history=True，该步 pass=False 不崩
    4. 排序键: 硬通过数×10 + 软分 降序
    5. 截 top limit，30s 进程缓存
    NaN→None 守卫(_nan)，防 allow_nan=False 500
    """
```

## 排序与输出

每行字段：
`code/name/change_pct/turnover_rate/latest_price/circulating_market_cap/pe/st_type/volume_ratio/board/board_rank/board_zt_count/step1_pass..step5_pass/score/need_history`

排序键：`硬通过数(step1+step2+step3+step5)×10 + step4软分` 降序。全通过的清单在前，部分通过的（带某步未过）沉底但保留可见（便于诊断为何未过）。

## 降级链

- 无 `stock_daily` 历史 → step3 `need_history=True`，该步 pass=False，不崩
- 无 `board` 字段 → step5 `board=None`，pass=False
- 分时项 → 永久降级（无数据源）
- `stock_spot` 为空 → 返空清单 + note "先 /api/refresh"
- `sector_fund_flow` 为空 → step5 板块排名无法算，pass=False

## 测试

`tests/test_daily_strong.py`（mock `db.query_rows`，不触网）：

1. `test_step1_pass` — 涨幅>5/换手>3/价<50 通过
2. `test_step2_reject` — 市值/PE/ST 各雷区剔除
3. `test_step3_ma_bullish` — 多头排列通过、空头排列剔除、均线粘合剔除
4. `test_step3_volume_breakout` — 站稳60日线+量翻倍通过
5. `test_step3_need_history` — <60日历史→need_history=True 不崩
6. `test_step4_score` — 量比/涨幅温和打分、涨幅>7%扣分
7. `test_step5_board_assist` — 板块前5+≥2涨停通过、无board字段降级
8. `test_rank_order` — 硬通过数×10+软分降序、不通过沉底
9. `test_coarse_filter_topk` — codes限定不粗筛、否则 top K
10. `test_cache` — 30s 内命中缓存
11. `test_empty_spot` — 空表返空清单+note
12. `test_nan_none` — NaN→None 守卫防 500

## 路由

```python
@app.get("/api/daily-strong")
def daily_strong(universe: str = Query("stock"),
                 limit: int = Query(50, ge=1, le=200),
                 days: int = Query(30, ge=5, le=120),
                 min_change_pct: float = Query(5.0, ge=0.0, le=20.0),
                 min_turnover: float = Query(3.0, ge=0.0, le=50.0),
                 max_price: float = Query(50.0, ge=1.0, le=500.0),
                 min_mv: float = Query(10.0, ge=0.0, le=1000.0),
                 max_mv: float = Query(200.0, ge=10.0, le=10000.0),
                 max_pe: float = Query(150.0, ge=0.0, le=1000.0),
                 codes: str = Query("")):
    """每日强势股5步漏斗+板块助攻(混合:硬剔除+软打分)。
    复用 stock_spot/sector_fund_flow/stock_daily，不触网不新增表。
    依赖 stock_daily(step3/MA)，无历史该步降级。挂 cand_disclaimer。"""
    from screener import daily_strong as ds
    cl = [c.strip() for c in codes.split(",") if c.strip()] if codes else None
    res = ds.daily_strong_rank(universe=universe, codes=cl, limit=limit, days=days,
                               min_change_pct=min_change_pct, min_turnover=min_turnover,
                               max_price=max_price, min_mv=min_mv, max_mv=max_mv, max_pe=max_pe)
    return _wrap(res, {"cand_disclaimer":
                       "每日强势清单——多步机械漏斗+板块助攻排序观察清单，非荐股非买卖信号，盈亏自负。"})
```

## 前端入口

实时筛选 tab 或优质筛选 tab 加"每日强势"入口（实施时定，非 spec 范围）。状态保持不重新 fetch（同现有 tab 约定）。

## 改动检查清单对齐

- 新增 SQLite 列/表 → **无**（全复用现有表）
- 新增采集源 → **无**（零触网）
- 新增 API 路由 → 用 `_wrap()` + `cand_disclaimer`，对齐 nextday-strong 风格
- 新增筛选编排层 → **不新增表**（复用 stock_spot/sector_fund_flow/stock_daily），因子只读不改
- 单测放 `tests/test_daily_strong.py`，mock `db.query_rows` 不触网
- NaN→None 守卫复用 `_nan`（防 `allow_nan=False` 500）
- 措辞"每日强势/板块助攻/观察清单"，挂 `cand_disclaimer`
