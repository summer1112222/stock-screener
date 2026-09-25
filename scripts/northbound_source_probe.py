# -*- coding: utf-8 -*-
"""Phase 0: 探通达信(pytdx)能否提供北向/沪深股通数据。只读诊断，不触生产逻辑。

用户定调"全数据 tdx 优先"(记忆 tdx-primary-directive)。本探针回答：
  Q1 get_company_info 公司信息文本类目里是否含北向/沪深股通数据(能看不能算?)
  Q2 get_block_members 板块名单里是否有"沪深股通/北向"板块(有名单无净买?)
  Q3 pytdx 库源码有无未封装北向接口(静态: hq.py 方法集已确认无 hsgt/资金流)

结果写 data/cache/northbound_source_probe.json(同 etf_source_probe.py 范式)。
宿主需装 pytdx(项目 data/pytdx_client.py 已用)。只读,不装新依赖。
运行: python -m scripts.northbound_source_probe  (仓库根目录)
"""
from __future__ import annotations
import json, os, traceback
from data import pytdx_client as t

_KW = ["北向", "沪股通", "深股通", "沪深股通", "陆股通", "北上",
       "北向资金", "hsgt", "HSGT", "港股通"]

# 已知 16 类中的候选(避免乱码匹配失败的兜底关键词)
_TRY_CATS = ["主力追踪", "股东研究", "龙虎榜单", "公司概况",
             "股本结构", "财务分析", "研究报告", "业内点评"]


def _hit(text: str) -> list[str]:
    return [kw for kw in _KW if kw in (text or "")]


def _decode(name) -> str:
    if isinstance(name, bytes):
        for enc in ("gbk", "utf-8"):
            try:
                return name.decode(enc)
            except Exception:
                continue
        return name.decode("gbk", "replace")
    return str(name or "")


def _probe_company_info(code: str) -> dict:
    """Q1: 迭代该股全类目取文本,找含北向的类目。用 api 原语 + GBK 解码。"""
    out = {"code": code, "ok": False, "err": "",
           "categories": [], "north_hits": {}, "sample_texts": {}}
    api = t._get_api()
    if api is None:
        out["err"] = "通达信服务器全不可用"
        return out
    pure = t._pure_code(code)
    m = t._market(pure)
    if m is None:
        out["err"] = f"无法识别 market {code}"
        return out
    try:
        with t._lock:
            cats = api.get_company_info_category(m, pure) or []
    except Exception as e:
        out["err"] = f"类别查询失败: {e}"
        return out
    names = []
    for c in cats:
        nm = _decode(c.get("name") if isinstance(c, dict) else "")
        names.append(nm)
        if not nm:
            continue
        try:
            with t._lock:
                content = api.get_company_info_content(
                    m, pure,
                    c.get("filename"), c.get("start"), c.get("length"))
            content = content if isinstance(content, str) else str(content or "")
        except Exception as e:
            continue
        hit = _hit(content)
        if hit:
            out["north_hits"][nm] = {"len": len(content), "kw": hit,
                                     "sample": content[:300]}
            out["sample_texts"][nm] = content[:500]
    # 兜底: 用关键词类别名再试一次(若上面按真实类目已覆盖则跳过)
    seen = set(names)
    for cat in _TRY_CATS:
        if cat in seen:
            continue
        r = t.get_company_info(code, cat)
        if r.get("ok"):
            hit = _hit(r.get("content") or "")
            if hit:
                out["north_hits"][f"[兜底]{cat}"] = {"len": len(r["content"]),
                                                    "kw": hit}
                out["sample_texts"][f"[兜底]{cat}"] = r["content"][:500]
    out["categories"] = names
    out["ok"] = True
    return out


def _probe_block_members() -> dict:
    """Q2: 读全部板块名单,找名字含北向/沪深股通/港股通/港股的板块。"""
    try:
        all_b = t.get_block_members("all") or {}
    except Exception as e:
        return {"ok": False, "err": f"{type(e).__name__}: {e}"}
    hit = {}
    for name, codes in (all_b or {}).items():
        k = _hit(str(name))
        if k:
            hit[str(name)] = {"members": len(codes), "sample": codes[:8]}
    return {"ok": True, "total_blocks": len(all_b),
            "north_blocks": hit}


def main() -> dict:
    out: dict = {}
    # 探活: 行情
    try:
        q = t.get_quote(["600519", "000001"])
        out["quote"] = {"ok": q is not None, "n": len(q or [])}
    except Exception as e:
        out["quote"] = {"ok": False, "err": f"{type(e).__name__}: {e}"}
    # Q1 公司信息文本类目
    out["company_info_600519"] = _probe_company_info("600519")
    out["company_info_000001"] = _probe_company_info("000001")
    # Q2 板块名单
    out["block_members"] = _probe_block_members()
    # Q3 源码结论(静态,源码 grep 已确认)
    out["pytdx_lib_src"] = ("hq.py 方法集: get_security_bars/index_bars/quotes/security_list/"
                            "company_info_category/content/xdxr/finance/block_info,"
                            "无 hsgt/北向/沪深股通/资金流接口")
    # 写缓存
    cache = os.environ.get("SCREENER_CACHE_DIR", "data/cache")
    os.makedirs(cache, exist_ok=True)
    path = os.path.join(cache, "northbound_source_probe.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(json.dumps(out, ensure_ascii=False, indent=2))
    print(f"written: {path}")
    return out


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
