"""
日経デイトレ方針判定のユニットテスト
PDFに登場する典型パターンをテストケースとして採用。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from market_direction import build_market_direction_v2, build_market_direction, classify_move


def test_classify_move():
    assert classify_move(1.5) == "bullish"
    assert classify_move(-1.5) == "bearish"
    assert classify_move(0.1) == "neutral"
    assert classify_move(-0.1) == "neutral"
    assert classify_move(0.3) == "bullish"
    assert classify_move(-0.3) == "bearish"
    print("✅ test_classify_move")


def test_buy_bias_all_us_up_futures_aligned():
    """米3指数全面上げ + 先物現対+300円 → 買いポジ主体"""
    sig = build_market_direction(
        nasdaq_change_pct=1.5,
        dow_change_pct=0.8,
        sox_change_pct=2.1,
        futures_diff=300.0,
    )
    assert sig.verdict == "buy_bias"
    assert sig.verdict_label == "買いポジ主体"
    assert sig.confidence == "high"
    assert sig.futures_aligned is True
    print("✅ test_buy_bias_all_us_up_futures_aligned")


def test_sell_bias_all_us_down_futures_aligned():
    """米3指数全面下げ + 先物現対-500円 → 売りポジ主体"""
    sig = build_market_direction(
        nasdaq_change_pct=-2.0,
        dow_change_pct=-1.2,
        sox_change_pct=-3.5,
        futures_diff=-500.0,
    )
    assert sig.verdict == "sell_bias"
    assert sig.verdict_label == "売りポジ主体"
    assert sig.confidence == "high"
    assert sig.futures_aligned is True
    print("✅ test_sell_bias_all_us_down_futures_aligned")


def test_hands_off_us_strong_jp_weak():
    """米強日弱: 米上げだが先物追随せず → 初心者手出し無用"""
    # PDFの例: アメリカ株の下げに対し、日経先物は微上げ（逆パターン）
    sig = build_market_direction(
        nasdaq_change_pct=-1.5,
        dow_change_pct=-0.8,
        sox_change_pct=-2.0,
        futures_diff=200.0,  # 米下げなのに先物上げ
    )
    assert sig.verdict == "hands_off"
    assert sig.futures_aligned is False
    print("✅ test_hands_off_us_strong_jp_weak")


def test_hands_off_individual_diff():
    """ダウ微上げ、SOX下げの個体差相場 → 初心者手出し無用"""
    # PDFの典型例
    sig = build_market_direction(
        nasdaq_change_pct=0.2,
        dow_change_pct=0.3,
        sox_change_pct=-1.5,
        futures_diff=-100.0,
    )
    assert sig.verdict == "hands_off"
    assert sig.us_consensus_direction == "neutral"  # 米3指数がバラバラ
    print("✅ test_hands_off_individual_diff")


def test_buy_bias_medium_confidence():
    """米上げは弱いが全部上げ + 先物追随 → 買いポジ主体、confidence=medium"""
    sig = build_market_direction(
        nasdaq_change_pct=0.4,
        dow_change_pct=0.5,
        sox_change_pct=0.6,
        futures_diff=250.0,
    )
    assert sig.verdict == "buy_bias"
    assert sig.confidence == "medium"
    print("✅ test_buy_bias_medium_confidence")


def test_two_of_three_bullish():
    """3指数中2つが上げ、1つが弱い → 多数決で米上げ判定"""
    sig = build_market_direction(
        nasdaq_change_pct=1.5,
        dow_change_pct=0.8,
        sox_change_pct=-0.2,   # SOXはほぼフラット
        futures_diff=300.0,
    )
    assert sig.us_consensus_direction == "bullish"
    assert sig.verdict == "buy_bias"
    print("✅ test_two_of_three_bullish")


def test_hands_off_neutral_futures():
    """米方向性あるが現対±200円未満 → 方向性不明確で様子見"""
    sig = build_market_direction(
        nasdaq_change_pct=1.2,
        dow_change_pct=0.8,
        sox_change_pct=1.5,
        futures_diff=50.0,  # 現対フラット
    )
    assert sig.verdict == "hands_off"
    assert sig.futures_aligned is False
    print("✅ test_hands_off_neutral_futures")


def test_reasons_contain_narrative():
    """判定根拠が箇条書きで含まれる"""
    sig = build_market_direction(
        nasdaq_change_pct=1.5,
        dow_change_pct=0.8,
        sox_change_pct=2.1,
        futures_diff=300.0,
    )
    assert len(sig.reasons) >= 2
    assert any("米指数" in r for r in sig.reasons)
    assert any("追随" in r or "連動" in r or "日弱" in r or "日強" in r for r in sig.reasons)
    print("✅ test_reasons_contain_narrative")


def test_v2_all_agree_and_fx_or_sp_against():
    v = build_market_direction_v2(1.2, 0.8, 2.0, 1.0, 0.5, 300.0)
    assert v.verdict == "buy_bias" and v.agreement_count == 6
    fx_against = build_market_direction_v2(1.2, 0.8, 2.0, 1.0, -0.5, 300.0)
    assert fx_against.verdict == "hands_off" and fx_against.usdjpy_direction == "bearish"
    sp_against = build_market_direction_v2(1.2, 0.8, 2.0, -0.6, 0.0, 300.0)
    assert sp_against.verdict == "buy_bias" and sp_against.agreement_count == 4   # 4指数中3つで連動
    two_only = build_market_direction_v2(1.2, -0.8, 2.0, -0.6, 0.0, 300.0)
    assert two_only.verdict == "hands_off"                                       # 現行版なら2つでよい
    down = build_market_direction_v2(-1.2, -0.8, -2.0, -1.0, -0.1, -300.0)
    assert down.verdict == "sell_bias" and down.agreement_direction == "bearish"
    print("✅ test_v2_all_agree_and_fx_or_sp_against")


def test_us_closed_forces_hands_off():
    sig = build_market_direction(1.5, 0.8, 2.1, 300.0, us_closed=True)
    assert sig.verdict == "hands_off" and sig.confidence == "low"
    assert any("休場" in r for r in sig.reasons)
    print("✅ test_us_closed_forces_hands_off")


def test_range_day():
    from market_direction import build_range_day, is_range_day
    assert is_range_day([0.2, -0.4, 0.49], 80)
    assert not is_range_day([0.2, -0.5, 0.1], 80)            # 0.5% ちょうどは外
    assert not is_range_day([0.2, 0.1, 0.1], -150)
    assert not is_range_day([0.2, None, 0.1], 0)
    r = build_range_day("hands_off", [0.1, 0.1, 0.1], 50)
    assert r["show"] and "逆張り" in r["text"] and "未検証" in r["text"]
    # トレンド日には絶対に出さない
    assert not build_range_day("buy_bias", [0.1, 0.1, 0.1], 50)["show"]
    assert not build_range_day("sell_bias", [0.1, 0.1, 0.1], 50)["show"]
    # 承認銘柄が決算明けなら出さない
    e = build_range_day("hands_off", [0.1, 0.1, 0.1], 50, earnings_names=["村田"])
    assert not e["show"] and e["range_day"] and "決算明け" in e["why"]
    assert "未検証" not in build_range_day("hands_off", [0.1, 0.1, 0.1], 50, evidence="検証：…")["text"]
    print("✅ test_range_day")


if __name__ == "__main__":
    test_range_day()
    test_us_closed_forces_hands_off()
    test_v2_all_agree_and_fx_or_sp_against()
    test_classify_move()
    test_buy_bias_all_us_up_futures_aligned()
    test_sell_bias_all_us_down_futures_aligned()
    test_hands_off_us_strong_jp_weak()
    test_hands_off_individual_diff()
    test_buy_bias_medium_confidence()
    test_two_of_three_bullish()
    test_hands_off_neutral_futures()
    test_reasons_contain_narrative()
    print("\n🎉 All market direction tests passed!")
