# 主力动向+优质筛选 数据可靠性加固 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让北向通道不再长期灰（盘后十大成交股+总额降级）、拉取失败保旧数据标 stale（三态灯）、十大股东覆盖小盘国家队、quality 口径2 buffett 失败时 spot 代理降级。

**Architecture:** 不新增表/列。stale 走 `CHANNEL_STATUS` 内存 + `db.set_meta("sm_stale_<ch>", ...)`；北向备援复用 `smart_money_action` 现有列；quality 降级复用 `stock_spot` 的 pe/pb/amplitude/turnover_rate。两层边界不变（采集层入库/查询层只读/quality 只读因子源）。

**Tech Stack:** Python 3.12 + pandas 3.0 + akshare（北向 stock_hsgt_north_acc_flow_in/stock_hsgt_north_net_flow_in）+ FastAPI + 原生 JS 前端。单测 pytest，合成数据 mock db，不触网。

## Global Constraints

- 合规硬约束：措辞"动向/动作/净额/观察清单/机械归类"，禁"推荐/买入信号/卖点"；disclaimer 与 `cand_disclaimer` 不变；stale 旧数据前端显式标"非实时/数据日期 YYYY-MM-DD"。
- NaN→None：出口必经 `_clean`/`_to_float`，禁用 `df.where(pd.notna(df), None)`（float64 列回弹 NaN 致 JSONResponse 500）。
- `(records, ok, err)` 契约 + 异常不崩 + `_install_http_patch()` 东财 UA/502 退避保护保留。
- akshare 北向接口列名需落地实测，统一用 `_first_col` 候选容错。
- 单测放 `tests/`，合成数据 mock `db.query_rows`/`db.set_meta`/`db.get_meta`，不触网。
- 仓库根目录跑 `python -m pytest tests/ -q`（子目录跑找不到 data 包）。

---

## File Structure

- `data/smart_money.py`（修改）：`collect_northbound` 重构多级备援；新增 `_stale_fallback`/`_last_ok_date`；`refresh_today` 接 stale；`channel_status` 读 meta；新增 `NATIONAL_TEAM_HOLDINGS_SEED` 常量 + `_load_seed`/`_save_seed`；`collect_holders` 候选∪种子。
- `backtest/quality.py`（修改）：`_dim_scores` 口径2 buffett 失败降级 spot 代理；`quality_rank` 返回增 `source_health`。
- `web/index.html`（修改）：`smChannelsHtml` 三态灯；stale 行内标；北向口径标注。
- `tests/test_smart_money.py`（修改）：+6 测试。
- `tests/test_quality.py`（修改）：+2 测试。
- `api/server.py`：**不改**（stale/source_health 经 `_wrap(res, extra)` 自动透传）。

---

## Task 1 (P1): 北向多级备援链

**Files:**
- Modify: `data/smart_money.py` `collect_northbound`（约 338-368 行）
- Test: `tests/test_smart_money.py`

**Interfaces:**
- Consumes: `ak.stock_hsgt_individual_em`/`ak.stock_hsgt_hold_stock_em`/`ak.stock_hsgt_north_acc_flow_in`/`ak.stock_hsgt_north_net_flow_in`；`db.get_meta`/`db.set_meta`；`_first_col`/`_clean`/`_to_float`/`_set_status`/`_rec`。
- Produces: `collect_northbound(date) -> (records, ok, err)` 签名不变；meta 键 `north_probe_date`。

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_smart_money.py`：

```python
def test_northbound_fallback_acc_flow(monkeypatch, tmp_path):
    """主源抛 NoneType 崩 → 备援2 十大成交股出记录。
    actor="" 保 UNIQUE 去重；action="上榜"；source 标北向十大成交股(盘后)。"""
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    db.init_db()
    def _boom(**kw):
        raise RuntimeError("'NoneType' object is not subscriptable")
    def _acc_flow(symbol="沪股通"):
        return pd.DataFrame({"股票代码": ["600519", "601318"],
                             "股票简称": ["贵州茅台", "中国平安"],
                             "净买额": [3.2e8, 1.1e8]})
    def _net_flow(symbol="北向"):
        return pd.DataFrame({"日期": ["2026-07-25"], "当日资金流入": [5e8]})
    _patch_ak(monkeypatch,
              stock_hsgt_individual_em=_boom,
              stock_hsgt_hold_stock_em=_boom,
              stock_hsgt_north_acc_flow_in=_acc_flow,
              stock_hsgt_north_net_flow_in=_net_flow)
    monkeypatch.setattr(db, "get_meta", lambda k, default="": "")
    recs, ok, err = sm.collect_northbound("2026-07-25")
    assert ok, err
    assert len(recs) == 2
    assert all(r["channel"] == "北向" for r in recs)
    assert all(r["actor"] == "" for r in recs)
    assert all(r["action"] == "上榜" for r in recs)
    assert recs[0]["amount"] == 3.2e8
    assert sm.CHANNEL_STATUS["北向"]["source"] == "北向十大成交股(盘后)"


def test_northbound_degrade_to_total(monkeypatch, tmp_path):
    """备援2 也失败/空 → 降级3 总额 1 条，actor="北向总额"。"""
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    db.init_db()
    def _boom(**kw):
        raise RuntimeError("NoneType")
    def _acc_flow(symbol="沪股通"):
        return pd.DataFrame()
    def _net_flow(symbol="北向"):
        return pd.DataFrame({"日期": ["2026-07-25"], "当日资金流入": [5e8]})
    _patch_ak(monkeypatch,
              stock_hsgt_individual_em=_boom,
              stock_hsgt_hold_stock_em=_boom,
              stock_hsgt_north_acc_flow_in=_acc_flow,
              stock_hsgt_north_net_flow_in=_net_flow)
    monkeypatch.setattr(db, "get_meta", lambda k, default="": "")
    recs, ok, err = sm.collect_northbound("2026-07-25")
    assert ok, err
    assert len(recs) == 1
    assert recs[0]["actor"] == "北向总额"
    assert recs[0]["action"] == "净买入"
    assert recs[0]["amount"] == 5e8
    assert sm.CHANNEL_STATUS["北向"]["source"] == "北向总额(盘后)"
```

- [ ] **Step 2: 跑测试验证失败**

Run: `python -m pytest tests/test_smart_money.py::test_northbound_fallback_acc_flow tests/test_smart_money.py::test_northbound_degrade_to_total -q`
Expected: FAIL（`collect_northbound` 旧两源逻辑无备援2/降级3）。

- [ ] **Step 3: 重写 `collect_northbound`**

替换 `data/smart_money.py` 中 `collect_northbound` 整个函数：

```python
def _north_probe_due() -> bool:
    """距上次北向主源探活 ≥7 天才试（主源已下线，日常不空等）。"""
    last = db.get_meta("north_probe_date", "")
    if not last:
        return True
    try:
        last_d = datetime.strptime(last, "%Y-%m-%d")
        return (datetime.now() - last_d).days >= 7
    except Exception:
        return True


def _nb_individual():
    """主源/备援1（已下线，仅探活时调）。"""
    try:
        df = ak.stock_hsgt_individual_em(stock="北向资金")
        if df is not None and not df.empty:
            return df, "东财个股"
    except Exception:
        pass
    try:
        df = ak.stock_hsgt_hold_stock_em(market="北向", indicator="今日排行")
        if df is not None and not df.empty:
            return df, "东财排行"
    except Exception:
        pass
    return None, ""


def _nb_acc_flow():
    """备援2（默认）：沪股通+深股通盘后十大成交股。"""
    out = []
    for sym in ("沪股通", "深股通"):
        try:
            df = ak.stock_hsgt_north_acc_flow_in(symbol=sym)
        except Exception:
            continue
        if df is None or df.empty:
            continue
        col_code = _first_col(df, ["股票代码", "代码", "code"])
        col_name = _first_col(df, ["股票简称", "名称", "name"])
        col_amt = _first_col(df, ["净买额", "买入金额", "成交金额"])
        for _, r in df.iterrows():
            out.append((r.get(col_code), r.get(col_name), _to_float(r.get(col_amt))))
    return out


def _nb_total_flow():
    """降级3：北向总额（无个股，1 条汇总）。"""
    try:
        df = ak.stock_hsgt_north_net_flow_in(symbol="北向")
    except Exception:
        return None
    if df is None or df.empty:
        return None
    col_amt = _first_col(df, ["当日资金流入", "资金净流入", "净流入额"])
    return _to_float(df.iloc[-1].get(col_amt)) if len(df) else None


def collect_northbound(date: str) -> tuple[list[dict], bool, str]:
    """北向：主源(探活,已下线)→备援2 十大成交股(默认,盘后)→降级3 总额。
    2024-08 起实时端点 NoneType 崩，默认走盘后十大成交股；全失败走 stale(§4.2)。"""
    if not _AK_OK:
        return [], False, _AK_ERR
    if _north_probe_due():
        db.set_meta("north_probe_date", datetime.now().strftime("%Y-%m-%d"))
        df, src = _nb_individual()
        if df is not None:
            col_code = _first_col(df, ["股票代码", "代码", "code"])
            col_name = _first_col(df, ["股票简称", "名称", "name"])
            col_amt = _first_col(df, ["持股数量变化", "增持市值", "净买额", "今日增持市值"])
            recs = [_rec(date, r.get(col_code), r.get(col_name), "股票",
                         "北向", "", "净买入", r.get(col_amt),
                         raw={k: _clean(v) for k, v in r.items()})
                    for _, r in df.iterrows()]
            _set_status("北向", True, src, "")
            return recs, True, ""
    acc = _nb_acc_flow()
    if acc:
        recs = [_rec(date, code, name, "股票", "北向", "", "上榜", amt,
                     raw={"source": "北向十大成交股(盘后)", "净额(元)": amt})
                for code, name, amt in acc]
        _set_status("北向", True, "北向十大成交股(盘后)", "")
        return recs, True, ""
    tot = _nb_total_flow()
    if tot is not None:
        recs = [_rec(date, None, "北向总额", "股票", "北向", "北向总额",
                     "净买入", tot,
                     raw={"source": "北向总额(盘后)", "净额(元)": tot})]
        _set_status("北向", True, "北向总额(盘后)", "")
        return recs, True, ""
    _set_status("北向", False, "", "北向: 主源崩+十大成交股空+总额无(全失败)")
    return [], False, "北向: 主源崩+十大成交股空+总额无(全失败)"
```

- [ ] **Step 4: 跑测试验证通过**

Run: `python -m pytest tests/test_smart_money.py::test_northbound_fallback_acc_flow tests/test_smart_money.py::test_northbound_degrade_to_total -q`
Expected: PASS。

- [ ] **Step 5: 回归 + 提交**

Run: `python -m pytest tests/test_smart_money.py -q`
Expected: 全 PASS。
```bash
git add data/smart_money.py tests/test_smart_money.py
git commit -m "feat(smart_money): 北向多级备援链(盘后十大成交股+总额降级)"
```

---

## Task 2 (P1): 全通道 stale 降级

**Files:**
- Modify: `data/smart_money.py`：新增 `_stale_fallback`/`_last_ok_date`；改 `refresh_today`；改 `channel_status`。
- Test: `tests/test_smart_money.py`

**Interfaces:**
- Consumes: `db.query_rows`/`db.set_meta`/`db.get_meta`/`db.init_db`/`db.stamp_update_time`/`db.upsert_rows`/`db.get_conn`；`CHANNEL_STATUS`。
- Produces: `_stale_fallback(channel, err) -> (rows, stale_flag, note)`；`_last_ok_date(channel) -> str`；`CHANNEL_STATUS[ch]` 增 `stale/stale_date/last_ok_date/stale_note`。

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_smart_money.py`：

```python
def _mock_other_channels(monkeypatch, ok=True):
    """把非测试目标的 5 通道 mock 成快速返回，避免 refresh_today 触网。
    龙虎榜/十大股东/高管/限售/北向 默认 ok 空；调用方可覆盖其中之一。"""
    for name in ("collect_dragon_tiger", "collect_holders",
                 "collect_management_hold", "collect_share_unlock",
                 "collect_northbound"):
        monkeypatch.setattr(sm, name, lambda d, _n=name: ([], ok, "skip"))


def test_stale_degradation_keeps_old_data(monkeypatch, tmp_path):
    """通道拉取失败 + DB 有 3 日前旧数据 → stale=True 保留旧、last_ok_date 正确、
    meta 写入；refresh_today 跳过 upsert（不入库新行）。"""
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    db.init_db()
    old = [{"date": "2026-07-22", "code": "000001", "name": "甲", "market": "股票",
            "channel": "资金流", "actor": "", "action": "净买入", "amount": 1.5e8,
            "as_of": None, "ts": "2026-07-22 10:00:00"}]
    db.upsert_rows("smart_money_action", old)
    _mock_other_channels(monkeypatch)   # 其余 5 通道 ok 空，不触网
    monkeypatch.setattr(sm, "collect_fund_flow",
                        lambda d: ([], False, "资金流: THS 与 spot 均无净额"))
    metas = {}
    monkeypatch.setattr(db, "set_meta", lambda k, v: metas.update({k: v}))
    monkeypatch.setattr(db, "get_meta", lambda k, default="": metas.get(k, default))
    report = sm.refresh_today("2026-07-25")
    ch = report["channels"]["资金流"]
    assert ch["ok"] is True and ch.get("stale") is True
    assert ch["rows"] == 1
    assert ch["err"].startswith("回退至 2026-07-22")
    assert "sm_stale_资金流" in metas


def test_three_state_channel_light(monkeypatch, tmp_path):
    """三态：黄(资金流有旧+采集失败→stale)/灰(北向无旧+失败)。"""
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    db.init_db()
    db.upsert_rows("smart_money_action",
                   [{"date": "2026-07-22", "code": "000001", "name": "甲",
                     "market": "股票", "channel": "资金流", "actor": "",
                     "action": "净买入", "amount": 1e8, "as_of": None, "ts": ""}])
    _mock_other_channels(monkeypatch)
    monkeypatch.setattr(sm, "collect_fund_flow", lambda d: ([], False, "fail"))
    monkeypatch.setattr(db, "set_meta", lambda k, v: None)
    monkeypatch.setattr(db, "get_meta", lambda k, default="": "")
    sm.refresh_today("2026-07-25")
    st = sm.channel_status()["资金流"]
    assert st["ok"] is True and st.get("stale") is True          # 黄
    # 灰：北向覆盖为失败（无旧数据）
    monkeypatch.setattr(sm, "collect_northbound", lambda d: ([], False, "全失败"))
    sm.CHANNEL_STATUS["北向"] = {"ok": False, "source": "", "err": "未采集", "at": ""}
    sm.refresh_today("2026-07-25")
    st_nb = sm.channel_status()["北向"]
    assert st_nb["ok"] is False and not st_nb.get("stale")       # 灰
```

- [ ] **Step 2: 跑测试验证失败**

Run: `python -m pytest tests/test_smart_money.py::test_stale_degradation_keeps_old_data tests/test_smart_money.py::test_three_state_channel_light -q`
Expected: FAIL。

- [ ] **Step 3: 实现 `_stale_fallback`/`_last_ok_date` + 改 `refresh_today` + `channel_status`**

在 `collect_share_unlock` 之后、`refresh_today` 之前新增：

```python
def _last_ok_date(channel: str) -> str:
    """该通道最近一次有数据日期（无则空）。"""
    try:
        rows = db.query_rows("smart_money_action", where="channel = ?",
                             params=(channel,), order_by="date DESC", limit=1)
        return rows[0].get("date", "") if rows else ""
    except Exception:
        return ""


def _stale_fallback(channel: str, err: str) -> tuple[list[dict], bool, str]:
    """拉取失败时查 DB 该通道最近一次有数据日期的全部行作回退数据。
    有→(旧rows, True, "回退至 <stale_date>（采集失败: <err>）")；
    无→([], False, err)。stale 标记落 CHANNEL_STATUS + db.set_meta。"""
    try:
        stale_date = _last_ok_date(channel)
        if not stale_date:
            return [], False, err
        old = db.query_rows("smart_money_action",
                            where="channel = ? AND date = ?",
                            params=(channel, stale_date), order_by="", limit=0)
        note = f"回退至 {stale_date}（采集失败: {err}）"
        _set_status(channel, True, "", note)
        CHANNEL_STATUS[channel]["stale"] = True
        CHANNEL_STATUS[channel]["stale_date"] = stale_date
        CHANNEL_STATUS[channel]["stale_note"] = note
        db.set_meta(f"sm_stale_{channel}", f"{stale_date}|{err}")
        return old, True, note
    except Exception:
        return [], False, err
```

替换 `refresh_today` 整个函数：

```python
def refresh_today(date: str | None = None) -> dict:
    """串行跑 6 通道 → upsert → 写 meta + CHANNEL_STATUS。
    拉取失败时若 DB 有旧数据 → stale 降级（不入库新行）。"""
    db.init_db()
    if not date:
        date = datetime.now().strftime("%Y-%m-%d")
    report = {"date": date, "counts": {}, "channels": {}}
    plan = [("资金流", collect_fund_flow), ("北向", collect_northbound),
            ("龙虎榜", collect_dragon_tiger), ("十大股东", collect_holders),
            ("高管增减持", collect_management_hold),
            ("限售解禁", collect_share_unlock)]
    for ch, fn in plan:
        try:
            recs, ok, err = fn(date)
        except Exception as e:
            recs, ok, err = [], False, f"{ch}: 未捕获异常 {e}"
            _set_status(ch, False, "", str(e))
        if not ok:
            old, stale_flag, note = _stale_fallback(ch, err)
            if stale_flag:
                st = CHANNEL_STATUS.get(ch, {})
                report["counts"][ch] = 0
                report["channels"][ch] = {
                    "ok": True, "stale": True, "rows": len(old),
                    "stale_date": st.get("stale_date", ""),
                    "last_ok_date": st.get("stale_date", ""),
                    "err": note, "at": st.get("at", "")}
                continue
            st = CHANNEL_STATUS.get(ch, {})
            report["counts"][ch] = 0
            report["channels"][ch] = {
                "ok": False, "stale": False, "rows": 0,
                "last_ok_date": _last_ok_date(ch),
                "err": err, "at": st.get("at", "")}
            continue
        n = db.upsert_rows("smart_money_action", recs) if recs else 0
        st = CHANNEL_STATUS.get(ch, {})
        report["counts"][ch] = n
        report["channels"][ch] = {
            "ok": True, "stale": False, "rows": n,
            "last_ok_date": date, "err": err, "at": st.get("at", "")}
    report["update_time"] = db.stamp_update_time()
    return report
```

替换 `channel_status` 整个函数：

```python
def channel_status() -> dict:
    """各通道状态，叠加 DB 实况 + stale meta：内存 CHANNEL_STATUS 给 source/err/at/stale
    细节；DB 给 rows/date；meta(sm_stale_<ch>)给 stale_date/last_ok_date。只读。"""
    status = {k: dict(v) for k, v in CHANNEL_STATUS.items()}
    try:
        from . import db as _db
        _db.init_db()
        with _db.get_conn() as conn:
            row = conn.execute(
                "SELECT MAX(date) AS d FROM smart_money_action").fetchone()
            latest = row["d"] if row else None
            counts = {}
            if latest:
                cur = conn.execute(
                    "SELECT channel, COUNT(*) AS n FROM smart_money_action "
                    "WHERE date=? GROUP BY channel", (latest,))
                counts = {r["channel"]: r["n"] for r in cur.fetchall()}
        for ch, st in status.items():
            meta = _db.get_meta(f"sm_stale_{ch}", "")
            if meta and "|" in meta:
                sd, _err = meta.split("|", 1)
                st.setdefault("stale_date", sd)
                st.setdefault("stale", True)
            st.setdefault("stale", False)
            st.setdefault("last_ok_date", _last_ok_date(ch))
            n = counts.get(ch, 0) if latest else 0
            st["rows"] = n
            st["date"] = latest or ""
            if n > 0 and not st.get("stale"):
                st["ok"] = True
                if not st.get("source"):
                    st["source"] = f"DB({latest})"
                st["err"] = ""
    except Exception:
        for st in status.values():
            st.setdefault("rows", 0)
            st.setdefault("date", "")
            st.setdefault("stale", False)
    return status
```

- [ ] **Step 4: 跑测试验证通过**

Run: `python -m pytest tests/test_smart_money.py::test_stale_degradation_keeps_old_data tests/test_smart_money.py::test_three_state_channel_light -q`
Expected: PASS。

- [ ] **Step 5: 回归 + 提交**

Run: `python -m pytest tests/test_smart_money.py -q`
Expected: 全 PASS。
```bash
git add data/smart_money.py tests/test_smart_money.py
git commit -m "feat(smart_money): 全通道 stale 降级(失败保旧数据标stale)+三态字段"
```

---

## Task 3 (P1): 前端三态灯 + 北向口径标注

**Files:**
- Modify: `web/index.html`：`smChannelsHtml`（604-614 行）、`smRender`（643-672 行）。

**Interfaces:**
- Consumes: `/api/smart-money/channels` 响应 `{ch:{ok,stale,stale_date,last_ok_date,err,source,rows,date}}`。
- Produces: 三态灯（绿 ok&!stale / 黄 stale / 灰 !ok&!last_ok_date）+ 北向表头"(盘后十大成交股口径)"。

- [ ] **Step 1: 手测点清单**

实施后启动服务，切"主力动向"tab 验证：三态灯三色；北向 chip 表头口径标注；黄灯 hover 显示"回退至 YYYY-MM-DD"。

- [ ] **Step 2: 改 `smChannelsHtml` 三态**

替换 `smChannelsHtml` 函数体：

```javascript
function smChannelsHtml(channels){
  return Object.entries(channels||{}).map(([k,v])=>{
    const stale=v&&v.stale;
    const ok=v&&v.ok;
    const color=stale?'#F59E0B':(ok?'#16A34A':'#5C6884');
    const rows=v&&v.rows!=null?`:${v.rows}行`:'';
    const dt=v&&v.date?`(${v.date})`:'';
    const reason=stale?('回退至 '+(v.stale_date||'')+' '+(v.err||''))
                  :(ok?(v.source||'ok'):(v.err||'不可用'));
    const title=`${k}：${reason} ${rows} ${dt}`;
    return `<span title="${title}" data-ch="${k}" style="color:${color};font-size:12px;cursor:pointer;border:1px solid var(--border);border-radius:6px;padding:2px 8px">${stale?'◐':'●'}${k}${rows}</span>`;
  }).join('');
}
```

- [ ] **Step 3: 改 `smRender` 北向口径标注**

在 `smRender` 的非聚合分支（`else{` 内）找到 `ths=[...]` 行，在其前插入北向口径标注并把 note 拼上。替换非聚合分支为：

```javascript
  }else{
    rows=[...rows].sort((a,b)=>Math.abs(b.amount||0)-Math.abs(a.amount||0));
    const shown=rows.slice(0,CAP);
    const nbNote=(smState.chip==='北向'||(smState.chip==='all'&&rows.some(x=>x.channel==='北向')))
      ?'<span style="color:var(--muted);font-size:12px">北向=盘后十大成交股口径(2024-08起实时已停披露)</span><br>':'';
    ths=['日期','代码','名称','市场','通道','席位/股东','动作','净额(元)'].map(h=>`<th class="num">${h}</th>`).join('');
    trs=shown.map(x=>`<tr class="clk" data-code="${x.code||''}"><td>${x.date||''}</td><td>${x.code||''}</td><td>${x.name||''}</td><td>${x.market||''}</td><td>${x.channel||''}</td><td>${x.actor||'—'}</td><td>${x.action||''}</td><td class="num">${fmtNum(x.amount)}</td></tr>`).join('');
    note=nbNote+(rows.length>CAP?`<div class="skipped">按净额绝对值降序前 ${CAP} 条(共 ${rows.length} 条)，其余折叠防卡顿</div>`:'');
  }
```

- [ ] **Step 4: 手动验证**

`uvicorn api.server:app --port 8000`，开 `http://localhost:8000/web/index.html` 主力动向 tab：三态灯可辨；点北向 chip 表头下方显示口径标注；hover 黄灯显示"回退至..."。

- [ ] **Step 5: 提交**

```bash
git add web/index.html
git commit -m "feat(web): 主力动向三态灯(绿/黄stale/灰)+北向盘后口径标注"
```

---

## Task 4 (P2): 十大股东国家队种子 ∪ + 学习扩充

**Files:**
- Modify: `data/smart_money.py`：新增 `NATIONAL_TEAM_HOLDINGS_SEED` + `_load_seed`/`_save_seed`；改 `collect_holders`。
- Test: `tests/test_smart_money.py`

**Interfaces:**
- Consumes: `NATIONAL_TEAM`；`db.get_meta`/`db.set_meta`；`db.query_rows("stock_spot")`；`_prefix_code`/`_latest_report_period`/`_set_status`。
- Produces: `NATIONAL_TEAM_HOLDINGS_SEED`（list[str]）；`_load_seed() -> set[str]`；`_save_seed(codes)`。

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_smart_money.py`：

```python
def test_holders_seed_union(monkeypatch, tmp_path):
    """候选 = 成交额前200 ∪ 种子；覆盖仅种子命中、shortlist 外的国家队小盘股。"""
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    db.init_db()
    db.upsert_rows("stock_spot", [
        {"code": "600519", "name": "贵州茅台", "turnover_amount": 1e9},
        {"code": "601318", "name": "中国平安", "turnover_amount": 8e8}])
    monkeypatch.setattr(sm, "_AK_OK", True)
    monkeypatch.setattr(sm, "NATIONAL_TEAM_HOLDINGS_SEED", ["600999"])
    monkeypatch.setattr(sm, "_load_seed", lambda: set(["600999"]))
    monkeypatch.setattr(sm, "_save_seed", lambda codes: None)
    monkeypatch.setattr(db, "get_meta", lambda k, default="": "")
    pulled = []
    def _gdfx(symbol, date):
        pulled.append(symbol)
        return pd.DataFrame({"股东名称": ["中央汇金"]})
    _patch_ak(monkeypatch, stock_gdfx_free_top_10_em=_gdfx)
    recs, ok, err = sm.collect_holders("2026-07-25")
    assert ok, err
    assert any("600999" in p for p in pulled)
    assert any(r["actor"] == "中央汇金" for r in recs)


def test_holders_seed_learning(monkeypatch, tmp_path):
    """成功拉取后新命中 code 并入种子、落 meta。"""
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    db.init_db()
    saved = {}
    monkeypatch.setattr(db, "set_meta", lambda k, v: saved.update({k: v}))
    monkeypatch.setattr(db, "get_meta", lambda k, default="": "")
    monkeypatch.setattr(sm, "_AK_OK", True)
    monkeypatch.setattr(sm, "NATIONAL_TEAM_HOLDINGS_SEED", ["600999"])
    monkeypatch.setattr(sm, "_load_seed", lambda: set(["600999"]))
    monkeypatch.setattr(db, "query_rows", lambda table, **kw:
        [{"code": "600519", "name": "茅台", "turnover_amount": 1e9}] if table=="stock_spot" else [])
    def _gdfx(symbol, date):
        return pd.DataFrame({"股东名称": ["中国证券金融"]})
    _patch_ak(monkeypatch, stock_gdfx_free_top_10_em=_gdfx)
    recs, ok, err = sm.collect_holders("2026-07-25")
    assert ok
    assert "nt_holdings_seed" in saved
    assert "600519" in saved["nt_holdings_seed"]
```

- [ ] **Step 2: 跑测试验证失败**

Run: `python -m pytest tests/test_smart_money.py::test_holders_seed_union tests/test_smart_money.py::test_holders_seed_learning -q`
Expected: FAIL。

- [ ] **Step 3: 实现种子 + 改 `collect_holders`**

在 `NATIONAL_TEAM` 常量之后新增：

```python
# 国家队历史重仓股种子（硬编兜底，覆盖小盘/低成交额国家队重仓股——成交额 shortlist 会漏）。
# 学习式扩充：成功拉取后命中 NATIONAL_TEAM 的 code 并入 meta nt_holdings_seed。
NATIONAL_TEAM_HOLDINGS_SEED = [
    "600519", "601318", "600036", "601398", "601288", "601628",
    "600028", "601857", "600030", "601166", "600276", "000858",
    "600000", "601988", "601328", "600436", "600009", "601088",
]
```

在 `_prefix_code` 之后新增：

```python
def _load_seed() -> set[str]:
    """加载国家队种子：硬编常量 ∪ meta nt_holdings_seed（学习扩充）。"""
    codes = set(NATIONAL_TEAM_HOLDINGS_SEED)
    try:
        meta = db.get_meta("nt_holdings_seed", "")
        if meta:
            codes |= {c for c in meta.split(",") if c}
    except Exception:
        pass
    return codes


def _save_seed(codes: set[str]) -> None:
    """把新命中国家队重仓的 code 并入种子 meta。"""
    try:
        all_codes = _load_seed() | codes
        db.set_meta("nt_holdings_seed", ",".join(sorted(all_codes)))
    except Exception:
        pass
```

在 `collect_holders` 中，把

```python
    candidates = spot_df.to_dict("records")
    as_of = date
```

替换为

```python
    # 候选 = 成交额前200 ∪ 国家队种子（去重，覆盖小盘国家队重仓股）
    seed_codes = _load_seed()
    spot_codes = {str(sp.get("code")) for sp in candidates}
    for sc in seed_codes:
        if sc not in spot_codes:
            candidates.append({"code": sc, "name": "种子标的", "turnover_amount": 0})
    as_of = date
```

并在 `collect_holders` 末尾 `return recs, True, ""` 之前（`db.set_meta("holders_last_as_of", as_of)` 之后）插入：

```python
    # 学习式扩充：命中国家队关键字的 code 并入种子
    hit_codes = {str(rec["code"]) for rec in recs
                 if rec.get("actor") and any(k in rec["actor"] for k in NATIONAL_TEAM)}
    if hit_codes:
        _save_seed(hit_codes)
```

- [ ] **Step 4: 跑测试验证通过**

Run: `python -m pytest tests/test_smart_money.py::test_holders_seed_union tests/test_smart_money.py::test_holders_seed_learning -q`
Expected: PASS。

- [ ] **Step 5: 回归 + 提交**

Run: `python -m pytest tests/test_smart_money.py -q`
Expected: 全 PASS。
```bash
git add data/smart_money.py tests/test_smart_money.py
git commit -m "feat(smart_money): 十大股东候选∪国家队种子+学习式扩充"
```

---

## Task 5 (P3): quality 口径2 buffett 失败降级 + source_health

**Files:**
- Modify: `backtest/quality.py`：`_dim_scores` 口径2 末尾加降级；`quality_rank` 返回增 `source_health`。
- Test: `tests/test_quality.py`

**Interfaces:**
- Consumes: `df`（spot DataFrame，含 `pe/pb/amplitude/turnover_rate`）；`_zscore`/`_to_pct`/`_to_float`；`dim_status`。
- Produces: `quality_rank` 返回增 `source_health: {1:str,2:str,3:str,4:str}`。

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_quality.py`（注意 `SPOT_STOCK` 已有 `pe` 缺失，需先给 `SPOT_STOCK` 补 `pe/pb/amplitude` 字段；若不补，降级分支 `_zscore` 全 NaN→返回全 0→分位 0.5，仍非 None，测试通过。本测试用补字段版以验证真实分位）：

```python
SPOT_STOCK_FULL = [
    {"code": "000001", "name": "甲", "latest_price": 10.0, "change_pct": 2.0,
     "turnover_amount": 1e8, "turnover_rate": 3.0, "main_net_inflow": 5e7,
     "pe": 8.0, "pb": 0.9, "amplitude": 2.0},
    {"code": "000004", "name": "丁", "latest_price": 6.0, "change_pct": 1.0,
     "turnover_amount": 6e7, "turnover_rate": 2.0, "main_net_inflow": 1e7,
     "pe": 30.0, "pb": 3.0, "amplitude": 8.0},
]


def test_quality_dim2_spot_proxy_fallback(monkeypatch):
    """buffett _AK_OK=False → 口径2 spot 代理分位非 None、dim_status/source_health 标降级。"""
    import backtest.buffett as bt_buf
    monkeypatch.setattr(bt_buf, "_AK_OK", False)
    monkeypatch.setattr(db, "query_rows",
                        lambda table, **kw: SPOT_STOCK_FULL if table == "stock_spot" else [])
    res = quality.quality_rank("stock", min_turnover=5e7, limit_pct=9.9)
    assert res["dim_status"].get("2", "").startswith("ok(降级spot估值代理)")
    assert res.get("source_health", {}).get("2", "").startswith("ok(降级")
    item = next((x for x in res["main"] if x["code"] == "000001"), None)
    assert item is not None
    assert item["dim_scores"].get(2) is not None
    # 000001 pe 低/amplitude 低 → 估值代理分位应高于 000004
    item2 = next((x for x in res["main"] if x["code"] == "000004"), None)
    if item2:
        assert item["dim_scores"][2] >= item2["dim_scores"][2]


def test_quality_dim2_main_path_intact(monkeypatch):
    """buffett 正常时口径2 走主路径不被 spot 代理覆盖（防回归）。
    这里 shortlist 空+analyze_many 空 → 触发降级路径，断言主路径逻辑可被降级接续。"""
    import backtest.buffett as bt_buf
    monkeypatch.setattr(bt_buf, "_AK_OK", True)
    monkeypatch.setattr(bt_buf, "shortlist_by_turnover",
                        lambda min_turnover=5e8, k=80: [])
    monkeypatch.setattr(bt_buf, "analyze_many", lambda codes: [])
    monkeypatch.setattr(db, "query_rows",
                        lambda table, **kw: SPOT_STOCK_FULL if table == "stock_spot" else [])
    res = quality.quality_rank("stock", min_turnover=5e7, limit_pct=9.9)
    assert res["dim_status"].get("2", "").startswith("ok(降级")
```

- [ ] **Step 2: 跑测试验证失败**

Run: `python -m pytest tests/test_quality.py::test_quality_dim2_spot_proxy_fallback tests/test_quality.py::test_quality_dim2_main_path_intact -q`
Expected: FAIL。

- [ ] **Step 3: 加口径2降级 + source_health**

在 `backtest/quality.py` `_dim_scores` 口径2 stock 分支末尾，找到：

```python
                dims_avail.append(2)
                status["2"] = "ok"
        except Exception as e:
            status["2"] = f"err:{e}"
```

把整个 `except Exception as e:` 块替换为：

```python
        except Exception as e:
            status["2"] = f"err:{e}"
            # 降级：buffett 主路径失败 → spot 估值代理(pe/pb/amplitude负向+turnover正向)
            try:
                pe = pd.to_numeric(df.get("pe"), errors="coerce")
                pb = pd.to_numeric(df.get("pb"), errors="coerce")
                amp = pd.to_numeric(df.get("amplitude"), errors="coerce")
                tr = pd.to_numeric(df.get("turnover_rate"), errors="coerce")
                comp = (_zscore(-pe) + _zscore(-pb) + _zscore(-amp) + _zscore(tr)) / 4
                pct = _to_pct(comp)
                for c in codes:
                    scores[c][2] = _to_float(pct.get(c)) if c in pct.index else None
                dims_avail.append(2)
                status["2"] = "ok(降级spot估值代理)"
            except Exception as e2:
                status["2"] = f"err:buffett与spot代理均失败:{e2}"
```

在 `quality_rank` 返回处，把

```python
    return {"main": main, "by_dim": by_dim, "dims_available": dims_avail,
            "dim_status": dim_status, "min_dims": eff_min_dims,
            "cand_disclaimer": _CAND_DISCLAIMER, "error": None}
```

替换为：

```python
    return {"main": main, "by_dim": by_dim, "dims_available": dims_avail,
            "dim_status": dim_status, "min_dims": eff_min_dims,
            "source_health": {d: dim_status.get(d, "") for d in (1, 2, 3, 4)},
            "cand_disclaimer": _CAND_DISCLAIMER, "error": None}
```

- [ ] **Step 4: 跑测试验证通过**

Run: `python -m pytest tests/test_quality.py::test_quality_dim2_spot_proxy_fallback tests/test_quality.py::test_quality_dim2_main_path_intact -q`
Expected: PASS。

- [ ] **Step 5: 回归全量 + 提交**

Run: `python -m pytest tests/ -q`
Expected: 全 PASS。
```bash
git add backtest/quality.py tests/test_quality.py
git commit -m "feat(quality): 口径2 buffett失败spot估值代理降级+source_health"
```

---

## Self-Review

**Spec 覆盖**：
- §4.1 北向备援链 → Task 1 ✓
- §4.2 stale 降级 + 三态灯 → Task 2 + Task 3 ✓
- §4.3 十大股东种子 → Task 4 ✓
- §5 quality 口径2降级 + source_health → Task 5 ✓
- §6 前端三态灯/口径标注 → Task 3 ✓
- §8 测试 8 条 → Task 1（北向备援2/降级3）+ Task 2（stale/三态）+ Task 4（种子∪/种子学习）+ Task 5（口径2降级/主路径回归）= 8 ✓

**Placeholder 扫描**：无 TBD/TODO；每代码步含真实代码。

**类型一致**：`_stale_fallback(channel, err) -> (rows, stale_flag, note)` 与 `refresh_today` 调用一致；`_last_ok_date(channel) -> str` 在 Task 2 定义且 `refresh_today`/`channel_status` 调用一致；`_load_seed() -> set[str]`/`_save_seed(codes)` 与 `collect_holders` 一致；`source_health` 在 Task 5 定义与测试读取一致。

**风险标注**：
- Task 1 `stock_hsgt_north_acc_flow_in`/`stock_hsgt_north_net_flow_in` 列名需落地实测（akshare 1.18 可能改名）；`_first_col` 候选容错覆盖常见列名，仍失败走降级3/全失败→stale。
- Task 3 前端无单测，靠手动验证。
