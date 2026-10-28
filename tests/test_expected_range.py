"""
expected_range のユニットテスト（ネットワーク不要）
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from expected_range import (
    RANGE_WINDOW,
    STOP_YEN,
    build_range_signal,
    completed_bars,
    daily_range_pct,
    ohlc_to_records,
    predicted_vs_actual,
    records_to_ohlc,
    validate_range_prediction,
)


def _flat(n=40, close=1000.0, width_pct=2.0, end="2026-10-27"):
    """毎日 終値=close、値幅=width_pct% の日足"""
    idx = pd.bdate_range(end=end, periods=n)
    half = close * width_pct / 100 / 2
    return pd.DataFrame({"Open": close, "High": close + half, "Low": close - half, "Close": close}, index=idx)


def test_daily_range_pct_uses_prev_close():
    idx = pd.bdate_range("2026-10-01", periods=3)
    df = pd.DataFrame({"Open": [100, 110, 100], "High": [101, 115, 104], "Low": [99, 105, 98], "Close": [100, 110, 100]},
                      index=idx, dtype=float)
    r = daily_range_pct(df)
    assert len(r) == 2                          # 初日は前日終値がないので除外
    assert abs(r.iloc[0] - 10.0) < 1e-9         # (115-105)/100
    assert abs(r.iloc[1] - (6 / 110 * 100)) < 1e-9
    print("✅ test_daily_range_pct_uses_prev_close")


def test_completed_bars_drops_today_and_later():
    df = _flat(n=10, end="2026-10-28")
    out = completed_bars(df, date(2026, 10, 28))
    assert out.index.max() == pd.Timestamp("2026-10-27")
    print("✅ test_completed_bars_drops_today_and_later")


def test_build_range_signal_converts_to_yen():
    df = _flat(n=40, close=4000.0, width_pct=2.5)
    rg = build_range_signal(df, today=date(2026, 10, 28))
    assert rg["available"]
    assert abs(rg["pred_pct"] - 2.5) < 1e-9
    assert abs(rg["pred_yen"] - 100.0) < 1e-6           # 4000円の2.5% = 100円
    assert abs(rg["lot_yen"] - 10_000.0) < 1e-4         # 100株
    assert abs(rg["stop_width"] - STOP_YEN / 100) < 1e-9  # 50円
    assert abs(rg["stop_frac"] - 0.5) < 1e-9
    assert rg["level"] == "normal"
    assert rg["rough_yen"] is None                       # 検証なしなら荒れた日の幅は出さない
    print("✅ test_build_range_signal_converts_to_yen")


def test_partial_today_bar_does_not_change_result():
    """自動実行が場中にずれ込んで、未完成の今日の足が混ざっても結果が変わらない"""
    df = _flat(n=40, close=4000.0, width_pct=2.5, end="2026-10-27")
    partial = pd.DataFrame({"Open": [4000.0], "High": [4400.0], "Low": [3600.0], "Close": [4300.0]},
                           index=[pd.Timestamp("2026-10-28")])
    a = build_range_signal(df, today=date(2026, 10, 28))
    b = build_range_signal(pd.concat([df, partial]), today=date(2026, 10, 28))
    assert a["pred_yen"] == b["pred_yen"] and a["as_of"] == b["as_of"] == "2026-10-27"
    print("✅ test_partial_today_bar_does_not_change_result")


def test_level_high_and_low():
    base = _flat(n=70, close=1000.0, width_pct=2.0, end="2026-10-14")
    hot = _flat(n=RANGE_WINDOW, close=1000.0, width_pct=4.0, end="2026-10-27")
    rg = build_range_signal(pd.concat([base, hot]), today=date(2026, 10, 28))
    assert rg["level"] == "high" and "大きい" in rg["level_text"]
    calm = _flat(n=RANGE_WINDOW, close=1000.0, width_pct=1.0, end="2026-10-27")
    rg2 = build_range_signal(pd.concat([base, calm]), today=date(2026, 10, 28))
    assert rg2["level"] == "low"
    print("✅ test_level_high_and_low")


def test_rough_day_uses_validation_ratio():
    df = _flat(n=40, close=4000.0, width_pct=2.5)
    rg = build_range_signal(df, today=date(2026, 10, 28), validation={"rough_ratio": 1.4, "r": 0.5})
    assert abs(rg["rough_yen"] - 140.0) < 1e-6
    print("✅ test_rough_day_uses_validation_ratio")


def test_not_enough_data():
    assert build_range_signal(None, today=date(2026, 10, 28))["available"] is False
    rg = build_range_signal(_flat(n=5), today=date(2026, 10, 28))
    assert rg["available"] is False and "不足" in rg["reason"]
    print("✅ test_not_enough_data")


def _regime_ohlc(n=300, seed=0, clustered=True):
    """値幅が「荒い時期」と「静かな時期」を行き来する（clustered=True）か、毎日ランダムか"""
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2025-06-01", periods=n)
    if clustered:
        regime = np.repeat(rng.choice([1.0, 3.0], size=n // 20 + 1), 20)[:n]
    else:
        regime = np.full(n, 2.0)
    width = regime * rng.lognormal(0, 0.3, n)
    close = 1000 * np.exp(rng.normal(0, 0.01, n).cumsum())
    high = close * (1 + width / 200)
    low = close * (1 - width / 200)
    return pd.DataFrame({"Open": close, "High": high, "Low": low, "Close": close}, index=idx)


def test_validation_detects_volatility_clustering():
    v = validate_range_prediction(_regime_ohlc(clustered=True), window=10)
    assert v["n"] > 200 and v["r"] > 0.5, v
    assert 0 < v["within_rate"] < 1
    assert v["rough_ratio"] > v["median_ratio"]
    v0 = validate_range_prediction(_regime_ohlc(clustered=False, seed=1), window=10)
    assert abs(v0["r"]) < 0.2, v0                     # 持続性がなければ当たらない
    print("✅ test_validation_detects_volatility_clustering")


def test_validation_uses_only_past():
    """予測に当日の値幅が混ざっていないこと"""
    df = _regime_ohlc(clustered=True, seed=3)
    pa = predicted_vs_actual(df, window=10)
    actual = daily_range_pct(df)
    d = pa.index[50]
    pos = actual.index.get_loc(d)
    assert abs(pa.loc[d, "pred"] - actual.iloc[pos - 10:pos].mean()) < 1e-9
    # その日だけ極端に荒らしても、その日の予測は変わらない（翌日以降の予測だけ変わる）
    df2 = df.copy()
    df2.loc[d, "High"] = df2.loc[d, "Close"] * 1.5
    pa2 = predicted_vs_actual(df2, window=10)
    assert pa2.loc[d, "pred"] == pa.loc[d, "pred"]
    assert pa2.loc[d, "actual"] > pa.loc[d, "actual"]
    nxt = pa.index[51]
    assert pa2.loc[nxt, "pred"] > pa.loc[nxt, "pred"]
    print("✅ test_validation_uses_only_past")


def test_records_roundtrip_and_tz():
    df = _flat(n=15)
    df.index = df.index.tz_localize("Asia/Tokyo")
    back = records_to_ohlc(ohlc_to_records(df))
    assert len(back) == 15 and list(back.columns) == ["Open", "High", "Low", "Close"]
    assert build_range_signal(back, today=date(2026, 10, 28))["available"]
    assert records_to_ohlc([]) is None
    print("✅ test_records_roundtrip_and_tz")


if __name__ == "__main__":
    test_daily_range_pct_uses_prev_close()
    test_completed_bars_drops_today_and_later()
    test_build_range_signal_converts_to_yen()
    test_partial_today_bar_does_not_change_result()
    test_level_high_and_low()
    test_rough_day_uses_validation_ratio()
    test_not_enough_data()
    test_validation_detects_volatility_clustering()
    test_validation_uses_only_past()
    test_records_roundtrip_and_tz()
    print("\n🎉 All expected_range tests passed!")
