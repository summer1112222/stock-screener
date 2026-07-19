# 设计：P2 signals 胜率 + ETF 因子 + 组合优化

- **日期**：2026-07-19
- **状态**：已确认，待写实现计划
- **范围**：在 P0/P1 落地基础上，增强 quality 生态的三项——signals 历史胜率回测、ETF 特化因子填口径2、quality 组合层最小方差开关。不新增 SQLite 表/schema，不改 disclaimer。

## 1. 背景与目标

P0/P1 已落地交易成本、滚动 walk-forward、幸存者偏差结构化、反转/Amihud 因子、个股 AND 过滤、buffett 缓存。quality 编排层仍剩三个缺口：

1. `signals.scan_signals` 只报"今日触发"，不算历史胜率；quality 口径4 用"触发数/5"等权，把 rsi_oversold 与 golden_cross 当同等权重，不合理。
2. `quality._dim_scores` 口径2（价值质量）对 universe=="etf" 恒空（ETF 无基本面），ETF 优质筛选永远缺一维。
3. `quality._apply_combo` 是贪心（按 resonance 降序 + 行业≤N + 相关性≤阈值过滤），无风险预算/最小方差，组合层未用风险信息。

本设计落地 P2 三项，让 quality 对个股/ETF 都更可信、组合层可选风险优化。

## 2. 合规边界（硬约束）

- **不荐股、不输出买卖点、不承诺收益**。signals 胜率是"历史触发统计事实"非预测；ETF 因子是"机械统计量"；min_var 权重是"风险预算机械分配"非推荐仓位。
- `cand_disclaimer`/`_CAND_DISCLAIMER` 沿用，不新增 disclaimer 文本。
- 措辞保持"筛选/排序/观察清单/机械触发"，禁"推荐/买入/卖出"。

## 3. P2-1 signals 历史胜率回测

- **文件**：`backtest/signals.py`（新增 `backtest_signals`）、`api/server.py`（新路由 `/api/signals/backtest`）、`backtest/quality.py`（口径4 改胜率加权）
- **改动**：
  - `signals.backtest_signals(universe, codes, signal_types=None, k_days=5, benchmark="sh000300") -> dict`：
    - 复用 `_uni_panels` 取 close/amount 面板 + `fetch_benchmark_hist` 取基准 close。
    - 对每个信号规则，扫历史每个交易日 t（t 从首个可算日到 len(close)-k_days-1）：
      - 用 ≤t 数据算该信号在 t 日的触发条件（MA5/MA20 穿越用 ≤t 的 rolling；RSI 用 ≤t 的 _rsi；放量用 t 日 amount vs ≤t 的 5日均；动量用 ≤t 的 20日动量）。
      - 若 t 日触发，记录 `t→t+k_days` 收益（close[t+k]/close[t]-1）与基准同区间收益。
    - 汇总每信号：`{triggers, abs_win_rate(收益>0), excess_win_rate(收益>基准), mean_ret, median_ret, n_samples}`。n_samples<10 时标 `"样本不足"`。
  - quality 口径4（`_dim_scores` 口径4）：现在 `trig/5`（触发数等权）；改为 `excess_win_rate`（超基准胜率）的横截 pct。**code 级聚合**：该 code 触发的各信号的 `excess_win_rate` 取均值作为该 code 的口径4 原始值，再横截 `_to_pct`。胜率缺失/样本不足（n<10）时降级回 `trig/5`。
  - 路由 `/api/signals/backtest`（POST，复用 BTEvalReq 类似参数 universe/codes + signal_types/k_days/benchmark），`_wrap` + `cand_disclaimer`。
- **数据流**：`/api/signals/backtest` → `backtest_signals` → 读 `*_daily` + 基准历史 → 逐 t 扫描 → 汇总 → `_wrap(cand_disclaimer)`。
- **错误处理**：`*_daily` 为空 → `{"error":"无历史数据，先 /api/backtest/fetch"}`；历史不足 25+k_days → `{"error":"历史不足"}`；基准拉取失败 → 仅报绝对胜率，excess_win_rate=None。
- **测试**：合成 close（含确定的 MA 穿越点）+ 基准 close，断言 abs_win_rate∈[0,1]、n_samples 正确、excess 口径可算；样本不足分支。

## 4. P2-2 ETF 特化因子（填口径2）

- **文件**：`backtest/quality.py`（口径2 对 etf 改用 ETF 因子 + 新增 `ETF_BENCHMARK_MAP` 常量）
- **改动**：
  - 新增模块级常量 `ETF_BENCHMARK_MAP = {"510300":"sh000300","510050":"sh000016","510500":"sh000905","159915":"sz399006","510310":"sh000300","588000":"sh000688","512100":"sh000852","510160":"sh000300"}`（主流宽基 ETF→基准指数；未映射的 ETF 该因子 None）。
  - `_dim_scores` 口径2 对 `universe=="etf"` 改为：
    - **成交额稳定性**：ETF daily amount 的 `rolling(n).std() / rolling(n).mean()`（越小越稳定越优，取负 zscore）。
    - **跟踪误差**：ETF close 日收益 − 基准 close 日收益 的差序列 `rolling(n).std()`（越小越好，取负 zscore）。基准用 `fetch_benchmark_hist(ETF_BENCHMARK_MAP[code])`，未映射或拉取失败 → 该因子 None。
    - 合成 `(_zscore(-tracking_error) + _zscore(-amount_vol)) / 2 → _to_pct`。
    - status["2"] = "ok(ETF:跟踪误差+成交额稳定)"；因子源失败标 err 不崩。
  - 个股口径2 不变（仍走 buffett）。
- **数据流**：`quality_rank(universe="etf")` → `_dim_scores` 口径2 → 拉 etf_daily + 基准 → 算两因子 → zscore 合成 → pct。
- **错误处理**：etf_daily 未 fetch → 口径2 标 `err:无历史数据`（与个股 buffett 失败一致）；基准未映射 → 跟踪误差 None，仅用成交额稳定性（降级，不崩）。
- **测试**：合成 etf_daily close+amount + 基准 close，断言 tracking_error>0、口径2 对 etf 不再全 None、未映射基准时降级为单因子。

## 5. P2-3 quality 组合层最小方差开关

- **文件**：`backtest/quality.py`（`quality_rank` 加参数 + `_apply_combo` 分支）
- **改动**：
  - `quality_rank` 加 `combo_method: str = "greedy"` 参数（默认 greedy，行为不变）。
  - `_apply_combo` 若 `combo_method=="min_var"`：
    - 先用现有贪心逻辑（行业≤max_per_board + 相关性≤max_corr）筛入选池（保 limit 规模）。
    - 在入选池上解最小方差权重：`Σ = 收益率协方差矩阵`（复用 `_corr_matrix` 的收益率，再算方差对角+协方差），`w = Σ⁻¹·1 / (1ᵀ·Σ⁻¹·1)`，用 `np.linalg.solve` 或 `np.linalg.inv`（numpy，免 scipy）。
    - Σ 奇异（`np.linalg.LinAlgError` 或条件数过大）→ 降级 `1/方差` 加权（等风险贡献近似）。
    - 负权重归零后归一（long-only 约束）；若全零兜底等权。
    - 输出每标的 `weight` 字段（greedy 路径 weight 均为 1/池大小，保持一致）。
  - 前端：默认 greedy 前端可不改；可选加 combo_method 下拉（非必须）。
- **数据流**：`quality_rank(..., combo_method="min_var")` → `_apply_combo(min_var)` → 贪心筛池 → 解 Σ → 解析权重 → 归一 → 输出 weight。
- **错误处理**：历史不足算协方差 → 降级等权（status 标注）；Σ 奇异 → 1/方差加权；全零 → 等权。
- **测试**：合成 close 算协方差，min_var 权重和=1、非负；Σ 奇异（构造完全相关标的）降级不崩；greedy 路径权重不变。

## 6. 改动检查清单覆盖（CLAUDE.md）

- P2-1 新路由 `/api/signals/backtest` → `_wrap` + `cand_disclaimer`。
- P2-2 新增 `ETF_BENCHMARK_MAP` 常量（纯常量，不动 schema/表）。
- P2-3 `quality_rank` 新参数 `combo_method` → 前端可选加下拉（默认不影响）。
- **不新增 SQLite 表/schema**（P2 全复用 etf_daily/stock_daily/基准历史/corr_matrix）。
- NaN→None：signals 胜率/ETF 因子结果走 quality 现有 `_to_float` + dict 输出，不新增 records 路径；min_var 权重为 float。

## 7. 非目标（YAGNI）

- 不做 ETF 折溢价率/规模/行业集中度（数据源 `fund_etf_spot_em` 无 IOPV/规模字段，akshare 探查不稳，跳过）。
- 不做风险平价（等风险贡献需迭代解，min_var 解析解已够）。
- 不做 signals 信号的组合策略回测（只算单信号胜率，不做多信号组合回测）。
- 不暴露 combo_method 为前端必选项（默认 greedy，前端可选加）。
- 不动 smart_money 时序化（P3）。

## 8. 测试策略

- 全部新逻辑放 `tests/`，合成数据，不依赖网络（基准历史用 monkeypatch 或合成）。
- P2-1：合成 close 含确定穿越点，断言胜率/样本数/超基准。
- P2-2：合成 etf_daily+基准，断言跟踪误差/口径2 非空/降级。
- P2-3：合成 close，断言 min_var 权重和=1/非负/奇异降级。
- 宿主跑 `python -m pytest tests/ -q`。

## 9. 实现顺序建议

P2-1 → P2-2 → P2-3（signals 胜率先落地，quality 口径4 改加权依赖它；ETF 因子独立；组合优化最后，需协方差矩阵稳定）。
