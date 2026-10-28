"""
focus_stocks のユニットテスト
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from focus_stocks import (
    FOCUS_STOCKS,
    build_all_focus_signals,
    build_focus_signal,
    classify_link,
    classify_move,
    get_focus_us_tickers,
)

FUJIKURA = next(s for s in FOCUS_STOCKS if s.code == "5803")
SBG = next(s for s in FOCUS_STOCKS if s.code == "9984")


def test_focus_stocks_are_approved_two_and_trial_murata():
    assert {s.code for s in FOCUS_STOCKS if not s.trial} == {"5803", "9984"}
    assert {s.code for s in FOCUS_STOCKS if s.trial} == {"6981"}
    murata = build_focus_signal(next(s for s in FOCUS_STOCKS if s.code == "6981"), {"SOXX": 3.0}, "hands_off")
    assert murata.trial is True
    assert build_focus_signal(FUJIKURA, {}, "hands_off").trial is False
    print("✅ test_focus_stocks_are_approved_two_and_trial_murata")


def test_classify_move_thresholds():
    assert classify_move(0.5) == ("neutral", "neutral")
    assert classify_move(1.2) == ("bullish", "weak")
    assert classify_move(-3.0) == ("bearish", "strong")
    print("✅ test_classify_move_thresholds")


def test_classify_link():
    assert classify_link(None, None) == "unknown"
    assert classify_link(0.45, 0.001) == "confirmed"
    assert classify_link(0.15, 0.2) == "weak"
    assert classify_link(0.25, 0.2) == "weak"       # rは大きいが有意でない
    assert classify_link(0.02, 0.8) == "none"
    assert classify_link(-0.3, 0.01) == "reverse"
    assert classify_link(-0.3, 0.2) == "none"       # 有意でない逆相関は「なし」
    print("✅ test_classify_link")


def test_average_uses_only_available_tickers():
    sig = build_focus_signal(FUJIKURA, {"GLW": 2.0, "COHR": 4.0}, "hands_off")
    assert abs(sig.us_avg_change_pct - 3.0) < 1e-9
    assert set(sig.us_changes) == {"GLW", "COHR"}
    assert sig.direction == "bullish" and sig.strength == "strong"
    print("✅ test_average_uses_only_available_tickers")


def test_no_data_is_neutral():
    sig = build_focus_signal(SBG, {}, "hands_off")
    assert sig.direction == "neutral"
    assert sig.us_changes == {}
    print("✅ test_no_data_is_neutral")


def test_conflict_with_market_flagged():
    # 指数は買い主体なのに、SBGの関連株が下げ → 見送り候補
    sig = build_focus_signal(SBG, {"ARM": -3.0, "NVDA": -2.0, "QQQ": -1.0}, "buy_bias")
    assert sig.conflict_with_market is True
    assert "見送る" in sig.conflict_text
    # 同じ向きなら警告なし
    sig2 = build_focus_signal(SBG, {"ARM": -3.0, "NVDA": -2.0, "QQQ": -1.0}, "sell_bias")
    assert sig2.conflict_with_market is False
    # 手出し無用の日は逆向きという概念がない
    sig3 = build_focus_signal(SBG, {"ARM": -3.0}, "hands_off")
    assert sig3.conflict_with_market is False
    print("✅ test_conflict_with_market_flagged")


def test_unvalidated_text_says_so():
    sig = build_focus_signal(FUJIKURA, {"GLW": 2.0}, "hands_off", validation=None)
    assert "未検証" in sig.gap_text
    assert "未検証" in sig.intraday_text
    print("✅ test_unvalidated_text_says_so")


def test_validated_gap_only_warns_intraday():
    """窓は連動・寄り後は連動なし → 寄り後は『読めない』と表示する"""
    v = {"gap_r": 0.5, "gap_p": 0.0001, "intraday_r": 0.03, "intraday_p": 0.7, "n": 240}
    sig = build_focus_signal(FUJIKURA, {"GLW": 2.0, "COHR": 2.0}, "hands_off", validation=v)
    assert "検証済み" in sig.gap_text and "高く" in sig.gap_text
    assert "読めない" in sig.intraday_text
    print("✅ test_validated_gap_only_warns_intraday")


def test_reverse_intraday_warns_yoritaka():
    v = {"gap_r": 0.5, "gap_p": 0.0001, "intraday_r": -0.3, "intraday_p": 0.001, "n": 240}
    up = build_focus_signal(SBG, {"ARM": 3.0}, "hands_off", validation=v)
    assert "寄り天" in up.intraday_text
    down = build_focus_signal(SBG, {"ARM": -3.0}, "hands_off", validation=v)
    assert "寄り底" in down.intraday_text and "安く" in down.gap_text
    print("✅ test_reverse_intraday_warns_yoritaka")


def test_build_all_and_tickers():
    sigs = build_all_focus_signals({"ARM": 1.5}, "hands_off", {"9984": {"gap_r": 0.4, "gap_p": 0.01}})
    assert [s.code for s in sigs] == ["5803", "9984", "6981"]
    assert sigs[1].validation is not None and sigs[0].validation is None
    t = get_focus_us_tickers()
    assert "ARM" in t and "GLW" in t and t == sorted(set(t))
    print("✅ test_build_all_and_tickers")


if __name__ == "__main__":
    test_focus_stocks_are_approved_two_and_trial_murata()
    test_classify_move_thresholds()
    test_classify_link()
    test_average_uses_only_available_tickers()
    test_no_data_is_neutral()
    test_conflict_with_market_flagged()
    test_unvalidated_text_says_so()
    test_validated_gap_only_warns_intraday()
    test_reverse_intraday_warns_yoritaka()
    test_build_all_and_tickers()
    print("\n🎉 All focus_stocks tests passed!")
