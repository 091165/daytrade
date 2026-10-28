"""
毎朝の自動実行のタイミング判定

背景:
  GitHub Actions の定期実行（schedule）は混雑で大きく遅れる。
  2026/9〜10月の実績では、7:30指定に対して遅れの中央値が約2時間15分、
  寄り付き（9:00）前に間に合った日は0回だった。

対策:
  - 実行時刻を寄り前に複数回入れる（ワークフロー側）
  - 9:00以降に遅れて動いた定期実行は、その日の寄り前の版がすでにあれば上書きしない
  - 寄り付き後に作られた版には注意書きを出す
    （先物の値に当日の値動きが含まれ、バックテストで検証した寄り前の条件と異なるため）
"""

from __future__ import annotations

import json
from datetime import datetime, time
from pathlib import Path

MARKET_OPEN = time(9, 0)


def is_after_open(ts: datetime) -> bool:
    """寄り付き（9:00 JST）以降に作られたか。ts は JST の日時。"""
    return ts.time() >= MARKET_OPEN


def read_existing_timestamp(signal_json: Path) -> datetime | None:
    """既存の signal.json に記録された生成時刻（JST）を返す。読めなければ None。"""
    try:
        with signal_json.open("r", encoding="utf-8") as f:
            return datetime.fromisoformat(json.load(f)["market"]["timestamp"])
    except (OSError, ValueError, KeyError, TypeError):
        return None


def should_skip_scheduled_run(event_name: str, now_jst: datetime, existing_ts: datetime | None) -> bool:
    """遅れて動いた定期実行で、すでに今日の寄り前の版があるなら True（上書きしない）。

    - 手動実行（workflow_dispatch）やコード変更時（push）は常に生成する
    - 寄り前の定期実行は、より新しい情報で上書きしてよい
    """
    if event_name != "schedule":
        return False
    if not is_after_open(now_jst):
        return False
    if existing_ts is None:
        return False
    same_day = existing_ts.date() == now_jst.date()
    return same_day and not is_after_open(existing_ts)


def late_notice(ts: datetime) -> str | None:
    """寄り付き後に作られた版に出す注意書き。寄り前なら None。"""
    if not is_after_open(ts):
        return None
    return (
        f"⚠️ この判定は寄り付き後（{ts.strftime('%H:%M')}）に作られました。"
        "先物の値に当日の値動きが含まれるため、検証済みの寄り前の判定とは条件が異なります。参考程度に。"
    )
