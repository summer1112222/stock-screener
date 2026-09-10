# scripts/etf_source_probe.py
"""Phase 0: 实测 akshare/公开数据在出口 IP 的可用性，结果写 data/cache/etf_source_probe.json。
只读、依赖容器内 akshare，不触生产逻辑。"""
from __future__ import annotations
import json, os, traceback
try:
    import akshare as ak
except Exception:
    ak = None

_ENDPOINTS = [
    ("fund_etf_spot_em", lambda: ak.fund_etf_spot_em() if ak else (_ for _ in ()).throw(ImportError())),
    ("stock_zh_index_value_csindex", lambda: ak.stock_zh_index_value_csindex(symbol="000300") if ak else (_ for _ in ()).throw(ImportError())),
    ("fund_etf_fund_info_em", lambda: ak.fund_etf_fund_info_em(symbol="513100") if ak else (_ for _ in ()).throw(ImportError())),
]

def _probe(name, fn):
    try:
        df = fn()
        ok = df is not None and getattr(df, "shape", (0,))[0] > 0
        return ok, f"rows={getattr(df, 'shape', (0,))[0]}" if ok else "empty/None"
    except Exception as e:
        return False, f"{type(e).__name__}: {str(e)[:80]}"

def main() -> dict:
    out = {}
    for name, fn in _ENDPOINTS:
        out[name] = _probe(name, fn)
    # 集思录可直接 read_html 静态表(独立站,非东财),仅探可达性;疑似需 cookie 则仍标 false
    try:
        import pandas as pd
        gu = pd.read_html("https://www.jisilu.cn/data/etf/", flavor="lxml")
        out["jisilu_static"] = (len(gu) > 0, f"tables={len(gu)}")
    except Exception as e:
        out["jisilu_static"] = (False, f"{type(e).__name__}: {str(e)[:80]}")
    # 采集东财 ETF spot 即知封禁与否(容器内需先 pip 装 akshare)
    cache_dir = os.environ.get("SCREENER_CACHE_DIR", "/app/var")
    os.makedirs(cache_dir, exist_ok=True)
    path = os.path.join(cache_dir, "etf_source_probe.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(json.dumps(out, ensure_ascii=False, indent=2))
    print(f"written: {path}")
    return out

if __name__ == "__main__":
    main()
