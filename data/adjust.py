# -*- coding: utf-8 -*-
"""前复权(qfq)本地计算 —— 基于 pytdx 不复权 raw 日 K + get_xdxr_info 除权除息因子。

pytdx `get_security_bars` 返回**不复权**日 K；`get_xdxr_info` 返回每只票全历史
除权除息/股本变动记录。本模块用后者给前者算前复权，使通达信成为历史日 K 的
自洽主源（不再依赖新浪/东财 qfq 接口）。

算法（前复权）：
- 只处理 category==1（除权除息）事件；category==5（股本变化）不调价只改股本，
  对价格/收益率复权无影响，跳过。
- 每个除权日 D，取 D 前一交易日 raw 收盘 prev_close，按
  P_ref = (prev_close - fenhong + peigujia*peigu_per) / (1 + song_per + pei_per)
  算理论除权价，ratio = P_ref / prev_close（<1 表示分红使历史价下调）。
  songzhuangu/peigu 在 pytdx 中为"每 10 股"单位，故 song_per=songzhuangu/10。
- 前复权：t < D 的价格 *= Π ratio[D']（所有之后的除权日）。
  实现上**先用 raw 算全部 ratio（避免累乘污染前收），再按除权日降序累乘**到
  对应位置之前的所有行；volume 反向 ÷ (1+song_per+pei_per)（送转扩股使历史
  量上调）；amount 为实际成交额，不复权。

合规：复权仅影响历史收益率/成本计算口径，非买卖信号。
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def get_xdxr(code: str) -> pd.DataFrame:
    """取某股除权除息记录（规范化）。依赖 pytdx_client。

    返回列：date(YYYY-MM-DD)/category/fenhong/songzhuangu/peigu/peigujia/suogu。
    pytdx 不可用或无记录返空 DataFrame（不抛崩）。
    """
    from . import pytdx_client  # 延迟导入避循环
    if not pytdx_client._TDX_OK:
        return pd.DataFrame()
    try:
        raw = pytdx_client.get_xdxr(code)
    except Exception:
        return pd.DataFrame()
    if raw is None or raw.empty:
        return pd.DataFrame()
    out = raw.copy()
    out["date"] = (
        out["year"].astype(str) + "-" +
        out["month"].astype(str).str.zfill(2) + "-" +
        out["day"].astype(str).str.zfill(2)
    )
    # pytdx 的 fenhong/songzhuangu/peigu 均为"每 10 股"单位，归一化为每股
    for col in ("fenhong", "songzhuangu", "peigu"):
        if col in out.columns:
            out[col] = pd.to_numeric(out[col], errors="coerce").fillna(0.0) / 10.0
    keep = [c for c in ("date", "category", "fenhong", "songzhuangu",
                        "peigu", "peigujia", "suogu") if c in out.columns]
    return out[keep].reset_index(drop=True)


def qfq(raw_df: pd.DataFrame, xdxr_df: pd.DataFrame | None) -> pd.DataFrame:
    """给不复权日 K 算前复权。raw_df 需含 date/open/high/low/close/volume（升序）。
    xdxr_df 为 get_xdxr 返回（空则原样返回）。返回新 DataFrame，不改动入参。"""
    if raw_df is None or raw_df.empty:
        return raw_df if raw_df is not None else pd.DataFrame()
    df = raw_df.copy()
    df["date"] = df["date"].astype(str)
    df = df.sort_values("date").reset_index(drop=True)
    if xdxr_df is None or xdxr_df.empty:
        return df
    ev = xdxr_df[xdxr_df["category"] == 1].copy() if "category" in xdxr_df.columns else pd.DataFrame()
    if ev.empty:
        return df
    ev["date"] = ev["date"].astype(str)
    dates = df["date"].to_numpy()

    # 1) 先用 raw 收盘算全部 (idx, ratio, vol_expand)，避免累乘污染前收
    plans: list[tuple[int, float, float]] = []
    for _, r in ev.sort_values("date", ascending=False).iterrows():
        d = str(r["date"])
        ge = np.flatnonzero(dates >= d)
        if ge.size == 0:
            continue  # 除权日晚于数据末日（未来事件），不影响历史
        i = int(ge[0])
        if i == 0:
            continue  # 数据起点之前/当日的除权，无前收无法算，跳过（诚实不回溯）
        prev_close = float(df.iloc[i - 1]["close"])
        if prev_close <= 0:
            continue
        fenhong = float(r.get("fenhong") or 0.0)  # 已归一化为每股
        song_per = float(r.get("songzhuangu") or 0.0)  # 每股送转
        pei_per = float(r.get("peigu") or 0.0)  # 每股配股
        peigujia = float(r.get("peigujia") or 0.0)
        denom = 1.0 + song_per + pei_per
        if denom <= 0:
            continue
        p_ref = (prev_close - fenhong + peigujia * pei_per) / denom
        ratio = p_ref / prev_close
        plans.append((i, ratio, denom))

    # 2) 降序（plans 已按除权日降序）累乘应用到对应位置之前
    for i, ratio, vol_expand in plans:
        for col in ("open", "high", "low", "close"):
            if col in df.columns:
                vals = df[col].to_numpy(dtype=float).copy()
                vals[:i] *= ratio
                df[col] = vals
        if "volume" in df.columns:
            vols = df["volume"].to_numpy(dtype=float).copy()
            vols[:i] /= vol_expand
            df["volume"] = vols
    return df
