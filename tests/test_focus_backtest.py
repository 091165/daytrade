"""
focus_backtest のユニットテスト（ネットワーク不要・合成データ）
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from focus_backtest import align_focus_returns, high_day_study, compute_focus_stats, validate_focus_stock
from focus_stocks import FOCUS_STOCKS


def _bdays(n: int, start="2026-01-05") -> pd.DatetimeIndex:
    return pd.bdate_range(start=start, periods=n)


def _make_jp(us: pd.Series, gap_beta: float, intra_beta: float, noise: float, seed: int):
    """米の前日騰落率 us（米日付）から、日本株の Open/Close を作る。

    日本T日の窓 = gap_beta * us[T-1] + ノイズ、寄り後 = intra_beta * us[T-1] + ノイズ
    """
    rng = np.random.default_rng(seed)
    jp_dates = us.index[1:]  # 米T-1 → 日本T
    opens, closes = [], []
    prev_close = 1000.0
    for i, t in enumerate(jp_dates):
        u = us.iloc[i]
        gap = gap_beta * u + rng.normal(0, noise)
        intra = intra_beta * u + rng.normal(0, noise)
        o = prev_close * (1 + gap / 100)
        c = o * (1 + intra / 100)
        opens.append(o)
        closes.append(c)
        prev_close = c
    # 最初の日（米データの初日）にも終値が必要
    idx = us.index
    jp_open = pd.Series([1000.0] + opens, index=idx)
    jp_close = pd.Series([1000.0] + closes, index=idx)
    return jp_open, jp_close


def test_alignment_uses_previous_us_day():
    idx = _bdays(4)
    us = pd.Series([1.0, 2.0, 3.0, 4.0], index=idx)
    jp_open = pd.Series([100.0, 110.0, 100.0, 100.0], index=idx)
    jp_close = pd.Series([100.0, 120.0, 90.0, 100.0], index=idx)
    df = align_focus_returns(us, jp_open, jp_close)
    # 日本2日目は米1日目(=1.0)を使う
    assert df["us"].iloc[0] == 1.0
    assert abs(df["gap"].iloc[0] - 10.0) < 1e-9           # 100 → 110
    assert abs(df["intraday"].iloc[0] - (120 / 110 - 1) * 100) < 1e-9
    assert df["us"].iloc[1] == 2.0
    assert abs(df["gap"].iloc[1] - (100 / 120 - 1) * 100) < 1e-9
    print("✅ test_alignment_uses_previous_us_day")


def test_us_holiday_is_skipped():
    """米国が休場で新しい情報がない日本の営業日は除外する"""
    jp_idx = pd.to_datetime(["2026-07-02", "2026-07-03", "2026-07-06"])
    # 米は 7/1 と 7/2 のみ（7/3は米国休場の想定）
    us = pd.Series([1.0, 2.0], index=pd.to_datetime(["2026-07-01", "2026-07-02"]))
    jo = pd.Series([100.0, 100.0, 100.0], index=jp_idx)
    jc = pd.Series([100.0, 100.0, 100.0], index=jp_idx)
    df = align_focus_returns(us, jo, jc)
    # 7/3(日本) ← 米7/2 は新情報なので採用。7/6(日本) ← 最新の米は7/2で、日本の前営業日7/3より前 → 除外
    assert list(df.index) == [pd.Timestamp("2026-07-03")]
    assert df["us"].iloc[0] == 2.0
    print("✅ test_us_holiday_is_skipped")


def test_timezone_aware_index_is_ok():
    idx = _bdays(30).tz_localize("Asia/Tokyo")
    us = pd.Series(np.linspace(-2, 2, 30), index=idx.tz_convert("America/New_York"))
    jo = pd.Series(100.0, index=idx)
    jc = pd.Series(100.0, index=idx)
    df = align_focus_returns(us, jo, jc)
    assert len(df) > 0
    print("✅ test_timezone_aware_index_is_ok")


def test_detects_gap_only_link():
    """窓だけ連動・寄り後は無関係 → gap=confirmed, intraday=none"""
    rng = np.random.default_rng(1)
    us = pd.Series(rng.normal(0, 1.5, 250), index=_bdays(250))
    jo, jc = _make_jp(us, gap_beta=0.8, intra_beta=0.0, noise=1.0, seed=2)
    stats_ = compute_focus_stats(align_focus_returns(us, jo, jc))
    assert stats_["gap_link"] == "confirmed", stats_
    assert stats_["intraday_link"] in ("none", "weak"), stats_
    assert stats_["gap_r"] > 0.5
    assert abs(stats_["intraday_r"]) < 0.2
    # 米が大きく上げた日は窓が上に開きやすい
    assert stats_["us_up"]["gap_hit"] > 0.7
    print("✅ test_detects_gap_only_link")


def test_detects_reverse_intraday():
    """米が上げると寄り後は下げる（寄り天型） → intraday=reverse"""
    rng = np.random.default_rng(3)
    us = pd.Series(rng.normal(0, 1.5, 250), index=_bdays(250))
    jo, jc = _make_jp(us, gap_beta=0.8, intra_beta=-0.5, noise=0.8, seed=4)
    stats_ = compute_focus_stats(align_focus_returns(us, jo, jc))
    assert stats_["intraday_link"] == "reverse", stats_
    assert stats_["us_up"]["mean_intraday"] < 0
    print("✅ test_detects_reverse_intraday")


def test_insufficient_data_is_unknown():
    idx = _bdays(10)
    us = pd.Series(np.arange(10, dtype=float), index=idx)
    jo = pd.Series(100.0, index=idx)
    jc = pd.Series(100.0, index=idx)
    stats_ = compute_focus_stats(align_focus_returns(us, jo, jc))
    assert stats_["gap_link"] == "unknown" and stats_["gap_r"] is None
    print("✅ test_insufficient_data_is_unknown")


def test_validate_focus_stock_with_fake_download():
    """yfinance の group_by='ticker' 形式を模したデータで、通しで動くこと"""
    sbg = next(s for s in FOCUS_STOCKS if s.code == "9984")
    idx = _bdays(250)
    rng = np.random.default_rng(5)
    cols = {}
    # 米: ARM と QQQ だけある（NVDA欠損）
    arm = pd.Series(100 * np.cumprod(1 + rng.normal(0, 0.02, 250)), index=idx)
    qqq = pd.Series(100 * np.cumprod(1 + rng.normal(0, 0.01, 250)), index=idx)
    us_avg = ((arm.pct_change() + qqq.pct_change()) / 2 * 100).fillna(0)
    jo, jc = _make_jp(us_avg, gap_beta=0.7, intra_beta=0.0, noise=1.0, seed=6)
    for t, s in (("ARM", arm), ("QQQ", qqq)):
        cols[(t, "Open")] = s
        cols[(t, "Close")] = s
    cols[("9984.T", "Open")] = jo
    cols[("9984.T", "Close")] = jc
    data = pd.DataFrame(cols)
    r = validate_focus_stock(sbg, data)
    assert r["code"] == "9984"
    assert r["us_tickers_used"] == ["ARM", "QQQ"]
    assert r["n"] > 200
    assert r["gap_link"] == "confirmed", r
    assert set(r["per_proxy"]) == {"ARM", "QQQ"}
    assert "gap_label" in r and "intraday_label" in r
    print("✅ test_validate_focus_stock_with_fake_download")


def test_validate_focus_stock_missing_jp_data():
    sbg = next(s for s in FOCUS_STOCKS if s.code == "9984")
    idx = _bdays(30)
    data = pd.DataFrame({("ARM", "Close"): pd.Series(100.0, index=idx)})
    r = validate_focus_stock(sbg, data)
    assert r["n"] == 0 and r["gap_link"] == "unknown"
    print("✅ test_validate_focus_stock_missing_jp_data")


def test_high_day_study():
    rng = np.random.default_rng(3)
    idx = _bdays(900)
    close = 100 * np.exp(np.cumsum(rng.normal(0.001, 0.02, len(idx))))
    open_ = close * np.exp(rng.normal(0, 0.005, len(idx)))
    d = pd.DataFrame({"Open": open_, "High": np.maximum(open_, close) * 1.01, "Low": np.minimum(open_, close) * 0.99,
                      "Close": close}, index=idx)
    r = high_day_study(d)
    assert r["high"]["n"] + r["other"]["n"] == len(idx) - 250
    assert r["high"]["n"] > 0 and r["other"]["n"] > 0
    assert 0 <= r["high"]["up_rate"] <= 1 and r["p_rate"] is not None
    print("✅ test_high_day_study")


if __name__ == "__main__":
    test_high_day_study()
    test_alignment_uses_previous_us_day()
    test_us_holiday_is_skipped()
    test_timezone_aware_index_is_ok()
    test_detects_gap_only_link()
    test_detects_reverse_intraday()
    test_insufficient_data_is_unknown()
    test_validate_focus_stock_with_fake_download()
    test_validate_focus_stock_missing_jp_data()
    print("\n🎉 All focus_backtest tests passed!")
