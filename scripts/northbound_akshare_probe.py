# -*- coding: utf-8 -*-
"""Phase 0: 容器内探 akshare 北向/沪深股通函数在当前出口 IP 的可用性。
只读诊断，不触生产逻辑，结果 print + 写 /app/var/northbound_akshare_probe.json。

运行(容器内): python -m scripts.northbound_akshare_probe  (需先 docker cp 本文件进 /app/scripts/)
聚焦: 完整持股 + 个股逐日净买 + 历史。东财被封的函数会超时(~20s)被 try 吞掉。
"""
from __future__ import annotations
import json, os, time, traceback
import akshare as ak

_AK = getattr(ak, "__version__", "?")
_START = time.time()

# 针对性核心探针: (名称, 调用闭包)
_CORE = [
    ("stock_hsgt_hold_stock_em(今日排行)", lambda: ak.stock_hsgt_hold_stock_em(market="北向", indicator="今日排行")),
    ("stock_hsgt_hold_stock_detail_em(600519)", lambda: ak.stock_hsgt_hold_stock_detail_em(stock="600519")),
    ("stock_hsgt_hold_stock_detail_em(sh600519)", lambda: ak.stock_hsgt_hold_stock_detail_em(stock="sh600519")),
    ("stock_hsgt_hist_em(北向资金)", lambda: ak.stock_hsgt_hist_em(symbol="北向资金")),
    ("stock_hsgt_north_net_flow_in(北向)", lambda: ak.stock_hsgt_north_net_flow_in(symbol="北向")),
    ("stock_hsgt_fund_flow_summary_em()", lambda: ak.stock_hsgt_fund_flow_summary_em()),
    ("stock_hsgt_fund_flow_rank_em(今日)", lambda: ak.stock_hsgt_fund_flow_rank_em(indicator="今日")),
    ("stock_hsgt_board_rank_em(今日)", lambda: ak.stock_hsgt_board_rank_em(indicator="今日")),
    ("stock_hsgt_industry_em(北向)", lambda: ak.stock_hsgt_industry_em(symbol="北向")),
    ("stock_gdfx_free_top_10_em(600519)", lambda: ak.stock_gdfx_free_top_10_em(symbol="sh600519", date="20240331")),
]


def _probe(name, fn):
    t0 = time.time()
    try:
        df = fn()
        rows = getattr(df, "shape", (0,))[0]
        cols = list(getattr(df, "columns", []))[:12]
        ok = df is not None and rows > 0
        return {"ok": ok, "rows": rows, "cols": cols,
                "secs": round(time.time() - t0, 1),
                "sample": (df.iloc[:2].to_dict(orient="records") if ok else None)}
    except Exception as e:
        return {"ok": False, "rows": 0, "cols": [],
                "secs": round(time.time() - t0, 1),
                "err": f"{type(e).__name__}: {str(e)[:90]}"}


def main() -> dict:
    out: dict = {"akshare": _AK}
    out["core"] = {name: _probe(name, fn) for name, fn in _CORE}

    # 动态扫: dir(ak) 里含 hsgt/north 的无参函数, cap 25 防太长
    dyn_names = sorted({n for n in dir(ak)
                        if ("hsgt" in n.lower() or "north" in n.lower())
                        and not n.startswith("_")})
    out["dyn_total_found"] = len(dyn_names)
    out["dyn"] = {}
    import inspect
    tried = 0
    for n in dyn_names:
        if tried >= 25:
            break
        fn = getattr(ak, n)
        try:
            sig = inspect.signature(fn)
            req = [p for p in sig.parameters.values()
                   if p.default is inspect.Parameter.empty and p.kind in
                   (p.POSITIONAL_OR_KEYWORD, p.KEYWORD_ONLY)]
            if req:
                out["dyn"][n] = {"skip": "need_params",
                                 "params": [p.name for p in req]}
                continue
        except (TypeError, ValueError):
            pass
        tried += 1
        out["dyn"][n] = _probe(f"dyn:{n}", lambda f=fn: f())

    out["secs"] = round(time.time() - _START, 1)
    cache = os.environ.get("SCREENER_CACHE_DIR", "/app/var")
    os.makedirs(cache, exist_ok=True)
    path = os.path.join(cache, "northbound_akshare_probe.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2, default=str)
    print(json.dumps(out, ensure_ascii=False, indent=2, default=str))
    print(f"written: {path}")
    return out


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
