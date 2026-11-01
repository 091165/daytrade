"""
trade_plan・level_study のユニットテスト
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from level_study import day_events, summarize
from trade_plan import bracket, build_trade_plan, level_evidence, nearest_levels, price_levels, round_steps


def _daily(n=60, price=3900.0, seed=0):
    rng = np.random.default_rng(seed)
    c = price * np.exp(rng.normal(0, 0.01, n).cumsum())
    o = c * (1 + rng.normal(0, 0.003, n))
    idx = pd.bdate_range(end="2026-10-30", periods=n)
    return pd.DataFrame({"Open": o, "High": np.maximum(o, c) * 1.02, "Low": np.minimum(o, c) * 0.98, "Close": c}, index=idx)


def test_bracket_fujikura_sbg_murata():
    f = bracket(120.0)                        # フジクラ：9時台 約120円
    assert f["stop_width"] == 40 and f["target_width"] == 120 and f["stop_yen"] == 4000 and f["target_yen"] == 12000
    assert f["reachable"] and not f["capped"] and f["notes"] == []
    s = bracket(152.0)                        # SBG：50円で上限
    assert s["stop_width"] == 50 and s["target_yen"] == 15000 and s["capped"] and s["reachable"]
    m = bracket(217.0)                        # 村田：損切りが近い
    assert m["capped"] and any("引っかかりやすい" in x for x in m["notes"])
    e = bracket(120.0, earnings_today=True, earnings_ratio=2.6)
    assert e["skip"] and any("見送り" in x for x in e["notes"])
    small = bracket(90.0)
    assert small["stop_width"] == 30 and small["reachable"]
    assert bracket(None) is None
    print("✅ test_bracket_fujikura_sbg_murata")


def test_levels_from_completed_bars():
    d = _daily()
    lv = price_levels(d, 200.0)
    pc = float(d["Close"].iloc[-1])
    assert all(abs(x["price"] - pc) <= 200 for x in lv)
    kinds = " ".join(x["kind"] for x in lv)
    assert "前日高値" in kinds and "前日安値" in kinds
    assert any(x["price"] % 100 == 0 for x in lv)                 # キリ（100円刻み）
    near = nearest_levels(lv)
    assert all(x["side"] == "up" for x in near["up"]) and len(near["up"]) <= 3
    assert near["up"] == sorted(near["up"], key=lambda x: x["dist"])
    assert round_steps(3900) == (100, 500) and round_steps(6300) == (100, 1000) and round_steps(34000) == (500, 5000)
    print("✅ test_levels_from_completed_bars")


def test_build_trade_plan_uses_as_of():
    d = _daily()
    rng = {"available": True, "pred_yen": 200.0, "as_of": str(d.index[-2].date())}
    plan = build_trade_plan(d, rng, {"available": True, "morning_yen": 120.0})
    assert plan["prev_close"] == float(d["Close"].iloc[-2])        # as_of より後の足は使わない
    assert plan["bracket"]["stop_yen"] == 4000
    assert "未検証" in plan["level_evidence"]
    v = {"source": "x", "control_bounce": 0.5, "kinds": [{"kind": "前日高値", "bounce": 0.62, "significant": True, "diff": 0.12}]}
    assert "前日高値 62%" in level_evidence(v)
    v2 = {"source": "x", "control_bounce": 0.43, "kinds": [{"kind": "5日高値", "bounce": 0.32, "significant": True, "diff": -0.11},
                                                          {"kind": "キリ", "bounce": 0.43, "significant": False, "diff": 0.0}]}
    assert "抜けやすい水準 5日高値" in level_evidence(v2)
    print("✅ test_build_trade_plan_uses_as_of")


def test_level_study_detects_bounce():
    d = _daily(40, 1000.0)
    day = pd.Timestamp("2026-11-02")
    prev_high = float(d["High"].iloc[-1])
    o = prev_high - 30
    path = [o, prev_high - 10, prev_high + 1, prev_high - 15, prev_high - 25, prev_high - 30]   # 前日高値に触れて押し戻される
    idx = pd.date_range(f"{day.date()} 09:00", periods=len(path), freq="5min")
    prev = [path[0]] + path[:-1]
    bars = pd.DataFrame({"Open": prev, "High": np.maximum(prev, path), "Low": np.minimum(prev, path), "Close": path}, index=idx)
    ev = day_events(d, bars, day)
    assert any(e["kind"] == "前日高値" and e["result"] == "bounce" for e in ev)
    rows = []
    rng = np.random.default_rng(0)
    for k in range(200):                                          # 前日高値だけ止まりやすい合成データ
        rows.append({"code": "X", "date": k, "kind": "前日高値", "result": "bounce" if rng.random() < 0.75 else "break"})
        for _ in range(3):
            rows.append({"code": "X", "date": k, "kind": "比較用", "result": "bounce" if rng.random() < 0.5 else "break"})
    s = summarize(pd.DataFrame(rows))
    ph = [k for k in s["kinds"] if k["kind"] == "前日高値"][0]
    assert ph["significant"] and ph["diff"] > 0.15
    assert not [k for k in s["kinds"] if k["kind"] == "前日安値"][0]["significant"]
    print("✅ test_level_study_detects_bounce")


if __name__ == "__main__":
    test_bracket_fujikura_sbg_murata()
    test_levels_from_completed_bars()
    test_build_trade_plan_uses_as_of()
    test_level_study_detects_bounce()
    print("\n🎉 All trade_plan tests passed!")
