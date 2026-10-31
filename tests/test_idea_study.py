"""
idea_study のユニットテスト
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from idea_study import (
    add_verdicts,
    bh_qvalues,
    build_day_table,
    collect_pvalues,
    deviation_frame,
    kenri_days,
    study_closing,
    study_dev25,
    study_kenri,
    study_sp05,
    study_v2,
    study_weekday,
    weekday_group,
)


def _ohlc(days, seed, drift=0.0):
    rng = np.random.default_rng(seed)
    c = 100 * np.exp(rng.normal(drift, 0.01, len(days)).cumsum())
    o = c * (1 + rng.normal(0, 0.003, len(days)))
    return pd.DataFrame({"Open": o, "High": np.maximum(o, c) * 1.005, "Low": np.minimum(o, c) * 0.995, "Close": c},
                        index=days)


def _market(n=400, follow_sp=0.0, seed=0):
    jp = pd.bdate_range("2024-01-04", periods=n)
    us = pd.bdate_range("2024-01-03", periods=n + 1).drop(pd.Timestamp("2024-07-04"), errors="ignore")
    data = {t: _ohlc(us, seed + i) for i, t in enumerate(("^IXIC", "^DJI", "^SOX", "^GSPC", "JPY=X"))}
    data["NKD=F"] = _ohlc(us, seed + 9)
    n225 = _ohlc(jp, seed + 10)
    if follow_sp:
        sp = data["^GSPC"]["Close"].pct_change() * 100
        lag = sp.reindex(jp - pd.Timedelta(days=1), method="ffill").fillna(0).values
        n225["Close"] = n225["Open"] * (1 + follow_sp * lag / 100 + np.random.default_rng(seed).normal(0, 0.002, n))
        n225["High"] = n225[["Open", "Close"]].max(axis=1) * 1.003
        n225["Low"] = n225[["Open", "Close"]].min(axis=1) * 0.997
    data["^N225"] = n225
    return data


def test_day_table_alignment_and_us_holiday():
    data = _market(150)
    df = build_day_table(data)
    t = pd.Timestamp("2024-07-05")                       # 前日 7/4 は米国休場
    assert df.loc[t, "us_closed"]
    assert not df.loc[pd.Timestamp("2024-07-08"), "us_closed"]   # 月曜の材料は米国の金曜
    sp = data["^GSPC"]["Close"].pct_change() * 100
    assert abs(df.loc[pd.Timestamp("2024-07-08"), "sp"] - sp[pd.Timestamp("2024-07-05")]) < 1e-9
    assert abs(df.loc[t, "ret"] - (data["^N225"].loc[t, "Close"] / data["^N225"].loc[t, "Open"] - 1) * 100) < 1e-9
    print("✅ test_day_table_alignment_and_us_holiday")


def test_sp05_detects_follow_and_v2_runs():
    df = add_verdicts(build_day_table(_market(500, follow_sp=0.8)))
    r = study_sp05(df, {"日経平均": df["ret"]})["日経平均"]
    assert r["up"]["hit"] > 0.7 and r["down"]["hit"] > 0.7 and r["up"]["p_mean"] < 0.01
    v = study_v2(df)
    assert v["v1"]["n"] >= v["v2"]["n"] or v["v2"]["hands_off_share"] >= 0
    assert all(1 <= a["agree"] <= 6 for a in v["by_agree"])
    print("✅ test_sp05_detects_follow_and_v2_runs")


def test_noise_is_mostly_not_significant():
    data = _market(500, seed=50)
    df = add_verdicts(build_day_table(data))
    stocks = {"X": data["^N225"].assign(ret=df["ret"], trendiness=df["trendiness"], range_pct=df["range_pct"],
                                         gap_days=df["gap_days"]).dropna()}
    res = {"v2": study_v2(df), "sp05": study_sp05(df, {"日経平均": df["ret"]}), "closing": study_closing(df),
           "weekday": study_weekday(df, stocks), "dev25": study_dev25({"X": data["^N225"]})}
    qs = bh_qvalues([p for _, p in collect_pvalues(res)])
    assert sum(q < 0.05 for q in qs) <= 1
    print("✅ test_noise_is_mostly_not_significant")


def test_closing_reversal_detected():
    """金曜に今週の向きと逆に動く市場を作ると、反転の的中が高く出る"""
    df = build_day_table(_market(300, seed=3))
    week = df.index.to_period("W")
    first = df.groupby(week)["open"].transform("first")
    fri = df.index.weekday == 4
    trend = np.sign(df["prev_close"] - first)
    df.loc[fri, "ret"] = -trend[fri] * 0.5
    r = study_closing(df)["friday"]["all"]
    assert r["hit"] > 0.95 and r["n"] > 40
    print("✅ test_closing_reversal_detected")


def test_kenri_days_two_days_before_month_end():
    days = [d.date() for d in pd.bdate_range("2025-03-01", "2025-04-30")]
    days = [d for d in days if d != date(2025, 3, 20)]   # 春分の日
    last, ex = kenri_days(days)
    assert date(2025, 3, 27) in last and date(2025, 3, 28) in ex      # 3/31(月) の2営業日前
    # 月末が土日のとき：2025/8/29(金) が最終営業日 → 8/27 が権利付き最終日
    days = [d.date() for d in pd.bdate_range("2025-08-01", "2025-09-05")]
    last, ex = kenri_days(days)
    assert date(2025, 8, 27) in last and date(2025, 8, 28) in ex
    df = build_day_table(_market(300, seed=4))
    k = study_kenri(df)
    assert k["全部の月"]["last_day_buy"]["n"] >= 10
    print("✅ test_kenri_days_two_days_before_month_end")


def test_weekday_group_after_long_weekend():
    df = build_day_table(_market(120, seed=5))
    df = df.drop(pd.Timestamp("2024-03-18"))                 # 月曜休み → 火曜が週明け
    df.loc[pd.Timestamp("2024-03-19"), "gap_days"] = 4
    g = weekday_group(df)
    assert g[pd.Timestamp("2024-03-19")] == "週明け"
    assert g[pd.Timestamp("2024-03-20")] == "火〜木" and g[pd.Timestamp("2024-03-22")] == "金曜"
    print("✅ test_weekday_group_after_long_weekend")


def test_deviation_uses_previous_close_only():
    d = _ohlc(pd.bdate_range("2024-01-01", periods=300), 7)
    f = deviation_frame(d)
    t = f.index[-1]
    prev = d["Close"].loc[:t].iloc[:-1]
    expect = (prev.iloc[-1] / prev.iloc[-25:].mean() - 1) * 100
    assert abs(f.loc[t, "dev"] - expect) < 1e-9
    d2 = d.copy()
    d2.loc[t, "Close"] *= 1.5                                 # 当日の終値を変えても乖離は変わらない
    assert abs(deviation_frame(d2).loc[t, "dev"] - f.loc[t, "dev"]) < 1e-9
    print("✅ test_deviation_uses_previous_close_only")


def test_report_section_renders_and_json_ok():
    import json
    from backtest_report import render_idea_section
    from idea_study import study_after_break
    data = _market(400, seed=8)
    df = add_verdicts(build_day_table(data))
    stocks = {"X": data["^N225"].assign(ret=df["ret"], trendiness=df["trendiness"], range_pct=df["range_pct"],
                                         gap_days=df["gap_days"]).dropna()}
    res = {"period": "a〜b", "n_days": len(df), "v2": study_v2(df), "sp05": study_sp05(df, {"日経平均": df["ret"]}),
           "closing": study_closing(df), "weekday": study_weekday(df, stocks), "kenri": study_kenri(df),
           "dev25": study_dev25({"X": data["^N225"]}), "after_break": study_after_break(df), "strength": {},
           "us_closed": {"n": 3}}
    ps = collect_pvalues(res)
    res["tests"] = [{"name": n, "p": p, "q": q, "significant": q < 0.05} for (n, p), q in zip(ps, bh_qvalues([p for _, p in ps]))]
    json.dumps(res, ensure_ascii=False)
    html = render_idea_section(res)
    assert "ドル円" in html and "権利付き最終日" in html and "25日線" in html
    assert "まだ結果がありません" in render_idea_section(None)
    print("✅ test_report_section_renders_and_json_ok")


if __name__ == "__main__":
    test_report_section_renders_and_json_ok()
    test_day_table_alignment_and_us_holiday()
    test_sp05_detects_follow_and_v2_runs()
    test_noise_is_mostly_not_significant()
    test_closing_reversal_detected()
    test_kenri_days_two_days_before_month_end()
    test_weekday_group_after_long_weekend()
    test_deviation_uses_previous_close_only()
    print("\n🎉 All idea_study tests passed!")
