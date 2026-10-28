"""
毎朝の判定を日付ごとに残すモジュール

目的:
  signal.json は毎回上書きされ、history.json は判定ラベルだけを直近60件しか持たない。
  これでは「その日にどのルールが出ていたか」を後から確かめられない。
  daytrade-journal の振り返りページで、その日のルールと実際のトレードを突き合わせるため、
  その日の判定一式を docs/archive/YYYY-MM-DD.json に残す。

構造:
  docs/archive/YYYY-MM-DD.json  その日の signal.json（重い日足データは除く）
  docs/archive/index.json       日付の一覧（GitHub Pages ではフォルダの中身を一覧できないため）

上書きの方針:
  寄り前に作られた版がある日は、寄り後に作られた版で上書きしない（run_timing と同じ考え方）。
  振り返りでは、実際にトレード前に見ていた判定と比べたいため。
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from run_timing import is_after_open, read_existing_timestamp
from trading_rules import build_trading_rules

INDEX_NAME = "index.json"


def _write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, default=str)


def save_daily_archive(archive_dir: Path, signal: dict, generated_at: datetime) -> bool:
    """その日の判定を保存する。保存したら True、寄り前の版を守って保存しなかったら False。

    Args:
        signal: signal.json と同じ内容
        generated_at: 生成時刻（JST）
    """
    path = archive_dir / f"{generated_at.date().isoformat()}.json"
    existing_ts = read_existing_timestamp(path)
    if (
        existing_ts is not None
        and existing_ts.date() == generated_at.date()
        and not is_after_open(existing_ts)
        and is_after_open(generated_at)
    ):
        return False

    market = {k: v for k, v in signal.get("market", {}).items() if k != "focus_ohlc"}
    _write_json(path, {
        "date": generated_at.date().isoformat(),
        "reconstructed": False,
        **signal,
        "market": market,
    })
    return True


def backfill_from_history(archive_dir: Path, history: list[dict]) -> int:
    """history.json にあって archive にない日を、判定ラベルからルールを組み立て直して埋める。

    当時のルールの文言・承認銘柄は残っていないため、今のルールで組み立てる（reconstructed=True）。
    既存のファイルは上書きしない。埋めた件数を返す。
    """
    added = 0
    for h in history:
        date = h.get("date")
        if not date or (archive_dir / f"{date}.json").exists():
            continue
        verdict = h.get("verdict", "hands_off")
        confidence = h.get("confidence", "medium")
        _write_json(archive_dir / f"{date}.json", {
            "date": date,
            "reconstructed": True,
            "direction": {
                "verdict": verdict,
                "verdict_label": h.get("verdict_label", ""),
                "confidence": confidence,
                "futures_diff": h.get("futures_diff"),
            },
            "warnings": [{"label": w} for w in h.get("warnings", [])],
            "trading_rules": build_trading_rules(verdict, confidence).as_dict(),
        })
        added += 1
    return added


def write_archive_index(archive_dir: Path) -> list[dict]:
    """archive 内の日付一覧を index.json に書き出す（日付昇順）。"""
    entries = []
    for path in sorted(archive_dir.glob("????-??-??.json")):
        try:
            with path.open("r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            print(f"⚠️ Failed to read archive {path.name}: {e}")
            continue
        direction = data.get("direction", {})
        entries.append({
            "date": path.stem,
            "verdict": direction.get("verdict"),
            "verdict_label": direction.get("verdict_label"),
            "confidence": direction.get("confidence"),
            "reconstructed": bool(data.get("reconstructed")),
            "generated_after_open": bool(data.get("generated_after_open")),
        })
    _write_json(archive_dir / INDEX_NAME, entries)
    return entries
