"""
指数（ナスダック・SOX・日経）の25日線乖離が、その指数自身の過去の分布で何σにあたるか（純粋関数）

長期スクリーナーの「理想乖離」と同じ考え方：乖離率の過去250日の平均と標準偏差から ±2σ・±3σ を出す。
"""

from __future__ import annotations

import pandas as pd

MA_DAYS = 25
LOOKBACK = 250


def dev_sigma(close: pd.Series) -> pd.DataFrame:
    """各日の終値での25日線乖離（%）と、前日までの250日の乖離の平均からの距離（σ）"""
    dev = (close / close.rolling(MA_DAYS).mean() - 1) * 100
    mu = dev.rolling(LOOKBACK, min_periods=120).mean().shift(1)
    sd = dev.rolling(LOOKBACK, min_periods=120).std().shift(1)
    return pd.DataFrame({"dev": dev, "z": (dev - mu) / sd}).dropna()


def latest_z(close: pd.Series) -> dict | None:
    d = dev_sigma(close.dropna())
    if d.empty:
        return None
    return {"dev": float(d["dev"].iloc[-1]), "z": float(d["z"].iloc[-1]), "date": str(d.index[-1].date())}


def zone(z: float | None) -> str:
    if z is None:
        return "unknown"
    if z <= -3:
        return "minus3"
    if z <= -2:
        return "minus2"
    if z >= 3:
        return "plus3"
    if z >= 2:
        return "plus2"
    return "normal"
