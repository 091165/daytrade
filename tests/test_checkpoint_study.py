"""
checkpoint_study のユニットテスト
"""

from __future__ import annotations

import sys
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from checkpoint_study import (
    checkpoint_prices,
    pooled_stats,
    seg_label,
    segment_frame,
    stock_stats,
    us_prev_move,
)

JST = ZoneInfo("Asia/Tokyo")


def _market(n_days: int = 60, seed: int = 0, follow: float = 0.0, us_beta: float = 0.0):
    """合成の日足・5分足・米国株。follow>0 なら 寄り→9:15 の動きが 9:15→10:00 にも続く。
    us_beta>0 なら 寄り→9:15 が米国株の前日の動きに連動する。9:00 の5分足はわざと欠けさせる。"""
    rng = np.random.default_rng(seed)
    days = pd.bdate_range("2026-07-01", periods=n_days)
    us_close = pd.Series(100 * np.exp(rng.normal(0, 0.01, n_days + 1).cumsum()),
                         index=pd.bdate_range("2026-06-30", periods=n_days + 1))
    us_ret = us_close.pct_change() * 100
    bars, daily = [], []
    prev = 1000.0
    for i, d in enumerate(days):
        o = prev * (1 + rng.normal(0, 0.01))
        us_prev = us_ret.iloc[i] if i > 0 and not np.isnan(us_ret.iloc[i]) else 0.0
        # 節目ごとに独立した動き（ランダムウォーク）。指定したつながりだけを入れる
        p905 = o * (1 + rng.normal(0, 0.003) + us_beta * us_prev / 200)
        p915 = p905 * (1 + rng.normal(0, 0.003) + us_beta * us_prev / 200)
        drift = follow * (p915 / o - 1) / 2
        p930 = p915 * (1 + rng.normal(0, 0.003) + drift)
        p1000 = p930 * (1 + rng.normal(0, 0.003) + drift)
        path = {"09:05": p905, "09:15": p915, "09:30": p930, "10:00": p1000}
        for hm in pd.date_range(f"{d.date()} 09:05", f"{d.date()} 10:25", freq="5min"):
            key = hm.strftime("%H:%M")
            px = path.get(key, p1000 if key > "10:00" else (p915 if key < "09:30" else path["09:30"]))
            bars.append((hm.tz_localize(JST).tz_convert("UTC"), px, px))
        close = p1000 * (1 + rng.normal(0, 0.01))
        daily.append((d, o, close))
        prev = close
    m5 = pd.DataFrame(bars, columns=["t", "Open", "Close"]).set_index("t")
    d1 = pd.DataFrame(daily, columns=["d", "Open", "Close"]).set_index("d")
    return m5, d1, us_close


def test_prices_use_daily_open_and_bar_opens():
    m5, d1, _ = _market(5)
    p = checkpoint_prices(m5, d1)
    assert len(p) == 4                                   # 初日は前日終値がない
    day = p.index[1]
    assert p.at[day, "open"] == d1.at[day, "Open"]       # 寄り値は日足の始値（9:00 の5分足がなくても）
    assert not np.isnan(p.at[day, "09:05"]) and not np.isnan(p.at[day, "10:00"])
    print("✅ test_prices_use_daily_open_and_bar_opens")


def test_segment_frame_columns():
    m5, d1, us_close = _market(10)
    p = checkpoint_prices(m5, d1)
    frames = segment_frame(p, us_prev_move({"X": us_close}, ("X",), p.index))
    assert set(map(seg_label, frames)) == {"寄り→09:05", "寄り→09:15", "寄り→09:30", "寄り→10:00",
                                          "09:05→10:00", "09:15→10:00", "09:30→10:00"}
    assert "so_far" not in frames[("open", "09:15")]
    assert "so_far" in frames[("09:15", "10:00")]
    print("✅ test_segment_frame_columns")


def test_detects_follow_through_and_us_link():
    frames_all = {}
    stats = None
    for k in range(6):
        m5, d1, us_close = _market(60, seed=k, follow=0.8, us_beta=0.6)
        p = checkpoint_prices(m5, d1)
        frames = segment_frame(p, us_prev_move({"X": us_close}, ("X",), p.index))
        frames_all[f"S{k}"] = frames
        if k == 0:
            stats = stock_stats(frames, 1000.0)
    follow = stats["09:15→10:00|so_far"]
    assert follow["r"] > 0.3 and follow["hit"] > 0.6 and follow["edge_yen"] > 0
    pooled = {(o["seg"], o["key"]): o for o in pooled_stats(frames_all)}
    assert pooled[("09:15→10:00", "so_far")]["significant"]
    assert pooled[("寄り→09:15", "us")]["significant"]
    assert not pooled[("寄り→09:15", "prev")]["significant"]       # 前日の動きは無関係にしてある
    print("✅ test_detects_follow_through_and_us_link")


def test_noise_is_not_significant():
    frames_all = {}
    for k in range(6):
        m5, d1, us_close = _market(60, seed=100 + k)
        p = checkpoint_prices(m5, d1)
        frames_all[f"S{k}"] = segment_frame(p, us_prev_move({"X": us_close}, ("X",), p.index))
    assert not any(o["significant"] for o in pooled_stats(frames_all))
    print("✅ test_noise_is_not_significant")


if __name__ == "__main__":
    test_prices_use_daily_open_and_bar_opens()
    test_segment_frame_columns()
    test_detects_follow_through_and_us_link()
    test_noise_is_not_significant()
    print("\n🎉 All checkpoint_study tests passed!")
