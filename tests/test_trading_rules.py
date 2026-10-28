"""
trading_rules のユニットテスト
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from trading_rules import build_trading_rules, ALWAYS_RULES


def test_buy_bias_is_trend_up_danger():
    b = build_trading_rules("buy_bias", "high")
    assert b.day_type == "trend_up"
    assert b.tone == "danger"
    assert "買い" in b.headline
    assert any("売りは入れない" in r for r in b.day_rules)
    assert any("半分" in r for r in b.day_rules)
    print("✅ test_buy_bias_is_trend_up_danger")


def test_sell_bias_is_trend_down_danger():
    b = build_trading_rules("sell_bias", "high")
    assert b.day_type == "trend_down"
    assert b.tone == "danger"
    assert "売り" in b.headline
    assert any("買いは入れない" in r for r in b.day_rules)
    print("✅ test_sell_bias_is_trend_down_danger")


def test_hands_off_is_caution_and_small():
    b = build_trading_rules("hands_off", "medium")
    assert b.day_type == "no_trend"
    assert b.tone == "caution"
    assert any("100株" in r for r in b.day_rules)
    print("✅ test_hands_off_is_caution_and_small")


def test_low_confidence_adds_skip_rule():
    b = build_trading_rules("hands_off", "low")
    assert any("見送り" in r for r in b.day_rules)
    b2 = build_trading_rules("hands_off", "high")
    assert not any("見送り" in r for r in b2.day_rules)
    print("✅ test_low_confidence_adds_skip_rule")


def test_always_rules_present_every_day():
    for v in ("buy_bias", "sell_bias", "hands_off"):
        b = build_trading_rules(v)
        assert b.always_rules == list(ALWAYS_RULES)
        assert any("10:00" in r for r in b.always_rules)
        assert any("日計り" in r for r in b.always_rules)
    print("✅ test_always_rules_present_every_day")


def test_low_confidence_does_not_leak_between_calls():
    """day_rules が呼び出し間で共有されていないこと（ミュータブル既定値の事故防止）"""
    build_trading_rules("buy_bias", "low")
    b = build_trading_rules("buy_bias", "high")
    assert not any("見送り" in r for r in b.day_rules)
    print("✅ test_low_confidence_does_not_leak_between_calls")


def test_unknown_verdict_falls_back_to_no_trend():
    b = build_trading_rules("something_else")
    assert b.day_type == "no_trend"
    print("✅ test_unknown_verdict_falls_back_to_no_trend")


def test_checks_match_day_type():
    up = build_trading_rules("buy_bias", "high").checks
    assert up["direction"] == "buy"
    assert up["approved_codes"] == ["5803", "9984"]
    assert up["per_trade_stop_yen"] == 5000
    assert up["max_qty"] is None and up["wait_window"] is None
    assert build_trading_rules("sell_bias", "high").checks["direction"] == "sell"
    flat = build_trading_rules("hands_off", "low").checks
    assert flat["direction"] is None
    assert flat["max_qty"] == 100
    assert flat["wait_window"] == ["09:05", "09:15"]
    assert flat["max_consecutive_losses"] == 2
    assert flat["daily_loss_limit_yen"] == 10000 and flat["end_time"] == "10:00"
    print("✅ test_checks_match_day_type")


if __name__ == "__main__":
    test_buy_bias_is_trend_up_danger()
    test_sell_bias_is_trend_down_danger()
    test_hands_off_is_caution_and_small()
    test_low_confidence_adds_skip_rule()
    test_always_rules_present_every_day()
    test_low_confidence_does_not_leak_between_calls()
    test_unknown_verdict_falls_back_to_no_trend()
    test_checks_match_day_type()
    print("\n🎉 All trading_rules tests passed!")
