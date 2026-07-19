# 设计:AKShare 扩展数据源(ST/完整财报/股东行为/机构视角)

- **日期**: 2026-07-19
- **范围**: 方案3 落地——用 AKShare 补 4 类数据源
- **状态**: 设计已用户逐节认可,待 spec 复核

## 1. 背景与目标

当前数据口径:spot/history/smart_money(4 通道)/buffett(财务摘要)。限制:

- AKShare 在当前出口 IP 被东财封(`RemoteDisconnected`/502),`_install_http_patch()` 注入 UA+Referer+退避+代理只能绕过部分。
- buffett 用 `stock_financial_abstract` 摘要,FCF 用"经营现金流量净额"代理,非真实自由现金流。
- ST 靠 spot 的 name 含"ST"排除,无独立名单与历史变迁。
- 无机构视角(研报评级/千股千评)、无高管增减持/限售解禁。

本批次扩展 4 类数据源,沿用现有采集约定,不破坏合规边界。

## 2. 合规边界(硬约束,沿用 CLAUDE.md)

- 所有新路由走 `api/server.py` 的 `_wrap()`,按域挂 disclaimer:
  - ST 名单:默认 `_wrap`(仅 disclaimer,事实清单非荐股口径)。
  - 高管增减持/限售解禁:`cand_disclaimer`(主力动向观察清单口径,复用 `SM_CAND_DISCLAIMER`)。
  - 研报/千股千评:`cand_disclaimer`("机构视角机械汇总,非荐股非买卖信号,盈亏自负")。
- 完整财报仅供 buffett 评分计算,不对外输出买卖点;措辞保持"机械评分/研究优先级"。
- 不新增推荐/买入/卖出逻辑;候选池口径不变。

## 3. 采集编排方案(方案A,已选)

按数据形态匹配采集时机:

- **列表型(全市场一次返回)** → 进 `refresh_all` / `refresh_today`:
  - ST 全名单(`stock_zh_a_st_em`)→ `refresh_all`
  - 高管增减持(`stock_hold_management_em`,按日期)→ `refresh_today`
  - 限售解禁(`stock_share_change_em`,按月份)→ `refresh_today`
  - 研报评级(`stock_research_report_em`,按日期范围,近 N 日)→ `refresh_all`
- **per-code 明细型** → 按需拉取 + 7 天 TTL 缓存(同 buffett `financial_abstract_cache` 模式):
  - 完整三大财报(`stock_balance_sheet_by_report_em` / `stock_cash_flow_sheet_by_report_em` / `stock_profit_sheet_by_report_em`)
  - 千股千评(`stock_comment_detail`)

**理由**:per-code 全市场预抓(5500×3 表)在东财被封现状下必然失败;列表型接口快,进全量刷新性价比高。

## 4. 文件清单

| 文件 | 动作 | 内容 |
|---|---|---|
| `data/models.py` | 改 | 新增 `st_list` / `research_report` / `fundamentals_cache` 三表的 SCHEMA_SQL + TABLE_FIELDS + ST_LIST_ALIASES / RESEARCH_REPORT_ALIASES |
| `data/db.py` | 不改 | 新表 `CREATE TABLE IF NOT EXISTS` 直接生效,`_migrate` 无需新增条目(全是新表,smart_money_action 无新列) |
| `data/collector.py` | 改 | 新增 `fetch_st_list()`;`refresh_all` 加 ST 抓取块 |
| `data/smart_money.py` | 改 | `CHANNEL_STATUS` 加"高管增减持"/"限售解禁";新增 `collect_management_hold(date)` / `collect_share_unlock(date)`;`refresh_today` plan 加两项 |
| `data/fundamentals.py` | 新建 | 完整三大财报按需采集+缓存:`fetch(code, source)` source ∈ {balance,cashflow,profit},7 天 TTL,线程超时包装 |
| `data/research.py` | 新建 | 研报 `fetch_reports(recent_days)` 列表型进 refresh + `query_reports`/`fetch_comments` 查询;千股千评 `fetch_comments(code)` 按需+缓存(source=comments) |
| `screener/smart_money.py` | 改 | 新增 `unlock_by_month(month, code)`(限售解禁按 as_of 月份查);高管增减持查询复用现有 `today_list`/`top_by_amount` 零改 |
| `backtest/buffett.py` | 改 | `analyze()` best-effort 调 `fundamentals.fetch(code,"cashflow")` 取真实 FCF(经营-资本开支),失败降级摘要代理;新增 `fcf_source` 字段 + `_pick_col_sum` 辅助 |
| `api/server.py` | 改 | 新增路由 `/api/st-list` `/api/management` `/api/share-unlock` `/api/research` `/api/comments`,均 `_wrap` + 对应 disclaimer |
| `web/index.html` | 改 | buffett 卡片显示 `fcf_source` 标签;确认主力动向通道面板按 `channels` 循环渲染(零改或补循环);研报/千股千评展示区可选 |
| `tests/` | 新建 | `test_st_list.py` / `test_management_unlock.py` / `test_fundamentals_cache.py` / `test_buffett_fcf.py` / `test_research.py`(mock db,不触网) |

## 5. Schema 与迁移

### 新增表

```sql
-- ST 全名单快照(同 spot 模式,code 主键覆盖)
CREATE TABLE IF NOT EXISTS st_list (
    code TEXT PRIMARY KEY,
    name TEXT,
    st_type TEXT,          -- ST / *ST / 其他
    latest_price REAL,
    change_pct REAL
);

-- 研报评级(列表型,多机构同日;靠 UNIQUE 去 REPLACE,同 smart_money_action 模式)
CREATE TABLE IF NOT EXISTS research_report (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT, name TEXT,
    rating TEXT,          -- 增持/买入/中性/减持
    title TEXT,
    org TEXT,             -- 机构
    analyst TEXT,
    pub_date TEXT,
    target_price REAL,
    ts TEXT,
    UNIQUE(code, pub_date, org, title)
);

-- 完整三大财报 + 千股千评按需缓存(多 source,7 天 TTL)
CREATE TABLE IF NOT EXISTS fundamentals_cache (
    code TEXT, source TEXT,   -- balance / cashflow / profit / comments
    payload_json TEXT,
    ts TEXT,
    PRIMARY KEY (code, source)
);
```

字段集(进 `TABLE_FIELDS`):
- `ST_LIST_FIELDS = {"code","name","st_type","latest_price","change_pct"}`
- `RESEARCH_REPORT_FIELDS = {"code","name","rating","title","org","analyst","pub_date","target_price","ts"}`(不含 id)
- `FUNDAMENTALS_CACHE_FIELDS = {"code","source","payload_json","ts"}`

### 复用现有表(零迁移)

- **`smart_money_action`**:高管增减持/限售解禁直接并入,复用 `channel` 字段,**不加列**:
  - 高管增减持:`actor`=高管名,`action`=增持/减持,`amount`=变动金额,`raw` 存明细
  - 限售解禁:`actor`=股东名,`action`=解禁,`amount`=解禁数量,`as_of`=解禁日期(可能未来),`raw` 存明细
  - `UNIQUE(date, code, channel, actor, action)` 天然去重
- **`financial_abstract_cache`**:buffett 摘要缓存保持不动,作 `fundamentals_cache` 失败时降级 fallback

### 迁移影响

`db.init_db` 先 `executescript(SCHEMA_SQL)`(新表直接生效),`_migrate` 无需新增条目。旧库直接拿到新表,持久化卷不受影响。

### AKShare 别名映射(models.py 新增)

```python
ST_LIST_ALIASES = {
    "代码": "code", "code": "code",
    "名称": "name", "name": "name",
    "涨跌幅": "change_pct", "change_pct": "change_pct",
    "最新价": "latest_price", "latest_price": "latest_price",
    # st_type 由采集层按 name 前缀(ST/*ST)解析
}
RESEARCH_REPORT_ALIASES = {
    "代码": "code", "股票代码": "code", "code": "code",
    "名称": "name", "股票简称": "name", "name": "name",
    "评级": "rating", "投资评级": "rating", "rating": "rating",
    "研报标题": "title", "标题": "title", "title": "title",
    "机构": "org", "研究机构": "org", "org": "org",
    "研究员": "analyst", "分析师": "analyst", "analyst": "analyst",
    "日期": "pub_date", "研报日期": "pub_date", "pub_date": "pub_date",
    "目标价": "target_price", "目标价（元）": "target_price",
}
```

## 6. 采集层

### 6.1 `data/collector.py` — ST 全名单

```python
def fetch_st_list() -> tuple[pd.DataFrame, bool, str]:
    """ST/*ST 全名单(东财 stock_zh_a_st_em)。无 THS 备援,被封标不可用不崩。
    st_type 由 name 前缀解析。"""
    if not _AK_OK:
        return pd.DataFrame(), False, _AK_ERR
    try:
        df = ak.stock_zh_a_st_em()
        norm = _normalize(df, ST_LIST_ALIASES)
        if not norm.empty:
            def _t(n):
                s = str(n or "")
                return "*ST" if s.startswith("*ST") else "ST" if s.startswith("ST") else "其他"
            norm["st_type"] = norm["name"].map(_t)
            return norm, True, ""
        return pd.DataFrame(), False, "st_list: 空结果"
    except Exception as e:
        return pd.DataFrame(), False, f"st_list: {e}"
```
`refresh_all` 在 stock_spot 块之后加:
```python
df, ok, err = fetch_st_list()
if ok:
    report["counts"]["st_list"] = db.upsert_rows("st_list", _to_records(df))
else:
    report["errors"].append(err); report["counts"]["st_list"] = 0
```

### 6.2 `data/smart_money.py` — 高管增减持 / 限售解禁

`CHANNEL_STATUS` 加:
```python
"高管增减持": {"ok": False, "source": "", "err": "未采集", "at": ""},
"限售解禁":   {"ok": False, "source": "", "err": "未采集", "at": ""},
```

新增(沿用 `(records, ok, err)` + `_rec()` + `_first_col()` + `_set_status` + `_friendly_err`):

```python
def collect_management_hold(date: str) -> tuple[list[dict], bool, str]:
    """高管增减持(东财 stock_hold_management_em,按日期全市场)。
    actor=高管名, action=增持/减持, amount=变动金额, raw 存明细。"""
    if not _AK_OK:
        return [], False, _AK_ERR
    try:
        df = ak.stock_hold_management_em()
    except Exception as e:
        _set_status("高管增减持", False, "", _friendly_err("高管增减持", e))
        return [], False, _friendly_err("高管增减持", e)
    if df is None or df.empty:
        _set_status("高管增减持", True, "", "当日无增减持")
        return [], True, ""
    col_code = _first_col(df, ["代码", "股票代码", "code"])
    col_name = _first_col(df, ["名称", "股票简称", "name"])
    col_actor = _first_col(df, ["变动人", "高管名称", "姓名"])
    col_action = _first_col(df, ["变动方向", "增减"])
    col_amt = _first_col(df, ["变动金额", "成交金额", "变动数额"])
    recs = []
    for _, r in df.iterrows():
        act = str(r.get(col_action) or "")
        action = "增持" if "增持" in act else "减持" if "减持" in act else act
        recs.append(_rec(date, r.get(col_code), r.get(col_name), "股票",
                        "高管增减持", r.get(col_actor), action,
                        r.get(col_amt), raw={k: _clean(v) for k, v in r.items()}))
    _set_status("高管增减持", True, "东财", "")
    return recs, True, ""


def collect_share_unlock(date: str) -> tuple[list[dict], bool, str]:
    """限售解禁(东财 stock_share_change_em,按月份)。date 取所在月,
    拉当月解禁清单;actor=股东, action=解禁, amount=解禁数量, as_of=解禁日期。"""
    if not _AK_OK:
        return [], False, _AK_ERR
    month = date[:7]
    try:
        df = ak.stock_share_change_em(symbol=month)
    except Exception as e:
        _set_status("限售解禁", False, "", _friendly_err("限售解禁", e))
        return [], False, _friendly_err("限售解禁", e)
    if df is None or df.empty:
        _set_status("限售解禁", True, "", f"{month} 无解禁")
        return [], True, ""
    col_code = _first_col(df, ["代码", "股票代码", "code"])
    col_name = _first_col(df, ["名称", "股票简称", "name"])
    col_actor = _first_col(df, ["解禁股东", "股东名称"])
    col_amt = _first_col(df, ["解禁数量", "解禁股数", "实际解禁数量"])
    col_date = _first_col(df, ["解禁日期", "解禁时间", "公告日期"])
    recs = []
    for _, r in df.iterrows():
        recs.append(_rec(date, r.get(col_code), r.get(col_name), "股票",
                        "限售解禁", r.get(col_actor), "解禁",
                        r.get(col_amt), as_of=str(r.get(col_date) or ""),
                        raw={k: _clean(v) for k, v in r.items()}))
    _set_status("限售解禁", True, "东财", "")
    return recs, True, ""
```

`refresh_today` plan 扩:
```python
plan = [("资金流", collect_fund_flow), ("北向", collect_northbound),
        ("龙虎榜", collect_dragon_tiger), ("十大股东", collect_holders),
        ("高管增减持", collect_management_hold),
        ("限售解禁", collect_share_unlock)]
```

**重要语义**:限售解禁 `date`=采集当日,`as_of`=真实解禁日期(可能未来),查询须按 `as_of` 月份过滤(见 §7.2)。

### 6.3 `data/fundamentals.py`(新)— 完整三大财报按需+缓存

完全沿用 buffett 缓存模式(`financial_abstract_cache` → 这里是 `fundamentals_cache` 多 source):

```python
_AK_TIMEOUT = 20       # 同 buffett,防 quality 逐只拉卡死
_CACHE_TTL_DAYS = 7

def _fetch_net(code: str, source: str):
    c = _strip_prefix(code)
    if source == "balance":   return ak.stock_balance_sheet_by_report_em(symbol=c)
    if source == "cashflow":  return ak.stock_cash_flow_sheet_by_report_em(symbol=c)
    if source == "profit":    return ak.stock_profit_sheet_by_report_em(symbol=c)
    return None

def fetch(code: str, source: str) -> tuple[pd.DataFrame | None, bool]:
    """返回 (df, stale)。缓存7天 TTL;_AK_OK=False 或单只超时(20s)降级返回过期缓存。"""
    df, status = _cache_get(code, source, allow_stale=False)
    if status == "hit":
        return df, False
    if _AK_OK:
        try:
            with ThreadPoolExecutor(max_workers=1) as ex:
                net = ex.submit(_fetch_net, code, source).result(timeout=_AK_TIMEOUT)
            if net is not None and not net.empty:
                _cache_set(code, source, net)
                return net, False
        except (FuturesTimeout, Exception):
            pass
    df_s, _ = _cache_get(code, source, allow_stale=True)
    if df_s is not None:
        return df_s, True
    return None, False
```

### 6.4 `data/research.py`(新)— 研报 + 千股千评

```python
def fetch_reports(recent_days: int = 30) -> tuple[pd.DataFrame, bool, str]:
    """研报(列表型,进 refresh_all)。stock_research_report_em 按日期范围取近 N 日,
    limit 防过大。"""
    if not _AK_OK:
        return pd.DataFrame(), False, _AK_ERR
    try:
        df = ak.stock_research_report_em(start_date=..., end_date=...)  # 近 N 日
        return _normalize(df, RESEARCH_REPORT_ALIASES), True, ""
    except Exception as e:
        return pd.DataFrame(), False, f"research: {e}"

def fetch_comments(code: str) -> tuple[dict | None, bool]:
    """千股千评(per-code 按需+缓存,source=comments)。超时包装同 buffett。"""
    ...

def query_reports(code: str | None = None, days: int = 30, limit: int = 200) -> dict:
    """从 research_report 表查近 N 日研报;code 非空则按 code 过滤。pub_date 降序。"""
    since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    where, params = ["pub_date >= ?"], [since]
    if code:
        where.append("code = ?"); params.append(code)
    rows = db.query_rows("research_report", where=" AND ".join(where),
                         params=tuple(params), order_by="pub_date DESC", limit=limit)
    return {"rows": rows, "total": len(rows)}
```
`refresh_all` 在 st_list 块之后加研报块:`db.upsert_rows("research_report", _to_records(df))`。

### 6.5 错误处理约定(沿用现有)

- 每个采集函数返回 `(df|records, ok, err)`,异常不抛崩
- 东财域名自动受 `collector._install_http_patch()` 保护(UA+Referer+退避+代理)
- 被封标不可用 + 前端灰点,不写 null 废行(同 `collect_fund_flow`)
- NaN→None 经 `_to_records`(列表型) / `_clean`(per-code raw)
- **实现约定**:akshare 接口入参/列名以当前版本为准,采集层用 `_first_col` 候选名容错,实现时单测 mock 验证字段映射(同现有 `smart_money.py` 风格)

## 7. API 路由

### 7.1 新增路由

```python
@app.get("/api/st-list")
def st_list():
    rows = db.query_rows("st_list", order_by="st_type, change_pct DESC")
    return _wrap({"rows": rows, "total": len(rows)})

@app.get("/api/management")
def management(days: int = 30):
    res = smart_money.top_by_amount(days=days, channel="高管增减持", limit=100)
    return _wrap(res, {"cand_disclaimer": SM_CAND_DISCLAIMER})

@app.get("/api/share-unlock")
def share_unlock(month: str | None = None, code: str | None = None):
    res = smart_money.unlock_by_month(month=month, code=code)
    return _wrap(res, {"cand_disclaimer": SM_CAND_DISCLAIMER})

@app.get("/api/research")
def research(code: str | None = None, days: int = 30, limit: int = 200):
    res = research_data.query_reports(code=code, days=days, limit=limit)
    return _wrap(res, {"cand_disclaimer": "机构研报评级机械汇总，非荐股非买卖信号，盈亏自负。"})

@app.get("/api/comments")
def comments(code: str):
    res = research_data.fetch_comments(code)
    return _wrap(res, {"cand_disclaimer": "千股千评机构视角机械汇总，非荐股非买卖信号，盈亏自负。"})
```

### 7.2 查询层扩展(`screener/smart_money.py`)

```python
def unlock_by_month(month: str | None = None, code: str | None = None) -> dict:
    """限售解禁按 as_of 月份查(channel=限售解禁)。
    month 形如 2026-07;不传取当月。code 非空则再按 code 过滤。as_of 升序。"""
    if not month:
        month = datetime.now().strftime("%Y-%m")
    where, params = ["channel = ?", "as_of LIKE ?"], ["限售解禁", f"{month}%"]
    if code:
        where.append("code = ?"); params.append(code)
    rows = db.query_rows("smart_money_action", where=" AND ".join(where),
                         params=tuple(params), order_by="as_of ASC", limit=0)
    total_amt = sum(r.get("amount") or 0 for r in rows if r.get("amount"))
    return {"rows": rows, "total": len(rows), "month": month, "total_amount": total_amt}
```
高管增减持查询复用现有 `today_list(channel="高管增减持")` / `top_by_amount(channel="高管增减持")`,零改动。

### 7.3 现有路由自动覆盖

- `/api/refresh`:`refresh_all` 扩,自动覆盖 ST + 研报
- `/api/smart-money/refresh`:`refresh_today` 扩,自动覆盖新增 2 通道
- `/api/smart-money/channels`:`channel_status()` 循环天然显示新通道灰点(DB 实况叠加)

## 8. buffett 增强(完整财报升级 FCF)

**仅升级 FCF,ROE/杠杆/EPS/商誉保持摘要口径**(避免破坏现有评分阈值)。

`analyze(code)` 顶部加 best-effort 完整现金流表拉取(失败不影响主流程):

```python
from data import fundamentals

def analyze(code: str) -> dict:
    code = str(code).strip()
    df, stale = fetch_abstract(code)          # 摘要(现有,优先)
    spot = _spot(code)
    res = {...现有...}

    # 新增:best-effort 完整现金流表,取真实 FCF
    cf_df, cf_stale = fundamentals.fetch(code, "cashflow")
    real_fcf = None
    fcf_source = "摘要代理(经营现金流量净额)"
    if cf_df is not None and not cf_df.empty:
        ocf = _pick_col_sum(cf_df, ["经营活动产生的现金流量净额", "经营活动现金流量净额"])
        capex = _pick_col_sum(cf_df, ["购建固定资产、无形资产及其他长期资产支付的现金",
                                      "购建固定资产无形资产和其他长期资产支付的现金"])
        if ocf is not None:
            real_fcf = (ocf - capex) if capex is not None else ocf
            fcf_source = "完整现金流表(经营-资本开支)"
            res["stale_data"] = res.get("stale_data") or cf_stale
    ...
```

`ratios` 块 FCF 口径:
```python
ocf_ann = _annual(ocf_p, latest_only=True)    # 摘要代理(现有,作 fallback)
ni_ann = _annual(ni_p, latest_only=True)
if real_fcf is not None:
    ratios["fcf_proxy"] = round(float(real_fcf), 2)
    ratios["fcf_source"] = fcf_source
    if ni_ann:
        ratios["fcf_to_netincome"] = round(float(real_fcf / ni_ann), 2)
elif ocf_ann and ni_ann:
    ratios["fcf_proxy"] = round(float(ocf_ann), 2)
    ratios["fcf_source"] = fcf_source
    ratios["fcf_to_netincome"] = round(float(ocf_ann / ni_ann), 2)
```
`red_flags` FCF 质量阈值(`<0.5`)不变(基于更准的 real_fcf)。`priority` 评分逻辑不变。

新增辅助:
```python
def _pick_col_sum(df: pd.DataFrame, candidates: list[str]) -> float | None:
    """从完整财报表(行=报告期)取最近年报(报告期 endswith 1231)行的命中科目值。
    列名模糊匹配(contains),NaN→None。"""
    # 找年报行(若有 报告期 列 endswith 1231),否则取首行;找命中列取值 _to_float
    ...
```

降级链:完整现金流表失败/超时 → 摘要代理(现有)→ `fcf_source` 标"摘要代理"。`_AK_OK=False` 时整层跳过。

## 9. 前端(`web/index.html`)

- **必做**:
  1. buffett 卡片显示 `fcf_source` 标签("FCF来源:完整现金流表/摘要代理");`LABEL` 字典补 `fcf_source: "FCF来源"`。
  2. 确认"主力动向"tab 通道面板按 `channels` dict 循环渲染(若硬编码 4 通道则补为循环),使新增 2 通道灰点自动显示。
- **可选(留后续)**:研报/千股千评展示区、ST 名单面板。

## 10. 测试(`tests/`,mock db,不触网)

- `test_st_list.py`:`fetch_st_list` mock `ak.stock_zh_a_st_em` → 验证 st_type 从 name 前缀解析;mock 抛异常 → `(df, False, err)` 不崩。
- `test_management_unlock.py`:`collect_management_hold`/`collect_share_unlock` mock ak → 验证 `_rec` 的 channel/action/as_of 填充;`unlock_by_month` mock `db.query_rows` → 验证 as_of LIKE 月份过滤 + 汇总金额。
- `test_fundamentals_cache.py`:`fundamentals.fetch` mock ak + db → hit/stale/miss 三态、超时降级返回 stale、`fundamentals_cache` upsert payload;`_pick_col_sum` 列名模糊匹配年报行。
- `test_buffett_fcf.py`:mock `fundamentals.fetch(code,"cashflow")` 返回合成现金流表 → `ratios.fcf_source`="完整现金流表"、`fcf_proxy`=经营-资本开支;mock 返回 None → 降级"摘要代理",评分不崩。
- `test_research.py`:`fetch_reports` mock ak → `_normalize` 别名映射;`query_reports` mock db → pub_date 过滤。

## 11. 已知风险与降级

- 东财被封:`stock_hold_management_em`/`stock_share_change_em`/`stock_research_report_em`/`stock_zh_a_st_em` 均走东财,出口 IP 被封时这些通道标不可用(灰点),不崩。`SCREENER_HTTPS_PROXY` 代理后可用。
- akshare 接口列名版本漂移:`_first_col` 候选名容错,单测 mock 验证。
- 完整财报 per-code 慢:buffett `analyze_many` 已有 `max_workers=8` + 单只 20s 超时,quality 逐只拉不卡死。
- 限售解禁 as_of 可能未来日期:`unlock_by_month` 按月份过滤,不依赖 date。

## 12. 不在本批次范围

- 完整三大财报中的资产负债表/利润表直接对外查询接口(本批次仅用于 buffett 内部 FCF 升级,不暴露 `/api/fundamentals` 路由)。
- 研报/千股千评前端展示区(§9 可选项)。
- Tushare/聚宽/官网 CSV 等其他数据源(方案1/4)。
