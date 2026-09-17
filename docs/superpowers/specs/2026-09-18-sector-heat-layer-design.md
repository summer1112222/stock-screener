# 宏观/行业景气排序加成层（sector-heat）设计

日期：2026-09-18　·　状态：approved（设计已确认）　·　子项目：A（B 风格感知 / C 看板 复用此模块，另行设计）

## 1 目标与边界

给 `quality`（优质筛选）与 `smart_money`（主力动向）补上唯一缺失的**宏观/行业景气**维度：
把"板块资金验证 + 政策主题命中"捏成一个**行业景气分位**，作为 main 清单的**排序上下文加成**，
而不是预测大盘、不荐板块、不新增口径。

**刚性约束**：
- **零新增采集源**：全复用已采数据（`sector_fund_flow` / `stock_spot` / `board_stocks`）+ 一个**静态**政策→板块映射表（从 gov.cn 政策要点人工维护，非实时抓取，避免脆弱新源）。
- **不改共振签名**：不入 `_DEFAULT_DIM_WEIGHTS`、不改 `min_dims`/`hits`/缓存键/warmup → quality 测试、前端口径零波及。
- 景气分位是横截排序上下文（大板块景气 → 该板块成分略加分），**非**孤立提拉某只票。
- 合规：措辞"行业景气机械排序上下文/资金+政策验证"，不预测板块涨跌。disclaimer 追加说明。

## 2 架构

```
screener/sector_heat.py (新模块, 纯函数 + 静态映射)
 ├─ POLICY_THEMES: dict[str, list[str]]   # 政策主题 → 板块名列表 (静态, 人工维护)
 ├─ board_heat(spot, fund_flow) -> pd.Series  # 板块 → heat ∈ [0,1]
 │     heat = 0.6*fund_pct + 0.4*breadth_pct
 │       fund_pct    = 板块资金净流入 横截 rank-pct
 │       breadth_pct = 板块内上涨家数占比(涨停加成) 横截 rank-pct
 ├─ policy_hit(board) -> float           # 板块命中静态政策主题 → 提前量加成 ∈ {0, 0.05}
 └─ stock_sector(code) -> (board, heat, policy_boost)  # 个股→所属板块反查(复用 board_stocks)
```

数据依赖（全只读）：
| 源 | 字段 / 用途 | 采集层 |
|---|---|---|
| `sector_fund_flow` | 板块名、净流入 | refresh 已采 |
| `stock_spot` | code、board、涨跌、是否涨停 | 已采 |
| `board_stocks` | 个股→所属板块（按需） | 按需、cache |

## 3 quality 接入（精排阶段加成，不动共振）

复用现有盘口精排 `_refine_by_quote` 同一路径，另加**行业景气分位**一族：

```
sector_heat(code) = 该 code 所属板块的 board_heat            # 板块资金+宽度景气
final             = 0.6 * resonance + 0.4 * sector_heat(code)
                                                              + policy_boost(code)
```

- `policy_boost` 仅对命中政策主题的板块 `+0.05`（clip 到 [0,1]，仅提前量，不过度提拉）。
- 精排只对 `refine_pool` 小名单触板块反查，非全市场（守 CLAUDE.md 盘口精排"小名单触网"约束，但板块反查走 `board_stocks` 缓存不触网新增）。
- 不改共振签名与分位缺失语义；`refine=False` 时跳过（与现有精排一致）。
- API `/api/quality` 响应 item 追加 `sector_heat`/`policy_hit`（raw 展示，不改变 main 之外清单）。

## 4 smart_money 接入（只读上下文标注）

- `top_by_amount` / `today_list` 行附 `sector_heat`、`policy_hit` 标注，**不改主排序**。
- 目的：主力清单可识别"这些票在不在景气主线上"，为人工判断提供上下文，非买卖信号。

## 5 与 B(风格感知)/ C(看板) 的关系

- **C 看板**：直接渲染 `board_heat` + `policy_hit` → `/api/sector-heat`（另行设计，复用此模块）。
- **B 风格感知**：留 `style_gauge()` 空实现接口，后续子项目填（检测大小盘/科技红利 5 日相对强度微调 nextday 加权）。本次不实现。

## 6 错误处理

- 任一源为空/失败 → 该板块 heat 记 NaN，不崩；无 heat 的 code 不做加成（等价现有"分位缺失"语义）。
- `stock_sector` 反查失败（板块无成分股映射）→ 返回 (None, None, 0)，不加分不降权，诚实缺失。
- 静态 `POLICY_THEMES` 缺失某板块名 → `policy_hit` 返回 0，不抛错。

## 7 测试 `tests/test_sector_heat.py`（纯 mock 三源，不触网）

1. `board_heat` rank-pct 正确（资金排名高 → heat 高）且 `[0,1]`。
2. `breadth_pct` 涨停加成、上涨家数占比正确。
3. `0.6/0.4` 加权权衡（资金弱但宽度强的中位结果）。
4. `policy_hit` 命中返回 0.05、未命中返回 0、空映射返回 0。
5. quality 精排加成：mock `_refine_by_quote` 路径，断言 `final = 0.6*resonance + 0.4*heat (+policy)` 且共振签名未变（hits/min_dims 仍按原逻辑）。
6. `stock_sector` 反查失败 → 不崩、返回 None/0。
7. 空源 → heat/该 code 不加成，不抛。

## 8 交付清单

- 新模块 `screener/sector_heat.py`（纯函数 + `POLICY_THEMES` 静态表，预填 gov.cn 当前六大主题：先进制造/电子信息/智能家居消费/基础研究(算力)/人形机器人/智能驾驶）。
- `backtest/quality.py` 精排阶段加行业景气加成（`refine=True` 时才走）。
- `screener/smart_money.py` 行附 `sector_heat`/`policy_hit` 标注。
- 测试 `tests/test_sector_heat.py`。
- CLAUDE.md 路由速查 + 检查清单补 `/api/quality` 字段与 sctor_heat 模块说明。

## 9 合规说明

景气分位 = "板块资金净流入排名 + 板块内上涨宽度 + 静态政策命中"的**横截排序上下文**，
机械计算、非预测、非荐板块。措辞"行业景气机械排序上下文/资金+政策验证"，disclaimer 追加
"行业景气为机械排序上下文，非板块预测，非买卖信号"。