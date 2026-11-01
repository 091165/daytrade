"""
キリバン計算ロジックのユニットテスト

PDFに登場する具体例（日経終値53749円など）をテストケースとして採用。
"""

from __future__ import annotations

import sys
from pathlib import Path

# src/ をパスに追加
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from kiriban import (
    calc_kiriban_bands,
    calc_sayatori_signal,
    calc_round_number_levels,
    is_high_volatility,
    build_daily_signal,
    SAYATORI_THRESHOLD,
)


def test_kiriban_bands_from_pdf_example():
    """PDFの例: 日経終値53749円 → +1000円=54749円（レジスタンス）、-1000円=52749円（サポート）"""
    bands = calc_kiriban_bands(53749.0)
    assert bands.bands["+1000"] == 54749.0
    assert bands.bands["-1000"] == 52749.0
    assert bands.bands["+500"] == 54249.0
    assert bands.bands["-500"] == 53249.0
    print("✅ test_kiriban_bands_from_pdf_example")


def test_sayatori_long_signal():
    """現対-1000円以下 → 鞘取りロング（先物安 → 先物買い）"""
    sig = calc_sayatori_signal(futures_price=37500, spot_price=38800)
    # 現対 = 37500 - 38800 = -1300 → ロング発動
    assert sig.direction == "long"
    assert sig.diff == -1300
    assert sig.abs_diff == 1300
    print("✅ test_sayatori_long_signal")


def test_sayatori_short_signal():
    """現対+1000円以上 → 鞘取りショート（先物高 → 先物売り）"""
    sig = calc_sayatori_signal(futures_price=39500, spot_price=38200)
    # 現対 = +1300 → ショート発動
    assert sig.direction == "short"
    assert sig.diff == 1300
    print("✅ test_sayatori_short_signal")


def test_sayatori_neutral_signal():
    """現対±1000円以内 → ニュートラル"""
    sig = calc_sayatori_signal(futures_price=38500, spot_price=38200)
    # 現対 = +300 → ニュートラル
    assert sig.direction == "neutral"
    print("✅ test_sayatori_neutral_signal")


def test_sayatori_threshold_boundary():
    """しきい値ちょうど（+1000円）は発動する（>=）"""
    sig = calc_sayatori_signal(futures_price=39500, spot_price=38500)
    assert sig.diff == 1000
    assert sig.direction == "short"
    print("✅ test_sayatori_threshold_boundary")


def test_round_number_levels():
    """現在価格38500 → サポート38000、レジスタンス39000"""
    lv = calc_round_number_levels(38500.0)
    assert lv.support == 38000.0
    assert lv.resistance == 39000.0

    # 現在価格がちょうど丸数字の場合
    lv2 = calc_round_number_levels(38000.0)
    assert lv2.support == 38000.0
    assert lv2.resistance == 39000.0
    print("✅ test_round_number_levels")


def test_high_volatility_detection():
    """現対絶対値1000円以上 = ハイボラ"""
    hi = calc_sayatori_signal(futures_price=37500, spot_price=38600)  # 現対 -1100
    lo = calc_sayatori_signal(futures_price=38400, spot_price=38500)  # 現対 -100
    assert is_high_volatility(hi) is True
    assert is_high_volatility(lo) is False
    print("✅ test_high_volatility_detection")


def test_build_daily_signal_end_to_end():
    """統合テスト: PDFに登場するシナリオを想定"""
    # シナリオ: 前日終値38500円、寄り前先物37400円（現対-1100円）
    result = build_daily_signal(
        nikkei_prev_close=38500.0,
        nikkei_futures=37400.0,
    )
    assert result["high_volatility"] is True
    assert result["sayatori_signal"]["direction"] == "long"
    assert result["kiriban_bands"]["bands"]["-1000"] == 37500.0
    assert result["round_number_levels"]["support"] == 38000.0
    print("✅ test_build_daily_signal_end_to_end")


def test_kiriban_day_hint():
    from kiriban import kiriban_day_hint
    assert "サポート" in kiriban_day_hint("buy_bias") and "逆張り" not in kiriban_day_hint("buy_bias")
    assert "レジスタンス" in kiriban_day_hint("sell_bias") and "逆張り" not in kiriban_day_hint("sell_bias")
    assert "逆張り" in kiriban_day_hint("hands_off")
    assert kiriban_day_hint("unknown") == ""
    print("✅ test_kiriban_day_hint")


def test_big_levels():
    from kiriban import big_level_text, big_levels, build_big_levels
    r = big_levels("日経", 39650, major_step=10000)
    assert r["up"] == 40000 and r["up_dist"] == 350 and r["up_major"] and r["down"] == 39000 and not r["down_major"]
    assert big_level_text(r) == "日経 40,000（大台） まで +350円 ／ 39,000 まで -650円"
    r = big_levels("1570", 26820)
    assert r["up"] == 27000 and abs(r["up_dist"] - 180) < 1e-9 and not r["up_major"]
    r = big_levels("x", 30000)
    assert r["down"] == 30000 and r["up"] == 31000                  # ちょうどなら下側に入れる
    assert len(build_big_levels(50100, None, 9500)) == 2
    print("✅ test_big_levels")


if __name__ == "__main__":
    test_big_levels()
    test_kiriban_day_hint()
    test_kiriban_bands_from_pdf_example()
    test_sayatori_long_signal()
    test_sayatori_short_signal()
    test_sayatori_neutral_signal()
    test_sayatori_threshold_boundary()
    test_round_number_levels()
    test_high_volatility_detection()
    test_build_daily_signal_end_to_end()
    print("\n🎉 All tests passed!")
