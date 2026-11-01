"""
stock_trend のユニットテスト
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from stock_trend import bar_streak, bias_hint, deviation_25ma, deviation_sigma, this_week_bar, trend_summary


def _ohlc(n=300, seed=0, start="2025-06-02"):
    rng = np.random.default_rng(seed)
    c = 1000 * np.exp(rng.normal(0, 0.01, n).cumsum())
    o = c * (1 + rng.normal(0, 0.003, n))
    idx = pd.bdate_range(start, periods=n)
    return pd.DataFrame({"Open": o, "High": np.maximum(o, c) * 1.01, "Low": np.minimum(o, c) * 0.99, "Close": c}, index=idx)


def test_deviation_and_sigma():
    c = pd.Series(np.r_[np.full(30, 100.0), 90.0])
    assert abs(deviation_25ma(c) - (90 / ((100 * 24 + 90) / 25) - 1) * 100) < 1e-9
    assert deviation_25ma(c.iloc[:10]) is None
    mu, lo, hi = deviation_sigma(_ohlc()["Close"])
    assert lo < mu < hi
    print("✅ test_deviation_and_sigma")


def test_today_bar_not_used():
    d = _ohlc()
    today = d.index[-1].date()
    a = trend_summary(d, today)
    d2 = d.copy()
    d2.iloc[-1, d2.columns.get_loc("Close")] *= 1.3          # 今日の足を変えても結果は同じ
    assert trend_summary(d2, today)["dev"] == a["dev"]
    print("✅ test_today_bar_not_used")


def test_monthly_streak_excludes_current_month():
    idx = pd.bdate_range("2026-07-01", "2026-11-04")
    o = np.ones(len(idx)) * 100
    c = o.copy()
    m = idx.month
    c[m == 7] = 90; c[m == 8] = 110; c[m == 9] = 120; c[m == 10] = 130   # 7月陰線 8〜10月陽線
    o[m == 8] = 100; o[m == 9] = 100; o[m == 10] = 100
    c[m == 11] = 50                                                     # 今月（未確定）は大きな陰線
    d = pd.DataFrame({"Open": o, "High": np.maximum(o, c), "Low": np.minimum(o, c), "Close": c}, index=idx)
    assert bar_streak(d, "M", date(2026, 11, 5)) == ("up", 3)
    print("✅ test_monthly_streak_excludes_current_month")


def test_this_week_bar():
    d = _ohlc(200)
    fri = [x for x in d.index if x.weekday() == 4][-1]
    w = this_week_bar(d, fri.date())
    week = d[(d.index >= fri - pd.Timedelta(days=4)) & (d.index < fri)]
    assert w["up"] == (week["Close"].iloc[-1] > week["Open"].iloc[0])
    print("✅ test_this_week_bar")


def test_bias_hint():
    assert "買い中心" in bias_hint(-8, -6, 6)
    assert "売り候補" in bias_hint(7, -6, 6)
    assert bias_hint(1, -6, 6) == ""
    print("✅ test_bias_hint")


if __name__ == "__main__":
    test_deviation_and_sigma()
    test_today_bar_not_used()
    test_monthly_streak_excludes_current_month()
    test_this_week_bar()
    test_bias_hint()
    print("\n🎉 All stock_trend tests passed!")
