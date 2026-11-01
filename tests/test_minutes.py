"""
collect_minutes・yahoo_chart のユニットテスト
"""

from __future__ import annotations

import sys
import tempfile
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from collect_minutes import days_to_save, load_minutes, minute_rows, write_day
from yahoo_chart import chart_to_frame

JST = ZoneInfo("Asia/Tokyo")


def test_minute_rows_keeps_morning_only():
    idx = pd.date_range("2026-10-28 08:59", "2026-10-28 10:31", freq="1min", tz=JST).tz_convert("UTC")
    df = pd.DataFrame({"Open": 1.0, "High": 1.0, "Low": 1.0, "Close": 1.0, "Volume": 5}, index=idx)
    rows = minute_rows(df, "5803")
    assert list(rows) == [date(2026, 10, 28)]
    times = [r[1] for r in rows[date(2026, 10, 28)]]
    assert times[0] == "09:00" and times[-1] == "10:29" and len(times) == 90
    print("✅ test_minute_rows_keeps_morning_only")


def test_days_to_save_skips_existing_and_unfinished_today():
    avail = {date(2026, 10, 27), date(2026, 10, 28)}
    assert days_to_save(avail, {date(2026, 10, 27)}, datetime(2026, 10, 28, 12, 0, tzinfo=JST)) == []
    assert days_to_save(avail, {date(2026, 10, 27)}, datetime(2026, 10, 28, 15, 47, tzinfo=JST)) == [date(2026, 10, 28)]
    assert days_to_save(avail, set(), datetime(2026, 10, 29, 8, 0, tzinfo=JST)) == sorted(avail)
    print("✅ test_days_to_save_skips_existing_and_unfinished_today")


def test_write_and_load_roundtrip():
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        write_day(d / "2026-10-28.csv", [["6981", "09:01", 1, 2, 0.5, 1.5, 10], ["5803", "09:00", 1, 1, 1, 1, 0]])
        df = load_minutes(d)
        assert list(df["code"]) == ["5803", "6981"]            # コードは文字列のまま・並べ替え済み
        assert df["date"].iloc[0] == pd.Timestamp("2026-10-28")
    print("✅ test_write_and_load_roundtrip")


def test_chart_to_frame():
    payload = {"chart": {"result": [{
        "timestamp": [1792972800, 1792972860, 1792972920],          # 2026-10-26 09:00 / 09:01 / 09:02 JST
        "indicators": {"quote": [{"open": [6175, 6178, None], "high": [6228, 6198, None],
                                  "low": [6168, 6159, None], "close": [6180, 6182, None],
                                  "volume": [18900800, 108000, None]}]},
    }]}}
    df = chart_to_frame(payload)
    assert len(df) == 2                                              # 値のない足は除く
    assert df.index[0].tz_convert(JST).strftime("%H:%M") == "09:00"
    rows = minute_rows(df, "6981")
    assert rows[date(2026, 10, 26)][0][:3] == ["6981", "09:00", 6175.0]
    assert chart_to_frame({"chart": {"result": None}}).empty
    print("✅ test_chart_to_frame")


def test_add_missing_codes():
    import tempfile
    from collect_minutes import add_missing_codes, load_minutes, write_day
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "2026-10-30.csv"
        write_day(path, [["1570", "09:05", 1, 2, 0.5, 1.5, 10]])
        added = add_missing_codes(path, [["1570", "09:05", 9, 9, 9, 9, 9], ["N225", "09:05", 50000, 50100, 49900, 50050, 0]])
        assert added == ["N225"]
        df = load_minutes(Path(tmp))
        assert sorted(df["code"]) == ["1570", "N225"] and float(df[df["code"] == "1570"]["open"].iloc[0]) == 1
        assert add_missing_codes(path, [["N225", "09:06", 1, 1, 1, 1, 0]]) == []
    print("✅ test_add_missing_codes")


if __name__ == "__main__":
    test_add_missing_codes()
    test_minute_rows_keeps_morning_only()
    test_days_to_save_skips_existing_and_unfinished_today()
    test_write_and_load_roundtrip()
    test_chart_to_frame()
    print("\n🎉 All minutes tests passed!")
