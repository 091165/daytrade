"""
idea_study2 のユニットテスト
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from idea_study import add_verdicts, build_day_table
from idea_study2 import (
    collect_pvalues2,
    day_points,
    dev_sigma,
    dividend_events,
    stock_day_frame,
    study_asym,
    study_big_caps,
    study_cp1030,
    study_dividend,
    study_hands_off,
    study_index_dev,
    study_jump,
    study_month_bar,
    study_stock_week,
    study_threshold,
    touch_days,
    us_futures_move,
)
from test_idea_study import _market, _ohlc


def _hourly(days, seed=0):
    rng = np.random.default_rng(seed)
    rows = []
    for d in days:
        px = 100.0
        for hm in ("09:00", "10:00", "11:00", "12:30", "13:30", "14:30"):
            o = px
            c = o * (1 + rng.normal(0, 0.003))
            rows.append((pd.Timestamp(f"{d.date()} {hm}"), o, max(o, c) * 1.001, min(o, c) * 0.999, c))
            px = c
    return pd.DataFrame(rows, columns=["t", "Open", "High", "Low", "Close"]).set_index("t")


def test_day_points():
    h = _hourly(pd.bdate_range("2026-01-05", periods=3))
    p = day_points(h)
    assert len(p) == 3
    d = p.index[0]
    g = h[h.index.normalize() == d]
    assert p.loc[d, "p10"] == g.iloc[1]["Open"] and p.loc[d, "am_close"] == g.iloc[2]["Close"]
    assert p.loc[d, "pm_open"] == g.iloc[3]["Open"] and p.loc[d, "close"] == g.iloc[-1]["Close"]
    print("✅ test_day_points")


def test_us_futures_move_window():
    # ES=F：ニューヨーク 2026/1/5(月) 16:00 に 100、日本の 1/6 9:00（= NY 19:00）に 101
    idx = pd.date_range("2026-01-05 14:00", "2026-01-05 20:00", freq="1h", tz="America/New_York")
    es = pd.DataFrame({"Close": [99, 99.5, 100, 100.2, 100.5, 101, 102]}, index=idx.tz_convert("UTC"))
    mv = us_futures_move(es, pd.DatetimeIndex([pd.Timestamp("2026-01-06")]))
    assert abs(mv.iloc[0] - 1.0) < 1e-9
    print("✅ test_us_futures_move_window")


def test_touch_days_and_dividend_events():
    d = pd.DataFrame({"open": [5100, 5050, 12000], "high": [5150, 5080, 12100], "low": [5050, 4990, 9990],
                      "close": [5060, 5000, 10050], "prev_close": [5200, 5060, 10100]},
                     index=pd.bdate_range("2026-03-02", periods=3))
    t = touch_days(d)
    assert list(t) == [False, True, True]                  # 5000 に触れた・10000 に触れた（1万円以上は5,000円刻み）
    d["gap"] = (d["open"] / d["prev_close"] - 1) * 100
    d["ret"] = (d["close"] / d["open"] - 1) * 100
    ev = dividend_events(d, pd.Series([50.6], index=[d.index[1]]))
    assert len(ev) == 1 and abs(ev["div_pct"].iloc[0] - 1.0) < 1e-9
    assert abs(ev["drop_ratio"].iloc[0] - (-ev["gap"].iloc[0] / 1.0)) < 1e-9
    assert study_dividend(ev)["n"] == 1
    print("✅ test_touch_days_and_dividend_events")


def test_dev_sigma_flags_crash():
    c = pd.Series(100 * np.exp(np.random.default_rng(1).normal(0, 0.01, 400).cumsum()),
                  index=pd.bdate_range("2024-01-01", periods=400))
    c.iloc[-1] = c.iloc[-2] * 0.85                           # 最後の日に急落
    z = dev_sigma(c)["z"]
    assert z.iloc[-1] < -3 and z.iloc[:-1].abs().max() < 6
    print("✅ test_dev_sigma_flags_crash")


def test_cp1030_counts_flips():
    days = pd.bdate_range("2026-01-05", periods=5)
    rows = []
    for d in days:
        for i, hm in enumerate(("09:00", "09:30", "10:00", "10:30", "11:00")):
            up = i % 2 == 0                                    # 30分ごとに上げ下げが入れかわる
            rows.append((pd.Timestamp(f"{d.date()} {hm}"), 100, 101 if up else 99))
    m30 = pd.DataFrame(rows, columns=["t", "Open", "Close"]).set_index("t")
    r = study_cp1030({"X": m30})
    assert r["10:30"]["rate"] == 1.0 and r["09:30"]["n"] == 5
    print("✅ test_cp1030_counts_flips")


def test_studies_run_on_synthetic_market():
    data = _market(500, seed=11)
    df = add_verdicts(build_day_table(data))
    h = _hourly(df.index, seed=2)
    pts = {"日経平均": day_points(h), "X": day_points(_hourly(df.index, seed=3))}
    res = {"threshold": study_threshold(df), "asym": study_asym(df, pts["日経平均"]),
           "jump": study_jump(df, pts["日経平均"]), "hands_off": study_hands_off(df, pts)}
    st = {"X": stock_day_frame(_ohlc(df.index, 4))}
    nk = stock_day_frame(data["^N225"])
    res["stock_week"] = study_stock_week(st, nk)
    res["month_bar"] = study_month_bar(st)
    res["big_caps"] = study_big_caps({"A": stock_day_frame(_ohlc(df.index, 5) * 50)}, nk)
    res["index_dev"] = study_index_dev({"N": data["^IXIC"]["Close"]}, nk, pts["日経平均"])
    assert set(res["threshold"]) == {"0.3", "0.5"}
    assert res["threshold"]["0.5"]["hands_off_share"] >= res["threshold"]["0.3"]["hands_off_share"]
    assert "gap_fade" in res["hands_off"]
    ps = collect_pvalues2(res)
    assert len(ps) > 10 and all(0 <= p <= 1 for _, p in ps)
    print("✅ test_studies_run_on_synthetic_market")


if __name__ == "__main__":
    test_day_points()
    test_us_futures_move_window()
    test_touch_days_and_dividend_events()
    test_dev_sigma_flags_crash()
    test_cp1030_counts_flips()
    test_studies_run_on_synthetic_market()
    print("\n🎉 All idea_study2 tests passed!")
