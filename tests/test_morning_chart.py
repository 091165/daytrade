"""
morning_chart のユニットテスト
"""

from __future__ import annotations

import sys
from datetime import date, time
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from morning_chart import prev_closes, split_days, summarize_day

JST = ZoneInfo("Asia/Tokyo")


def _bars(day: str, closes: list[float], start: str = "09:00") -> pd.DataFrame:
    idx = pd.date_range(f"{day} {start}", periods=len(closes), freq="1min", tz=JST)
    c = pd.Series(closes, index=idx, dtype=float)
    o = c.shift(1).fillna(c.iloc[0])
    return pd.DataFrame({"Open": o, "High": pd.concat([o, c], axis=1).max(axis=1),
                         "Low": pd.concat([o, c], axis=1).min(axis=1), "Close": c, "Volume": 1})


def test_split_days_keeps_morning_only():
    idx = pd.date_range("2026-11-09 08:58", "2026-11-09 11:00", freq="1min", tz=JST).tz_convert("UTC")
    df = pd.DataFrame({"Open": 1.0, "High": 1.0, "Low": 1.0, "Close": 1.0, "Volume": 1}, index=idx)
    days = split_days(df)
    assert list(days) == [date(2026, 11, 9)]
    b = days[date(2026, 11, 9)]
    assert b.index[0].time() == time(9, 0) and b.index[-1].time() == time(10, 29) and len(b) == 90
    print("✅ test_split_days_keeps_morning_only")


def test_summarize_breakout_up_and_revert():
    # 9:00〜9:09 は 100〜102 の間、9:12 に 103 で上抜け、9:20 に 110 の高値、その後 108 まで戻す
    closes = [100, 101, 102, 101, 100, 101, 102, 101, 100, 101,   # 9:00-9:09
              101, 102, 103, 104, 105, 106, 107, 108, 109, 109,   # 9:10-9:19
              110, 109.5, 109, 108.8, 108.5]                     # 9:20-9:24
    s = summarize_day(_bars("2026-11-09", closes), prev_close=98)
    assert s["hi10"] == 102 and s["lo10"] == 100
    assert s["brk"] == "上抜け" and s["brk_time"] == time(9, 12)
    assert s["high"] == 110 and s["high_time"] == time(9, 20)
    assert s["high_revert"] == time(9, 23)           # 110*0.99=108.9 を最初に下回った終値 108.8 は 9:23
    assert round(s["gap_pct"], 2) == round((100 / 98 - 1) * 100, 2)
    print("✅ test_summarize_breakout_up_and_revert")


def test_summarize_inside_and_no_revert():
    closes = [100, 102, 98, 100] + [100] * 10 + [101, 99]
    s = summarize_day(_bars("2026-11-09", closes))
    assert s["brk"] == "内側" and s["brk_time"] is None
    assert s["gap_pct"] is None
    print("✅ test_summarize_inside_and_no_revert")


def test_summarize_missing_first_ten():
    s = summarize_day(_bars("2026-11-09", [100, 101, 102], start="09:15"))
    assert s["brk"] == "データなし" and s["hi10"] is None
    print("✅ test_summarize_missing_first_ten")


def test_prev_closes():
    idx = pd.to_datetime(["2026-11-05", "2026-11-06", "2026-11-09"]).tz_localize(JST)
    d = pd.DataFrame({"Close": [10.0, 11.0, 12.0]}, index=idx)
    pc = prev_closes(d)
    assert pc == {date(2026, 11, 6): 10.0, date(2026, 11, 9): 11.0}
    print("✅ test_prev_closes")


if __name__ == "__main__":
    test_split_days_keeps_morning_only()
    test_summarize_breakout_up_and_revert()
    test_summarize_inside_and_no_revert()
    test_summarize_missing_first_ten()
    test_prev_closes()
    print("All morning_chart tests passed")
