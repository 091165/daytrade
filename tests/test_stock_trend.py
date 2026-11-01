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

from stock_trend import at_high, at_high_flags, bar_streak, bias_hint, deviation_25ma, deviation_sigma, this_week_bar, trend_summary


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


def test_at_high():
    idx = pd.bdate_range("2025-01-01", periods=300)
    base = pd.Series(np.r_[np.linspace(100, 120, 200), np.linspace(120, 110, 97), [118, 121, 119]], index=idx)
    # 直近5日に 121 > それより前の52週の高値 120 → 52週高値更新中
    assert at_high(base, base) == "52週高値更新中"
    # 日足より前の最高値 150 を超えていないので上場来ではない／130 なら上場来
    assert at_high(base, base, older_high=150) == "52週高値更新中"
    assert at_high(base, base, older_high=119) == "上場来高値更新中"
    # 6日前に付けた高値は「更新中」に入らない
    old = base.copy()
    old.iloc[-6] = 125
    assert at_high(old, old) is None
    # データが250本に足りなければ判定しない
    assert at_high(base.iloc[-200:], base.iloc[-200:]) is None
    # 検証用の日ごとの判定は、前日までで at_high と同じ
    flags = at_high_flags(base)
    nxt = idx[-1] + pd.offsets.BDay(1)
    ext = pd.concat([base, pd.Series([90.0], index=[nxt])])
    assert bool(at_high_flags(ext).iloc[-1]) == (at_high(base, base) is not None)
    assert not flags.iloc[:250].any()
    print("✅ test_at_high")


if __name__ == "__main__":
    test_at_high()
    test_deviation_and_sigma()
    test_today_bar_not_used()
    test_monthly_streak_excludes_current_month()
    test_this_week_bar()
    test_bias_hint()
    print("\n🎉 All stock_trend tests passed!")
