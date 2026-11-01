"""
us_range_study のユニットテスト
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from us_range_study import bucket_of, mark_significant, range_ratios, summarize, today_multiplier


def _market(n=500, effect=0.0, seed=0):
    """米関連株の動きが大きい翌日ほど、値幅が (1 + effect × |動き|) 倍に広がる合成データ"""
    rng = np.random.default_rng(seed)
    us_days = pd.bdate_range("2024-01-01", periods=n)
    us = pd.Series(rng.normal(0, 1.8, n), index=us_days)
    jp_days = us_days + pd.Timedelta(days=1)
    jp_days = jp_days[jp_days.weekday < 5]
    lag = us.reindex(jp_days - pd.Timedelta(days=1)).fillna(0).values
    width = 0.02 * (1 + effect * np.abs(lag)) * np.exp(rng.normal(0, 0.15, len(jp_days)))
    close = 1000 * np.exp(rng.normal(0, 0.01, len(jp_days)).cumsum())
    ohlc = pd.DataFrame({"Open": close, "High": close * (1 + width / 2), "Low": close * (1 - width / 2), "Close": close},
                        index=jp_days)
    return ohlc, us


def test_alignment_and_skip():
    ohlc, us = _market(60)
    rows = range_ratios(ohlc, us)
    t = rows.index[20]
    assert rows.loc[t, "us"] == us[us.index < t].iloc[-1]          # 日本の t には、t より前の最新の米国の値
    skipped = range_ratios(ohlc, us, [t])
    assert t not in skipped.index
    assert bucket_of(0.5) == "1%未満" and bucket_of(2.5) == "2〜3%" and bucket_of(7) == "3%以上"
    print("✅ test_alignment_and_skip")


def test_detects_effect_and_ignores_noise():
    ohlc, us = _market(600, effect=0.25, seed=1)
    a = {"A": {"name": "A", **summarize(range_ratios(ohlc, us))}}
    ohlc0, us0 = _market(600, effect=0.0, seed=2)
    a["B"] = {"name": "B", **summarize(range_ratios(ohlc0, us0))}
    mark_significant(a)
    big = [b for b in a["A"]["buckets"] if b["bucket"] == "3%以上"][0]
    assert big["significant"] and big["multiplier"] > a["A"]["buckets"][0]["mean"]
    assert not any(b.get("significant") for b in a["B"]["buckets"])
    m = today_multiplier({"per_code": a}, "A", -3.4)
    assert m["bucket"] == "3%以上" and m["multiplier"] == big["multiplier"] and "倍" in m["evidence"]
    assert today_multiplier({"per_code": a}, "B", 3.4)["multiplier"] is None
    assert today_multiplier(None, "A", 1.0)["evidence"] == "未検証"
    print("✅ test_detects_effect_and_ignores_noise")


if __name__ == "__main__":
    test_alignment_and_skip()
    test_detects_effect_and_ignores_noise()
    print("\n🎉 All us_range_study tests passed!")
