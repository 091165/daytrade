"""
intraday_study のユニットテスト
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from intraday_study import (
    day_frames,
    excursion,
    first10,
    simulate,
    summarize_trades,
    summarize_vix,
    trade_rows,
    vix_rows,
)


def _bars(day: str, path: list[float]) -> pd.DataFrame:
    idx = pd.date_range(f"{day} 09:00", periods=len(path), freq="5min")
    prev = [path[0]] + path[:-1]
    return pd.DataFrame({"Open": prev, "High": np.maximum(prev, path), "Low": np.minimum(prev, path), "Close": path}, index=idx)


def test_simulate_order_and_timeout():
    after = _bars("2026-11-02", [100, 101, 99, 106])
    assert simulate(after, 100, 1, 2, 4)["r"] == 2.0                       # 106 で利確（2R）
    assert simulate(after, 100, -1, 2, 4)["r"] == -1.0                     # 売りは 101→… 102 には届かず、106 で損切り
    both = pd.DataFrame({"Open": [100], "High": [110], "Low": [90], "Close": [100]}, index=[pd.Timestamp("2026-11-02 09:10")])
    assert simulate(both, 100, 1, 5, 5)["r"] == -1.0                       # 同じ足で両方なら損切り
    flat = _bars("2026-11-02", [100, 100.5, 100.2])
    s = simulate(flat, 100, 1, 2, 4)
    assert not s["decided"] and abs(s["r"] - 0.1) < 1e-9
    print("✅ test_simulate_order_and_timeout")


def test_day_frames_excursion_first10():
    daily = pd.DataFrame({"Open": 100.0, "High": 102.0, "Low": 98.0, "Close": 100.0},
                         index=pd.bdate_range(end="2026-10-30", periods=15))
    daily.loc[pd.Timestamp("2026-11-02")] = [100.0, 106, 97, 104]
    m5 = _bars("2026-11-02", [100, 101, 102, 101, 103, 104, 106, 105, 104, 103, 104, 104, 104])
    days = day_frames(m5, daily)
    assert len(days) == 1 and abs(days[0]["D"] - 4.0) < 1e-9               # 目安 = 4%×100円
    e = excursion(days[0])
    assert abs(e["up"] - 1.5) < 1e-9 and e["down"] == 0.0                    # 寄り100→高値106（10:00より前）
    f = first10(days[0])
    assert f["entry"] == 101 and abs(f["move"] - 0.25) < 1e-9                # 9:10 の足の始値（＝9:05の足の終値）
    t = trade_rows(days)
    assert set(t["mode"]) == {"順張り", "逆張り"} and len(t) == 8
    print("✅ test_day_frames_excursion_first10")


def test_follow_edge_detected():
    """最初の10分の向きがそのまま続く合成データでは、順張りが勝つ"""
    rng = np.random.default_rng(0)
    rows = []
    daily_idx = pd.bdate_range("2026-01-05", periods=80)
    daily = pd.DataFrame({"Open": 100.0, "High": 102.0, "Low": 98.0, "Close": 100.0}, index=daily_idx)
    m5 = []
    for day in daily_idx[15:]:
        sg = rng.choice([-1, 1])
        path = [100 + sg * 0.3 * i + rng.normal(0, 0.05) for i in range(12)]
        m5.append(_bars(str(day.date()), path))
    t = trade_rows(day_frames(pd.concat(m5), daily))
    s = summarize_trades(t)
    big = [x for x in s["by_size"] if x["follow_r"] is not None]
    assert all(x["follow_r"] > x["fade_r"] for x in big)
    print("✅ test_follow_edge_detected")


def test_vix_rows_alignment():
    daily = pd.DataFrame({"Open": 100.0, "High": 102.0, "Low": 98.0, "Close": 100.0},
                         index=pd.bdate_range("2026-01-05", periods=40))
    vix = pd.Series(np.linspace(15, 30, 40), index=pd.bdate_range("2026-01-02", periods=40))
    r = vix_rows(daily, vix)
    t = r.index[5]
    assert r.loc[t, "vix"] == vix[vix.index < t].iloc[-1]
    assert len(summarize_vix(r)) == 4
    print("✅ test_vix_rows_alignment")


def test_vix_signal_q_zero_counts_as_significant():
    from intraday_study import vix_signal
    v = {"pooled": {"vix": [{"group": "VIXが前日比+10%以上", "n": 958, "mean": 1.12, "calm_mean": 1.04, "q_vix": 0.0}]}}
    assert vix_signal(v, 15)["multiplier"] == 1.12
    assert vix_signal(v, 5) is None
    assert vix_signal(None, 15)["warning"]["evidence"] == "未検証"
    print("✅ test_vix_signal_q_zero_counts_as_significant")


if __name__ == "__main__":
    test_vix_signal_q_zero_counts_as_significant()
    test_simulate_order_and_timeout()
    test_day_frames_excursion_first10()
    test_follow_edge_detected()
    test_vix_rows_alignment()
    print("\n🎉 All intraday_study tests passed!")
