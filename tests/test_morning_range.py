"""
morning_range のユニットテスト
"""

from __future__ import annotations

import sys
from datetime import date, datetime
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from morning_range import (
    build_morning_signal,
    earnings_ratio,
    earnings_today,
    first5_table,
    morning_ratio,
    morning_rows,
    next_announcement,
    reaction_days,
)


def _daily(n: int = 60, start: str = "2026-07-01") -> pd.DataFrame:
    idx = pd.bdate_range(start, periods=n)
    close = np.full(n, 1000.0)
    return pd.DataFrame({"Open": close, "High": close * 1.02, "Low": close * 0.98, "Close": close}, index=idx)


def _m5(days: pd.DatetimeIndex, spread: float = 10.0, first: float = 4.0) -> pd.DataFrame:
    """毎日 9:00〜10:25 の5分足（UTC）。9時台の値幅=spread円、最初の5分の値幅=first円。"""
    rows = []
    for d in days:
        for k, hm in enumerate(pd.date_range(f"{d.date()} 09:00", f"{d.date()} 10:25", freq="5min")):
            hi = 1000 + (first / 2 if k == 0 else spread / 2 if k == 5 else 0.5)
            lo = 1000 - (first / 2 if k == 0 else spread / 2 if k == 6 else 0.5)
            rows.append((hm.tz_localize("Asia/Tokyo").tz_convert("UTC"), 1000, hi, lo, 1000))
    df = pd.DataFrame(rows, columns=["t", "Open", "High", "Low", "Close"]).set_index("t")
    return df


def test_morning_rows_and_ratio():
    daily = _daily()
    m5 = _m5(daily.index[20:50], spread=10.0, first=4.0)
    rows = morning_rows(m5, daily)
    assert len(rows) == 30
    # 1日の目安は 4%（高値1020-安値980 / 1000）、9時台は 1%、最初の10分は 0.4%
    assert abs(rows["avg10"].iloc[0] - 4.0) < 1e-9
    assert abs(rows["morning"].iloc[0] - 1.0) < 1e-9
    assert abs(rows["first5"].iloc[0] - 0.4) < 1e-9
    v = morning_ratio(rows)
    assert abs(v["ratio"] - 0.25) < 1e-9 and v["n"] == 30
    print("✅ test_morning_rows_and_ratio")


def test_ignores_bars_after_ten():
    daily = _daily()
    m5 = _m5(daily.index[20:21])
    late = pd.DataFrame({"Open": [1000], "High": [1100], "Low": [900], "Close": [1000]},
                        index=[pd.Timestamp(f"{daily.index[20].date()} 10:10", tz="Asia/Tokyo").tz_convert("UTC")])
    rows = morning_rows(pd.concat([m5, late]).sort_index(), daily)
    assert abs(rows["morning"].iloc[0] - 1.0) < 1e-9
    print("✅ test_ignores_bars_after_ten")


def test_first5_table_buckets():
    n = 200
    rng = np.random.default_rng(1)
    first = rng.uniform(0, 0.6, n)
    rows = pd.DataFrame({"avg10": np.full(n, 1.0), "first5": first, "morning": 0.4 + first})
    t = first5_table(rows)
    assert [r["from"] for r in t] == [0.0, 0.15, 0.30, 0.45]
    assert t[-1]["to"] is None
    assert t[0]["ratio"] < t[-1]["ratio"]
    print("✅ test_first5_table_buckets")


def test_reaction_days_after_close_is_next_day():
    days = pd.bdate_range("2026-10-26", periods=5)    # 月〜金
    ann = [datetime(2026, 10, 27, 15, 30), datetime(2026, 10, 29, 11, 30), datetime(2026, 10, 29)]
    got = reaction_days(ann, days)
    assert got == [pd.Timestamp("2026-10-28"), pd.Timestamp("2026-10-29"), pd.Timestamp("2026-10-30")]
    print("✅ test_reaction_days_after_close_is_next_day")


def test_earnings_ratio_and_today():
    daily = _daily(40)
    daily.loc[daily.index[30], ["High", "Low"]] = [1040, 960]     # 決算明けは値幅2倍
    ann = [datetime.combine(daily.index[29].date(), datetime.min.time()).replace(hour=15)]
    e = earnings_ratio(daily, ann)
    assert e["n"] == 1 and abs(e["ratio"] - 2.0) < 1e-9
    today, prev = daily.index[30].date(), daily.index[29].date()
    assert earnings_today(ann, today, prev)
    assert not earnings_today(ann, daily.index[31].date(), today)
    assert earnings_today([datetime.combine(today, datetime.min.time()).replace(hour=8)], today, prev)
    assert next_announcement(ann + [datetime(2026, 11, 5, 15)], today) == "2026-11-05"
    print("✅ test_earnings_ratio_and_today")


def test_build_morning_signal():
    rng = {"available": True, "pred_yen": 100.0, "shares": 100, "as_of": "2026-10-28"}
    val = {"n": 40, "ratio": 0.6, "rough_ratio": 0.9, "earnings": {"n": 8, "ratio": 2.3}}
    table = [{"from": 0.0, "to": 0.15, "n": 50, "ratio": 0.5}, {"from": 0.35, "to": None, "n": 50, "ratio": 0.9}]
    ann = [datetime(2026, 10, 28, 15, 30)]
    m = build_morning_signal(rng, val, table, ann, date(2026, 10, 29))
    assert m["available"] and abs(m["morning_lot_yen"] - 6000) < 1e-9 and abs(m["rough_lot_yen"] - 9000) < 1e-9
    assert abs(m["first5_lot_yen"] - 3500) < 1e-9 and abs(m["first5_morning_lot_yen"] - 9000) < 1e-9
    assert m["earnings_today"] and m["earnings_ratio"] == 2.3
    # 検証結果がない銘柄は 9時台の目安を出さない（決算の注意は出す）
    m2 = build_morning_signal(rng, None, None, ann, date(2026, 10, 29))
    assert not m2["available"] and m2["earnings_today"]
    print("✅ test_build_morning_signal")


if __name__ == "__main__":
    test_morning_rows_and_ratio()
    test_ignores_bars_after_ten()
    test_first5_table_buckets()
    test_reaction_days_after_close_is_next_day()
    test_earnings_ratio_and_today()
    test_build_morning_signal()
    print("\n🎉 All morning_range tests passed!")
