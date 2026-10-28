"""
archive のユニットテスト
"""

from __future__ import annotations

import json
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from archive import backfill_from_history, save_daily_archive, write_archive_index

JST = ZoneInfo("Asia/Tokyo")


def t(day: int, hh: int, mm: int) -> datetime:
    return datetime(2026, 10, day, hh, mm, tzinfo=JST)


def _signal(ts: datetime, verdict: str = "buy_bias") -> dict:
    return {
        "market": {"timestamp": ts.isoformat(), "focus_ohlc": {"5803": [1, 2, 3]}},
        "direction": {"verdict": verdict, "verdict_label": "買いポジ主体", "confidence": "high"},
        "trading_rules": {"day_type": "trend_up", "checks": {"direction": "buy"}},
        "generated_after_open": ts.hour >= 9,
    }


def _load(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def test_save_writes_dated_file_without_ohlc():
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        assert save_daily_archive(d, _signal(t(28, 7, 10)), t(28, 7, 10))
        data = _load(d / "2026-10-28.json")
        assert data["date"] == "2026-10-28"
        assert data["reconstructed"] is False
        assert data["trading_rules"]["checks"]["direction"] == "buy"
        assert "focus_ohlc" not in data["market"]
    print("✅ test_save_writes_dated_file_without_ohlc")


def test_late_version_does_not_overwrite_preopen_version():
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        save_daily_archive(d, _signal(t(28, 7, 10), "buy_bias"), t(28, 7, 10))
        assert not save_daily_archive(d, _signal(t(28, 20, 50), "sell_bias"), t(28, 20, 50))
        assert _load(d / "2026-10-28.json")["direction"]["verdict"] == "buy_bias"
    print("✅ test_late_version_does_not_overwrite_preopen_version")


def test_preopen_version_may_refresh_and_replace_late_one():
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        save_daily_archive(d, _signal(t(28, 6, 40), "hands_off"), t(28, 6, 40))
        assert save_daily_archive(d, _signal(t(28, 7, 40), "buy_bias"), t(28, 7, 40))
        assert _load(d / "2026-10-28.json")["direction"]["verdict"] == "buy_bias"

        # 寄り前に間に合わなかった日は、寄り後の版でも残す（その後の版で更新してよい）
        save_daily_archive(d, _signal(t(29, 10, 0), "hands_off"), t(29, 10, 0))
        assert save_daily_archive(d, _signal(t(29, 11, 0), "sell_bias"), t(29, 11, 0))
        assert _load(d / "2026-10-29.json")["direction"]["verdict"] == "sell_bias"
    print("✅ test_preopen_version_may_refresh_and_replace_late_one")


def test_backfill_fills_missing_days_only():
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        save_daily_archive(d, _signal(t(28, 7, 10), "buy_bias"), t(28, 7, 10))
        history = [
            {"date": "2026-10-27", "verdict": "sell_bias", "verdict_label": "売りポジ主体",
             "confidence": "low", "futures_diff": -1200.0, "warnings": ["⚠️ x"]},
            {"date": "2026-10-28", "verdict": "hands_off", "verdict_label": "初心者手出し無用",
             "confidence": "low", "futures_diff": 0.0, "warnings": []},
        ]
        assert backfill_from_history(d, history) == 1
        filled = _load(d / "2026-10-27.json")
        assert filled["reconstructed"] is True
        assert filled["trading_rules"]["day_type"] == "trend_down"
        assert filled["trading_rules"]["checks"]["direction"] == "sell"
        assert filled["warnings"] == [{"label": "⚠️ x"}]
        assert _load(d / "2026-10-28.json")["direction"]["verdict"] == "buy_bias"  # 既存は上書きしない
        assert backfill_from_history(d, history) == 0
    print("✅ test_backfill_fills_missing_days_only")


def test_index_lists_dates_in_order():
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        save_daily_archive(d, _signal(t(28, 10, 5)), t(28, 10, 5))
        backfill_from_history(d, [{"date": "2026-10-27", "verdict": "hands_off", "confidence": "low"}])
        entries = write_archive_index(d)
        assert [e["date"] for e in entries] == ["2026-10-27", "2026-10-28"]
        assert entries[0]["reconstructed"] is True
        assert entries[1]["generated_after_open"] is True
        assert _load(d / "index.json") == entries
    print("✅ test_index_lists_dates_in_order")


if __name__ == "__main__":
    test_save_writes_dated_file_without_ohlc()
    test_late_version_does_not_overwrite_preopen_version()
    test_preopen_version_may_refresh_and_replace_late_one()
    test_backfill_fills_missing_days_only()
    test_index_lists_dates_in_order()
    print("\n🎉 All archive tests passed!")
