"""
round_study のユニットテスト（大台の初回タッチ・節目を少し割ったところ）
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from round_study import (approach_episodes, big_number_note, big_number_study, big_numbers, boot_diff, near_big_number, day_dip_events, day_touch_events, dip_card_note, dip_result,
                         summarize_dip, summarize_touch, touch_events)


def _bars(path: list[float]) -> pd.DataFrame:
    """終値が path をたどる足（高値・安値は前の終値と今の終値の範囲）"""
    prev = [path[0]] + path[:-1]
    return pd.DataFrame({"Open": prev, "High": np.maximum(prev, path), "Low": np.minimum(prev, path), "Close": path})


def test_touch_count_and_approach():
    # 上から 27,000 に触れて反発 → 離れる → もう一度触れて抜ける
    b = _bars([27200, 27100, 27000, 27080, 27150, 27050, 27000, 26950, 26900])
    ev = touch_events(b, 27000, 40)
    assert [e["touch"] for e in ev] == [1, 2]
    assert ev[0]["approach"] == "support" and ev[0]["result"] == "bounce"
    assert ev[1]["result"] == "break"
    # 下から上がってきて触れたらレジスタンス
    up = touch_events(_bars([26800, 26900, 27000, 26930, 26900]), 27000, 40)
    assert up[0]["approach"] == "resistance" and up[0]["result"] == "bounce"
    # 離れないうちの触れ直しは数えない
    stay = touch_events(_bars([27100, 27000, 27010, 26995, 27005, 27100]), 27000, 40)
    assert len(stay) == 1
    print("✅ test_touch_count_and_approach")


def test_day_touch_levels_and_controls():
    b = _bars([26800, 27100, 27600, 27400])
    ev = day_touch_events(b)
    levels = {(e["level"], e["big"]) for e in ev}
    assert (27000.0, True) in levels and (27500.0, False) in levels
    assert len({(e["level"], e["touch"], e["approach"]) for e in ev}) == len(ev)        # 重複なし
    print("✅ test_day_touch_levels_and_controls")


def test_dip_result():
    assert dip_result(_bars([4050, 4010, 3990, 3985, 4005, 4020]), 4000) == "held"      # 0.5%（20円）以内で戻った
    assert dip_result(_bars([4050, 4010, 3990, 3970, 4005]), 4000) == "failed"          # 3980 より下へ
    assert dip_result(_bars([4050, 4020, 4010]), 4000) is None                          # 割っていない
    assert dip_result(_bars([3950, 3990, 4010]), 4000) is None                          # 下から上がった
    ev = day_dip_events(_bars([4100, 4010, 3990, 4005, 3950]))
    assert any(e["round"] and e["level"] == 4000 for e in ev)
    print("✅ test_dip_result")


def _sim(n_days: int, p_a: float, p_b: float, seed: int) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    rows = []
    for d in range(n_days):
        for _ in range(2):
            rows.append({"code": "X", "date": d, "a": True, "ok": float(rng.random() < p_a)})
            rows.append({"code": "X", "date": d, "a": False, "ok": float(rng.random() < p_b)})
    return pd.DataFrame(rows)


def test_boot_diff():
    ev = _sim(150, 0.7, 0.4, 1)
    r = boot_diff(ev, "ok", ev["a"], ~ev["a"])
    assert r["significant"] and r["diff"] > 0.15 and r["ci"][0] > 0
    fp = 0
    for k in range(10):
        e = _sim(150, 0.5, 0.5, 10 + k)
        fp += boot_diff(e, "ok", e["a"], ~e["a"])["significant"]
    assert fp <= 2
    small = _sim(5, 0.7, 0.4, 2)
    assert boot_diff(small, "ok", small["a"], ~small["a"])["diff"] is None                # 件数不足
    print("✅ test_boot_diff")


def test_summaries_and_card_note():
    ev = pd.DataFrame([
        {"code": "1570", "date": 1, "verdict": "buy_bias", "touch": 1, "approach": "support", "big": True, "result": "bounce"},
        {"code": "1570", "date": 1, "verdict": "buy_bias", "touch": 2, "approach": "support", "big": True, "result": "break"},
        {"code": "1570", "date": 1, "verdict": "buy_bias", "touch": 1, "approach": "support", "big": False, "result": "break"},
        {"code": "1570", "date": 2, "verdict": "hands_off", "touch": 1, "approach": "resistance", "big": True, "result": None},
    ])
    s = summarize_touch(ev)
    assert s["n"] == 3 and s["groups"]["big_first"]["rate"] == 1.0 and s["groups"]["big_later"]["rate"] == 0.0
    assert s["by_verdict"]["buy_bias"]["support"]["first"]["n"] == 1
    d = summarize_dip(pd.DataFrame([{"code": "5803", "date": 1, "round": True, "result": "held"},
                                    {"code": "5803", "date": 1, "round": False, "result": "failed"}]))
    assert d["round"]["rate"] == 1.0 and d["control"]["rate"] == 0.0 and not d["significant"]
    # カードに出すのは有意なときだけ
    assert dip_card_note(d, 4150) is None
    note = dip_card_note({"significant": True, "diff": 0.2, "a": 0.6, "b": 0.4}, 4150)
    assert "節目 4,000円" in note["text"] and "3,980円前後" in note["text"]
    print("✅ test_summaries_and_card_note")


def test_big_number_note():
    assert 10000 in big_numbers(9800) and 5000 in big_numbers(4900) and 1000 in big_numbers(990)
    n = near_big_number(9750)
    assert n["level"] == 10000 and n["below"] and abs(n["gap_pct"] + 2.5) < 1e-9
    assert near_big_number(4300) is None                     # 5,000円まで 14%
    assert near_big_number(5100)["level"] == 5000 and not near_big_number(5100)["below"]
    note = big_number_note(9750)
    assert note["text"].startswith("大台 10,000円の手前") and "突破すれば強い" in note["text"] and "未検証" in note["text"]
    assert big_number_note(4300) is None
    print("✅ test_big_number_note")


def test_approach_episodes_and_study():
    c = pd.Series([9000, 9500, 9750, 9800, 10100, 10200, 10300, 10400, 10500, 10600])
    ev = approach_episodes(c, 10000)
    assert len(ev) == 1 and ev[0]["i"] == 2 and ev[0]["result"] == "break"        # 入り直さない限り1回
    back = pd.Series([9000, 9800, 9600, 9500, 9400, 9300, 9200, 9100])
    assert approach_episodes(back, 10000)[0]["result"] == "pushed"
    rng = np.random.default_rng(0)
    closes = {f"c{k}": pd.Series(5000 * np.exp(np.cumsum(rng.normal(0, 0.02, 500)))) for k in range(4)}
    r = big_number_study(closes)
    assert r["n"] > 0 and r["round"]["n"] > 0 and r["control"]["n"] > 0
    print("✅ test_approach_episodes_and_study")


if __name__ == "__main__":
    test_big_number_note()
    test_approach_episodes_and_study()
    test_touch_count_and_approach()
    test_day_touch_levels_and_controls()
    test_dip_result()
    test_boot_diff()
    test_summaries_and_card_note()
    print("\n🎉 All round_study tests passed!")
