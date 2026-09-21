# -*- coding: utf-8 -*-
"""sector_heat 行业景气加成层测试。纯 mock 三源(板块资金流/行业板手脚)不触网。"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pandas as pd
import pytest
from screener import sector_heat as sh


def test_policy_hit():
    b = sh.board_heat  # 触发模块 import
    # 校准后 POLICY_THEMES 板块名对齐 industry_board 真实行业板块名(反查得到个股)
    assert sh.policy_hit("机器人") == 0.05
    assert sh.policy_hit("半导体") == 0.05
    assert sh.policy_hit("白酒") == 0.0
    assert sh.policy_hit("智能驾驶") == 0.0   # 风格名已剔除(industry_board 无此行业板块)
    # 2026-09-20 新增主线：城市更新(基建) / 养老大健康(医药)
    assert sh.policy_hit("水泥") == 0.05
    assert sh.policy_hit("化学制药") == 0.05
    assert sh.policy_hit("") == 0.0


def test_rank_pct_order():
    s = pd.Series({"a": 1.0, "b": 3.0, "c": 2.0})
    r = sh.rank_pct(s)
    assert r["b"] > r["c"] > r["a"]
    assert float(r["b"]) == 1.0 and float(r["a"]) == 0.0
    assert sh.rank_pct(pd.Series(dtype=float)).empty
    assert float(sh.rank_pct(pd.Series({"z": 5.0}))["z"]) == 0.5


def test_board_heat_weights():
    """0.6*资金分位 + 0.4*上涨宽度分位；width 用 up/(up+down)。"""
    ff = [
        {"name": "机器人", "main_net_inflow": 3e8},
        {"name": "白酒", "main_net_inflow": -1e8},
        {"name": "医药", "main_net_inflow": 1e8},
    ]
    br = [
        {"name": "机器人", "up_count": 60, "down_count": 40, "constituent_count": 100},
        {"name": "白酒", "up_count": 5, "down_count": 95, "constituent_count": 100},
        {"name": "医药", "up_count": 30, "down_count": 70, "constituent_count": 100},
    ]
    h = sh.board_heat(ff, br)
    # 机器人 资金最高(1.0)+宽度0.6=(1.0) → heat 最高；白酒最低
    assert h["机器人"] == max(h.values())
    assert h["白酒"] == min(h.values())
    for v in h.values():
        assert 0.0 <= v <= 1.0


def test_pick_board_highest_heat():
    mm = {"机器人": {"a", "b"}, "医药": {"a"}}
    heat = {"机器人": 0.8, "医药": 0.4}
    assert sh.pick_board("a", mm, heat) == "机器人"  # 多命中取 heat 最高
    assert sh.pick_board("c", mm, heat) is None


def test_attach_sector_heat():
    """attach 原地加 sector_heat/policy_hit；未命中板块→sector_heat=None。"""
    rows = [{"code": "a"}, {"code": "c"}]
    ff = [{"name": "机器人", "main_net_inflow": 3e8}]
    br = [{"name": "机器人", "up_count": 60, "down_count": 40}]
    mm = {"机器人": {"a"}}
    sh.attach_sector_heat(rows, ff, br, mm)
    r0 = rows[0]
    assert r0["policy_hit"] == 0.05            # 机器人 命中政策
    assert r0["sector_heat"] is not None and 0.0 <= r0["sector_heat"] <= 1.0
    assert isinstance(r0.get("policy_event"), list)  # 顺带附事件催化标注(列表,可空)
    assert rows[1]["policy_hit"] == 0.0
    assert rows[1]["sector_heat"] is None      # 无板块 → None


def test_unmatched_themes_reports_missing_boards():
    """诊断函数：喂数据源真实行业板块名集合，报告主题里配置但缺失(反查不到个股)的板块名。"""
    avail = {"机器人", "专用设备", "半导体"}
    miss = sh.unmatched_themes(avail)
    # 机器人/专用设备 在 avail → 先进制造 主题不报缺失；但"通用设备"等不在 → 报
    assert "机器人" not in {x for v in miss.values() for x in v}
    assert "通用设备" in miss.get("先进制造", [])
    # 全命中 → 每主题缺失列表为空
    assert all(not v for v in sh.unmatched_themes(set(sh._HIT_BOARDS)).values())


def test_quality_refine_applies_sector_heat():
    """精排加成：res 打平时 heat/policy 破平(高景气+政策方列首)，item 附字段。"""
    from backtest import quality
    pool = [
        {"code": "a", "resonance": 0.6},  # 机器人(heat最高)+政策 → 应列首
        {"code": "b", "resonance": 0.6},  # 白酒(heat最低)
        {"code": "c", "resonance": 0.0},  # 医药(heat中)
    ]
    ff = [{"name": "机器人", "main_net_inflow": 3e8},
          {"name": "白酒", "main_net_inflow": -1e8},
          {"name": "医药", "main_net_inflow": 1e8}]
    br = [{"name": "机器人", "up_count": 60, "down_count": 40},
          {"name": "白酒", "up_count": 5, "down_count": 95},
          {"name": "医药", "up_count": 30, "down_count": 70}]
    mm = {"机器人": {"a"}, "白酒": {"b"}, "医药": {"c"}}
    out = quality._apply_sector_heat(pool, ff, br, mm, in_session=True)
    assert out[0]["code"] == "a", f"景气+政策应列首: {[x['code'] for x in out]}"
    merged = {x["code"]: x for x in out}
    assert merged["a"]["sector_heat"] == 1.0
    assert merged["a"]["policy_hit"] == 0.05
    assert merged["b"]["policy_hit"] == 0.0
    # 盘后：A 流动性失效，仅 res+heat+policy，仍保持 a 首
    out2 = quality._apply_sector_heat(pool, ff, br, mm, in_session=False)
    assert out2[0]["code"] == "a"


def test_top_by_amount_annotates_sector(monkeypatch):
    """主力池经 _sector_ctx 标注管线：高景气+政策行附 sector_heat/policy_hit。"""
    from screener import smart_money as sm
    import data.db as db

    rows = [{"code": "a", "name": "甲", "market": "sh", "amount": 1e7, "count": 1},
            {"code": "z", "name": "乙", "market": "sz", "amount": 2e7, "count": 1}]
    ff = [{"name": "机器人", "main_net_inflow": 3e8}]
    br = [{"name": "机器人", "up_count": 60, "down_count": 40}]
    mm = {"机器人": {"a"}}

    monkeypatch.setattr(db, "query_rows", lambda *a, **k: rows)
    monkeypatch.setattr(sm, "_attach_intensity", lambda p: p)
    monkeypatch.setattr(sm, "_sector_ctx",
                        lambda p: sh.attach_sector_heat(p, ff, br, mm))

    out = sm.top_by_amount(days=5)
    merged = {x["code"]: x for x in out["rows"]}
    assert "sector_heat" in merged["a"] and merged["a"]["policy_hit"] == 0.05
    assert merged["z"]["sector_heat"] is None      # 无板块 → 诚实缺失


def test_sector_ctx_members_timeout_degrades(monkeypatch):
    """_sector_ctx 板块成分股反查套硬墙钟：_board_members_batch 慢/挂起时快速超时降级,
    不阻塞主清单返回(根因:530 板块逐个 fetch_constituents 触网可挂 120s+,today_list 被拖到
    116s 超时,主力动向筛不出最新数据)。超时后 sector_heat=None 诚实缺失,DB 数据仍秒回。"""
    import time
    from screener import smart_money as sm
    from screener import nextday as nd

    sm._SECTOR_MEMBERS_CACHE.clear()  # 干净起点
    rows = [{"code": "a", "name": "甲", "amount": 1e7}]
    ff = [{"name": "机器人", "main_net_inflow": 3e8}]
    br = [{"name": "机器人", "up_count": 60, "down_count": 40}]

    slow = {"n": 0}

    def slow_batch(scored):
        slow["n"] += 1
        time.sleep(5)  # 模拟慢网络/冷缓存下板块反查挂起
        return {"机器人": ["a"]}

    try:
        monkeypatch.setattr(sm, "_SECTOR_CTX_DEADLINE", 0.3)
        monkeypatch.setattr(nd, "_board_members_batch", slow_batch)
        t0 = time.monotonic()
        sm._sector_ctx(rows, fund_flow=ff, board_rows=br)
        elapsed = time.monotonic() - t0
        assert elapsed < 2.0                 # 硬墙钟降级,不被 5s 挂起拖垮
        assert rows[0]["sector_heat"] is None  # 超时 → 诚实缺失
        assert slow["n"] == 1                # 只触发一次反查
        # 空结果缓存:第二次直接复用空缓存秒回,不再花满 deadline 白等(死网络窗口不反复白等)
        t1 = time.monotonic()
        sm._sector_ctx(rows, fund_flow=ff, board_rows=br)
        assert time.monotonic() - t1 < 0.5
        assert slow["n"] == 1                # 未重复反查
    finally:
        sm._SECTOR_MEMBERS_CACHE.clear()     # 防泄漏污染其他测试


def test_sector_ctx_members_success_cached(monkeypatch):
    """板块反查成功结果缓存复用:第二次 _sector_ctx 直接命中缓存,不再重复触网反查。"""
    import time
    from screener import smart_money as sm
    from screener import nextday as nd

    sm._SECTOR_MEMBERS_CACHE.clear()
    rows = [{"code": "a", "name": "甲", "amount": 1e7}]
    ff = [{"name": "机器人", "main_net_inflow": 3e8}]
    br = [{"name": "机器人", "up_count": 60, "down_count": 40}]
    calls = {"n": 0}

    def fast_batch(scored):
        calls["n"] += 1
        return {"机器人": ["a"]}

    try:
        monkeypatch.setattr(nd, "_board_members_batch", fast_batch)
        sm._sector_ctx(rows, fund_flow=ff, board_rows=br)   # 首次: 反查并缓存
        assert calls["n"] == 1
        sm._sector_ctx(rows, fund_flow=ff, board_rows=br)   # 二次: 缓存命中
        assert calls["n"] == 1                               # 未重复触网
        assert rows[0]["sector_heat"] is not None
    finally:
        sm._SECTOR_MEMBERS_CACHE.clear()


# ===== B2 宏观风格调制（2026-09-20）：board_beta / policy_addon / attach 带 style =====

def test_board_beta_classification():
    """板块风格方向：进攻主线(政策主题高贝塔) / 防御板块(红利低波) / 其余中性。"""
    assert sh.board_beta("半导体") == "offensive"
    assert sh.board_beta("机器人") == "offensive"
    assert sh.board_beta("银行") == "defensive"
    assert sh.board_beta("公用事业") == "defensive"
    assert sh.board_beta("医药") == "neutral"      # 非进攻主线非防御板块
    # 2026-09-20 新增防御板块：基建(水泥/玻璃玻纤)与医药(化学制药/医疗器械)
    assert sh.board_beta("水泥") == "defensive"
    assert sh.board_beta("玻璃玻纤") == "defensive"
    assert sh.board_beta("化学制药") == "defensive"
    assert sh.board_beta("医疗器械") == "defensive"
    assert sh.board_beta("") == "neutral"


def test_policy_addon_attack_modulates_offensive():
    """进攻态：进攻主线政策加成放大 1.6(0.05→0.08) + 风格方向加成 0.03。"""
    pol, sh_ = sh.policy_addon("半导体", "attack")
    assert pol == pytest.approx(0.08)
    assert sh_ == pytest.approx(0.03)


def test_policy_addon_defense_shrinks_offensive():
    """防御态：进攻主线政策加成压到 0.6(0.05→0.03)，风格加成归零。"""
    pol, sh_ = sh.policy_addon("半导体", "defense")
    assert pol == pytest.approx(0.03)
    assert sh_ == pytest.approx(0.0)


def test_policy_addon_style_none_fallback():
    """无 style：回退静态政策加成 0.05，风格加成 0——兼容旧行为。"""
    pol, sh_ = sh.policy_addon("半导体", None)
    assert pol == pytest.approx(0.05)
    assert sh_ == pytest.approx(0.0)
    pol2, sh2 = sh.policy_addon("半导体", "")
    assert pol2 == pytest.approx(0.05) and sh2 == 0.0


def test_policy_addon_defensive_board_style_bonus():
    """防御态：防御板块风格加成 0.03（虽无政策加成）；进攻态防御板块两者皆 0。"""
    pol, sh_ = sh.policy_addon("银行", "defense")
    assert pol == pytest.approx(0.0)
    assert sh_ == pytest.approx(0.03)
    pol2, sh2 = sh.policy_addon("银行", "attack")
    assert pol2 == 0.0 and sh2 == 0.0


def test_policy_addon_neutral_board_no_style_effect():
    """中性板块(非进攻主线非防御)：任何风格都不给风格加成。"""
    for st in ("attack", "defense", "neutral", None):
        pol, sh_ = sh.policy_addon("医药", st)
        assert pol == 0.0 and sh_ == 0.0


def test_attach_sector_heat_with_attack_style(monkeypatch):
    """attach 带 style 攻击态：进攻主线政策放大+风格加成；防御板块风格加成；中性板无。"""
    rows = [
        {"code": "a"},   # 半导体(进攻主线)
        {"code": "b"},   # 银行(防御板块)
        {"code": "c"},   # 医药(中性)
    ]
    ff = [{"name": "半导体", "main_net_inflow": 3e8},
          {"name": "银行", "main_net_inflow": 2e8},
          {"name": "医药", "main_net_inflow": 1e8}]
    br = [{"name": "半导体", "up_count": 60, "down_count": 40},
          {"name": "银行", "up_count": 50, "down_count": 50},
          {"name": "医药", "up_count": 40, "down_count": 60}]
    mm = {"半导体": {"a"}, "银行": {"b"}, "医药": {"c"}}
    style = {"style": "attack", "score": 0.6}
    sh.attach_sector_heat(rows, ff, br, mm, style=style)
    merged = {x["code"]: x for x in rows}
    assert merged["a"]["policy_hit"] == pytest.approx(0.08)  # 进攻主线政策放大
    assert merged["a"]["style_hit"] == pytest.approx(0.03)   # 进攻主线风格加成
    assert merged["b"]["policy_hit"] == pytest.approx(0.0)   # 银行无政策主题
    assert merged["b"]["style_hit"] == pytest.approx(0.0)    # 进攻态不给防御板块加成
    assert merged["c"]["policy_hit"] == pytest.approx(0.0)
    assert merged["c"]["style_hit"] == pytest.approx(0.0)


def test_attach_sector_heat_with_defense_style():
    """attach 带 style 防御态：防御板块风格加成 0.03，进攻主线政策被压缩。"""
    rows = [{"code": "b"}, {"code": "a"}]
    ff = [{"name": "银行", "main_net_inflow": 3e8},
          {"name": "半导体", "main_net_inflow": 1e8}]
    br = [{"name": "银行", "up_count": 60, "down_count": 40},
          {"name": "半导体", "up_count": 40, "down_count": 60}]
    mm = {"银行": {"b"}, "半导体": {"a"}}
    style = {"style": "defense", "score": -0.5}
    sh.attach_sector_heat(rows, ff, br, mm, style=style)
    merged = {x["code"]: x for x in rows}
    assert merged["b"]["style_hit"] == pytest.approx(0.03)   # 防御板块防御态加成
    assert merged["a"]["policy_hit"] == pytest.approx(0.03)  # 进攻主线政策压到 0.6
    assert merged["a"]["style_hit"] == pytest.approx(0.0)    # 防御态不给进攻主线加成


def test_quality_refine_applies_style_into_final(monkeypatch):
    """精排加成计入 style_hit：同 res/heat 时风格加成破平，item 附 style_hit。"""
    from backtest import quality
    pool = [
        {"code": "a", "resonance": 0.6},  # 半导体(进攻主线,attack态政策放大+风格加成) → 列首
        {"code": "b", "resonance": 0.6},  # 医药(中性,无加成)
    ]
    ff = [{"name": "半导体", "main_net_inflow": 3e8},
          {"name": "医药", "main_net_inflow": 1e8}]
    br = [{"name": "半导体", "up_count": 60, "down_count": 40},
          {"name": "医药", "up_count": 40, "down_count": 60}]
    mm = {"半导体": {"a"}, "医药": {"b"}}
    style = {"style": "attack", "score": 0.6}
    out = quality._apply_sector_heat(pool, ff, br, mm, in_session=False, style=style)
    assert out[0]["code"] == "a"      # 风格加成破平 → a 列首
    merged = {x["code"]: x for x in out}
    assert merged["a"]["style_hit"] == pytest.approx(0.03)
    assert merged["a"]["policy_hit"] == pytest.approx(0.08)


def test_quality_refine_event_bonus_in_final():
    """新政事件催化权重进精排加成：命中事件的 code 靠 event_bonus 破平(同 res/heat)。"""
    from backtest import quality
    pool = [
        {"code": "car", "resonance": 0.6},  # 汽车(命中促消费事件,attack+时效权重>0) → 列首
        {"code": "med", "resonance": 0.6},  # 医药(中性,无政策/事件加成)
    ]
    ff = [{"name": "汽车", "main_net_inflow": 3e8},
          {"name": "医药", "main_net_inflow": 1e8}]
    br = [{"name": "汽车", "up_count": 60, "down_count": 40},
          {"name": "医药", "up_count": 40, "down_count": 60}]
    mm = {"汽车": {"car"}, "医药": {"med"}}
    style = {"style": "attack", "score": 0.6}
    as_of = "2026-09-02"   # 消费事件 09-01,age=1 → 时效衰减 + attack 放大 offensive
    out = quality._apply_sector_heat(pool, ff, br, mm, in_session=False,
                                     style=style, as_of=as_of)
    merged = {x["code"]: x for x in out}
    assert merged["car"]["policy_event"], "汽车应命中促消费事件"
    evw = max(e["weight"] for e in merged["car"]["policy_event"])
    assert evw > 0.0
    # event_bonus = min(evw, CAP)*FACTOR 进 _final,破平 → car 列首
    assert out[0]["code"] == "car"
    assert merged["med"]["policy_event"] == []           # 医药无事件 → 0 加成
    # 无事件 item 的 _final 不含 event_bonus(等价 0),不受影响
    assert merged["med"].get("_final", 0) >= 0.0


def test_attach_sector_heat_threads_style_to_policy_event():
    """B3 接线：attach 带 style/as_of → 命中事件 policy_event 附催化权重(style×时效)。"""
    import math
    rows = [{"code": "a"}, {"code": "c"}]
    ff = [{"name": "汽车", "main_net_inflow": 3e8}]
    br = [{"name": "汽车", "up_count": 60, "down_count": 40}]
    mm = {"汽车": {"a"}}
    style = {"style": "attack", "score": 0.6}
    sh.attach_sector_heat(rows, ff, br, mm, style=style, as_of="2026-09-10")
    evs = rows[0]["policy_event"]
    assert evs, "汽车应命中促消费事件"
    e = evs[0]
    assert "weight" in e and "age_days" in e          # B3 催化权重字段
    # 09-01→09-10 = 9 天时效 × 1.5(汽车 offensive in attack)
    assert e["weight"] == pytest.approx(math.exp(-9 / 7) * 1.5, abs=1e-3)
    assert e["age_days"] == 9
    assert rows[1]["policy_event"] == []              # 无板块 → 空