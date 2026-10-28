"""
collect_minutes・opening_drive のユニットテスト
"""

from __future__ import annotations

import sys
import tempfile
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from collect_minutes import days_to_save, load_minutes, minute_rows, write_day
from opening_drive import binom_p, drive_rows, follow_stats, summarize

JST = ZoneInfo("Asia/Tokyo")


def _bars(day: str, minutes: int, path: list[float]) -> pd.DataFrame:
    """day の 9:00 から minutes 分足で、終値が path をたどる足（始値=前の終値、最初は1000）。"""
    idx = pd.date_range(f"{day} 09:00", periods=len(path), freq=f"{minutes}min", tz=JST)
    opens = [1000.0] + path[:-1]
    return pd.DataFrame({"Open": opens, "High": np.maximum(opens, path), "Low": np.minimum(opens, path),
                         "Close": path, "Volume": 100}, index=idx)


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


def test_drive_rows_5min_and_1min():
    # 5分足: 9:10 に 1010（+1%）、9:20 に 1020.1、10:00 に 1030
    path5 = [1005, 1010, 1015, 1020.1] + [1025] * 7 + [1030]
    rows = drive_rows(_bars("2026-10-28", 5, path5), 5)
    r = rows.iloc[0]
    assert abs(r["first_09:10"] - 1.0) < 1e-9
    assert abs(r["after_09:10"] - (1030 / 1010 - 1) * 100) < 1e-9
    assert abs(r["first_09:20"] - 2.01) < 1e-9
    # 9:00 の足が欠けて 9:05 から始まる日も測る（Yahoo の5分足でよくある）
    late = _bars("2026-10-28", 5, path5).iloc[1:]
    assert len(drive_rows(late, 5)) == 1
    # 9:05 より後に始まる日は除く
    assert drive_rows(_bars("2026-10-28", 5, path5).iloc[2:], 5).empty
    # 1分足（60本）でも同じ区切りで測れる
    path1 = list(np.linspace(1001, 1030, 60))
    rows1 = drive_rows(_bars("2026-10-28", 1, path1), 1)
    assert "first_09:10" in rows1.columns and len(rows1) == 1
    print("✅ test_drive_rows_5min_and_1min")


def test_follow_stats_detects_continuation():
    rng = np.random.default_rng(3)
    n = 120
    first = rng.normal(0, 1, n)
    df = pd.DataFrame({"first_09:10": first, "after_09:10": 0.5 * first + rng.normal(0, 0.5, n),
                       "first_09:20": first, "after_09:20": rng.normal(0, 1, n)})
    s = follow_stats(df, "09:10", price=5000)
    assert s["follow_rate"] > 0.7 and s["mean_pct"] > 0 and s["mean_yen"] > 0
    noise = follow_stats(df, "09:20")
    assert 0.35 < noise["follow_rate"] < 0.65
    per = {"A": {"name": "A", "09:10": s, "09:20": noise}}
    pooled = summarize(per)
    assert per["A"]["09:10"]["significant"] and not per["A"]["09:20"]["significant"]
    assert pooled["09:10"]["p"] < 0.01
    print("✅ test_follow_stats_detects_continuation")


def test_binom_p():
    assert binom_p(50, 100) == 1.0
    assert binom_p(70, 100) < 0.001
    print("✅ test_binom_p")


if __name__ == "__main__":
    test_minute_rows_keeps_morning_only()
    test_days_to_save_skips_existing_and_unfinished_today()
    test_write_and_load_roundtrip()
    test_drive_rows_5min_and_1min()
    test_follow_stats_detects_continuation()
    test_binom_p()
    print("\n🎉 All minutes tests passed!")
