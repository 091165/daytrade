"""
stock_screen のユニットテスト
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from stock_screen import daily_moves, screen_all, screen_stock


def _ohlc(seed: int, n: int = 400, fade: float = 0.0, us: pd.Series | None = None, us_beta: float = 0.0) -> pd.DataFrame:
    """合成の日足。fade<0 なら窓と逆に寄り後が動く。us_beta>0 なら米国株の前日の動きに寄り後が連動する。"""
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2024-01-01", periods=n)
    gap = rng.normal(0, 1.0, n)
    intraday = rng.normal(0, 1.5, n) + fade * gap
    if us is not None:
        intraday = intraday + us_beta * us.reindex(idx - pd.Timedelta(days=1)).fillna(0).values
    close = np.empty(n)
    open_ = np.empty(n)
    prev = 1000.0
    for i in range(n):
        open_[i] = prev * (1 + gap[i] / 100)
        close[i] = open_[i] * (1 + intraday[i] / 100)
        prev = close[i]
    high = np.maximum(open_, close) * (1 + np.abs(rng.normal(0.01, 0.003, n)))
    low = np.minimum(open_, close) * (1 - np.abs(rng.normal(0.01, 0.003, n)))
    return pd.DataFrame({"Open": open_, "High": high, "Low": low, "Close": close,
                         "Volume": np.full(n, 1e6)}, index=idx)


def test_daily_moves_columns():
    df = daily_moves(_ohlc(1, n=10))
    assert list(df.columns) == ["gap", "intraday", "prev"]
    assert len(df) == 9          # 初日だけは窓・前日の寄り後が出せない
    print("✅ test_daily_moves_columns")


def test_detects_gap_fade_and_ignores_noise():
    us = pd.Series(np.random.default_rng(9).normal(0, 1, 600), index=pd.bdate_range("2023-12-01", periods=600))
    res = {
        "FADE": screen_stock(_ohlc(2, fade=-0.6), {"QQQ": us}),
        "NOISE": screen_stock(_ohlc(3), {"QQQ": us}),
    }
    screen_all(res)
    fade = res["FADE"]["predictors"]["gap"]
    assert fade["reliable"] and fade["r_conf"] < 0
    assert fade["hit_conf"] > 0.55 and fade["edge_pct"] > 0
    assert not any(p["reliable"] for p in res["NOISE"]["predictors"].values())
    print("✅ test_detects_gap_fade_and_ignores_noise")


def test_us_predictor_chosen_in_first_half_only():
    us_idx = pd.bdate_range("2023-12-01", periods=600)
    rng = np.random.default_rng(4)
    linked = pd.Series(rng.normal(0, 1, 600), index=us_idx)
    noise = pd.Series(rng.normal(0, 1, 600), index=us_idx)
    r = screen_stock(_ohlc(5, us=linked, us_beta=1.0), {"LINK": linked, "NOISE": noise})
    screen_all({"X": r})
    p = r["predictors"]["us"]
    assert p["us"] == "LINK"
    assert p["reliable"] and p["r_conf"] > 0
    print("✅ test_us_predictor_chosen_in_first_half_only")


def test_range_and_size_fields():
    r = screen_stock(_ohlc(6), {})
    assert r["range_pct"] > 0 and r["range_yen"] > 0
    assert r["range_r"] is not None
    assert r["turnover_oku"] is not None
    assert "us" not in r["predictors"]     # 米国株のデータがなければ出さない
    print("✅ test_range_and_size_fields")


if __name__ == "__main__":
    test_daily_moves_columns()
    test_detects_gap_fade_and_ignores_noise()
    test_us_predictor_chosen_in_first_half_only()
    test_range_and_size_fields()
    print("\n🎉 All stock_screen tests passed!")
