"""
fib_study のユニットテスト
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from fib_study import _resolve, gap_events, summarize_pattern, swing_events


def _bars(day: str, path: list[float], minutes: int = 5) -> pd.DataFrame:
    """終値が path をたどる足。各足の高値・安値は前の終値と今の終値の範囲。"""
    idx = pd.date_range(f"{day} 09:00", periods=len(path), freq=f"{minutes}min")
    prev = [path[0]] + path[:-1]
    return pd.DataFrame({"High": np.maximum(prev, path), "Low": np.minimum(prev, path), "Close": path}, index=idx)


def test_resolve_bounce_break_and_untouched():
    down = _bars("2026-10-01", [110, 105, 100.5, 103, 106])           # 100 に触れずに反発…ではなく 100.5 で止まる
    assert _resolve(down, 100, 3, down=True) is None                   # 触れていない
    bounce = _bars("2026-10-01", [110, 104, 99.5, 102, 104])
    assert _resolve(bounce, 100, 3, down=True) == "bounce"
    brk = _bars("2026-10-01", [110, 104, 99.5, 98, 96])
    assert _resolve(brk, 100, 3, down=True) == "break"
    through = _bars("2026-10-01", [110, 95, 104])                      # 触れた足でもう判定幅を抜けている
    assert _resolve(through, 100, 3, down=True) == "break"
    up_side = _bars("2026-10-01", [90, 96, 100.5, 98, 96])             # 下から上がってきて触れて戻る
    assert _resolve(up_side, 100, 3, down=False) == "bounce"
    print("✅ test_resolve_bounce_break_and_untouched")


def test_gap_events_levels_and_direction():
    # 前日終値 1000 → 寄り 1100（窓 +100）。半値 1050 まで下げて反発
    path = [1100, 1080, 1060, 1050, 1070, 1090, 1095]
    ev = gap_events(_bars("2026-10-01", path), 1100.0, 1000.0)
    by = {e["level"]: e["result"] for e in ev}
    assert by[0.5] == "bounce"
    assert 0.618 not in by and 0.7 not in by                           # 1038・1030 には触れていない
    assert all(e["pattern"] == "gap" for e in ev)
    assert gap_events(_bars("2026-10-01", path), 1100.0, 1095.0) == []   # 窓が小さい日は対象外
    # 窓を下に開けた日は、上に戻る方向で判定
    down = gap_events(_bars("2026-10-01", [900, 930, 950, 930, 910]), 900.0, 1000.0)
    assert {e["level"]: e["result"] for e in down}[0.5] == "bounce"
    print("✅ test_gap_events_levels_and_direction")


def test_swing_events_after_first_30min():
    # 9:00〜9:25 で 1000→1100 に上昇（9:30時点で高値側）、その後 61.8%（1038.2）まで押して反発
    first = [1000, 1020, 1040, 1060, 1080, 1100]
    after = [1090, 1070, 1050, 1038, 1060, 1080]
    ev = swing_events(_bars("2026-10-01", first + after))
    by = {e["level"]: e["result"] for e in ev}
    assert by[0.618] == "bounce"
    assert 0.7 not in by
    print("✅ test_swing_events_after_first_30min")


def _sim_events(n_days: int, fib_edge: float, seed: int) -> pd.DataFrame:
    """フィボの水準の反発率だけ fib_edge 高くした合成イベント。"""
    rng = np.random.default_rng(seed)
    rows = []
    for d in range(n_days):
        day_shift = rng.normal(0, 0.1)                                 # 同じ日の水準どうしの連動
        for k in (0.3, 0.382, 0.45, 0.5, 0.55, 0.618, 0.7):
            fib = k in (0.382, 0.5, 0.618)
            p = 0.5 + day_shift + (fib_edge if fib else 0)
            rows.append({"code": "X", "date": d, "level": k, "fib": fib,
                         "result": "bounce" if rng.random() < p else "break"})
    return pd.DataFrame(rows)


def test_summarize_detects_edge_and_not_noise():
    s = summarize_pattern(_sim_events(300, 0.15, 1))
    assert s["significant"] and s["diff"] > 0.08 and s["ci"][0] > 0
    assert len(s["levels"]) == 7
    fp = sum(summarize_pattern(_sim_events(300, 0.0, 10 + k))["significant"] for k in range(10))
    assert fp <= 2
    print("✅ test_summarize_detects_edge_and_not_noise")


if __name__ == "__main__":
    test_resolve_bounce_break_and_untouched()
    test_gap_events_levels_and_direction()
    test_swing_events_after_first_30min()
    test_summarize_detects_edge_and_not_noise()
    print("\n🎉 All fib_study tests passed!")
