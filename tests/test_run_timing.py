"""
run_timing のユニットテスト
"""

from __future__ import annotations

import json
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from run_timing import is_after_open, late_notice, read_existing_timestamp, should_skip_scheduled_run

JST = ZoneInfo("Asia/Tokyo")


def t(day: int, hh: int, mm: int) -> datetime:
    return datetime(2026, 10, day, hh, mm, tzinfo=JST)


def test_is_after_open():
    assert not is_after_open(t(29, 8, 59))
    assert is_after_open(t(29, 9, 0))
    print("✅ test_is_after_open")


def test_late_schedule_does_not_overwrite_preopen_version():
    # 7:07 に寄り前の版ができていて、7:37 の分が 10:12 に遅れて動いた
    assert should_skip_scheduled_run("schedule", t(29, 10, 12), t(29, 7, 8)) is True
    print("✅ test_late_schedule_does_not_overwrite_preopen_version")


def test_preopen_schedule_may_refresh():
    # 寄り前どうしなら、新しい情報で上書きしてよい
    assert should_skip_scheduled_run("schedule", t(29, 7, 40), t(29, 6, 40)) is False
    print("✅ test_preopen_schedule_may_refresh")


def test_late_schedule_without_today_preopen_version_generates():
    # 3回とも遅れた日: 既存は前日の版 → 遅れても生成する（注意書き付き）
    assert should_skip_scheduled_run("schedule", t(29, 9, 30), t(28, 7, 10)) is False
    # 今日の版があっても、それ自体が寄り付き後に作られたものなら更新してよい
    assert should_skip_scheduled_run("schedule", t(29, 11, 0), t(29, 9, 30)) is False
    # 既存の版がない／読めない
    assert should_skip_scheduled_run("schedule", t(29, 9, 30), None) is False
    print("✅ test_late_schedule_without_today_preopen_version_generates")


def test_manual_and_push_always_generate():
    for ev in ("workflow_dispatch", "push", ""):
        assert should_skip_scheduled_run(ev, t(29, 10, 12), t(29, 7, 8)) is False
    print("✅ test_manual_and_push_always_generate")


def test_late_notice_text():
    assert late_notice(t(29, 8, 50)) is None
    msg = late_notice(t(29, 10, 29))
    assert msg and "10:29" in msg and "寄り付き後" in msg
    print("✅ test_late_notice_text")


def test_read_existing_timestamp():
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "signal.json"
        assert read_existing_timestamp(p) is None                 # ファイルなし
        p.write_text("{broken", encoding="utf-8")
        assert read_existing_timestamp(p) is None                 # 壊れたJSON
        p.write_text(json.dumps({"market": {}}), encoding="utf-8")
        assert read_existing_timestamp(p) is None                 # キーなし
        p.write_text(json.dumps({"market": {"timestamp": "2026-10-29T07:08:12+09:00"}}), encoding="utf-8")
        ts = read_existing_timestamp(p)
        assert ts == t(29, 7, 8).replace(second=12)
    print("✅ test_read_existing_timestamp")


def test_cron_times_are_preopen_and_off_peak():
    """ワークフローの定期実行が、寄り前・00分/30分ちょうどを避けた時刻になっていること"""
    import re
    wf = (Path(__file__).resolve().parent.parent / ".github/workflows/daily_signal.yml").read_text(encoding="utf-8")
    crons = re.findall(r'cron:\s*"([^"]+)"', wf)
    assert len(crons) >= 2
    for c in crons:
        minute, hour, _, _, dow = c.split()
        jst_hour = (int(hour) + 9) % 24
        assert int(minute) not in (0, 30), c
        assert 6 <= jst_hour < 9, c                                # JSTで6時台〜8時台
        assert dow == "0-4", c                                     # UTCの日〜木 = JSTの月〜金の朝
    print("✅ test_cron_times_are_preopen_and_off_peak")


if __name__ == "__main__":
    test_is_after_open()
    test_late_schedule_does_not_overwrite_preopen_version()
    test_preopen_schedule_may_refresh()
    test_late_schedule_without_today_preopen_version_generates()
    test_manual_and_push_always_generate()
    test_late_notice_text()
    test_read_existing_timestamp()
    test_cron_times_are_preopen_and_off_peak()
    print("\n🎉 All run_timing tests passed!")
