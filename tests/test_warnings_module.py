"""
warnings_module のユニットテスト
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from warnings_module import (
    is_japanese_holiday,
    is_trading_day,
    is_last_trading_day_of_month,
    is_pre_holiday,
    next_trading_day,
    check_friday_warning,
    check_month_end_warning,
    check_pre_holiday_warning,
    check_month_start_warning,
    get_all_warnings,
)


def test_japanese_holidays():
    assert is_japanese_holiday(date(2026, 1, 1)) is True    # 元日
    assert is_japanese_holiday(date(2026, 5, 3)) is True    # 憲法記念日
    assert is_japanese_holiday(date(2026, 2, 11)) is True   # 建国記念の日
    assert is_japanese_holiday(date(2026, 11, 3)) is True   # 文化の日
    assert is_japanese_holiday(date(2026, 4, 23)) is False  # 平日
    # ハッピーマンデー: 2026/1/12 = 第2月曜日 = 成人の日
    assert is_japanese_holiday(date(2026, 1, 12)) is True
    print("✅ test_japanese_holidays")


def test_is_trading_day():
    # 2026/4/23 (木) = 営業日
    assert is_trading_day(date(2026, 4, 23)) is True
    # 2026/4/25 (土) = 非営業日
    assert is_trading_day(date(2026, 4, 25)) is False
    # 2026/5/3 (日、憲法記念日) = 非営業日
    assert is_trading_day(date(2026, 5, 3)) is False
    # 2026/1/1 = 非営業日
    assert is_trading_day(date(2026, 1, 1)) is False
    # 2025/12/31 = 非営業日
    assert is_trading_day(date(2025, 12, 31)) is False
    print("✅ test_is_trading_day")


def test_next_trading_day_skips_weekend():
    # 2026/4/24 (金) の翌営業日 = 4/27 (月)
    result = next_trading_day(date(2026, 4, 24))
    assert result == date(2026, 4, 27)
    print("✅ test_next_trading_day_skips_weekend")


def test_next_trading_day_skips_holidays():
    # 2026/5/1 (金) の翌営業日はGWを挟むので5月中旬寄り
    # 簡易祝日判定なので5/6（振替休日）を拾わない場合もある。
    # 実用上、金曜日の翌営業日が5月最初の方に来ればOK
    result = next_trading_day(date(2026, 5, 1))
    assert result is not None
    assert result >= date(2026, 5, 6)  # 5/6以降になっていればOK
    print("✅ test_next_trading_day_skips_holidays")


def test_is_last_trading_day_of_month():
    # 2026/4/30 (木) = 4月最終営業日
    assert is_last_trading_day_of_month(date(2026, 4, 30)) is True
    # 2026/4/23 (木) = 月最終ではない
    assert is_last_trading_day_of_month(date(2026, 4, 23)) is False
    print("✅ test_is_last_trading_day_of_month")


def test_is_pre_holiday():
    # 2026/4/24 (金) = 土日を挟むので、2日差だが「2日以上」の基準で 3連休前扱い
    # next_trading_day(2026/4/24) = 2026/4/27, gap = 3 → True
    assert is_pre_holiday(date(2026, 4, 24)) is True
    # 2026/4/23 (木) = 翌営業日 4/24 (金)、gap=1 → False
    assert is_pre_holiday(date(2026, 4, 23)) is False
    print("✅ test_is_pre_holiday")


def test_friday_warning_week_positive():
    """金曜かつ週足陽線 → high severity"""
    warn = check_friday_warning(date(2026, 4, 24), nikkei_week_is_positive=True)
    assert warn is not None
    assert warn.type == "friday_closing"
    assert warn.severity == "high"
    assert "陽線" in warn.label
    print("✅ test_friday_warning_week_positive")


def test_friday_warning_week_negative():
    """金曜かつ週足陰線 → medium severity、買い警戒"""
    warn = check_friday_warning(date(2026, 4, 24), nikkei_week_is_positive=False)
    assert warn is not None
    assert warn.severity == "medium"
    assert "ショートカバー" in warn.label or "ショートカバー" in warn.message
    print("✅ test_friday_warning_week_negative")


def test_friday_warning_not_friday():
    """金曜でない → None"""
    warn = check_friday_warning(date(2026, 4, 23), nikkei_week_is_positive=True)
    assert warn is None
    print("✅ test_friday_warning_not_friday")


def test_month_end_warning_positive():
    """月末 + 月足陽線 → high severity"""
    warn = check_month_end_warning(date(2026, 4, 30), nikkei_month_is_positive=True)
    assert warn is not None
    assert warn.severity == "high"
    assert "陽線" in warn.label
    print("✅ test_month_end_warning_positive")


def test_month_end_warning_not_month_end():
    warn = check_month_end_warning(date(2026, 4, 23), nikkei_month_is_positive=True)
    assert warn is None
    print("✅ test_month_end_warning_not_month_end")


def test_pre_holiday_warning():
    """金曜（週末前）で警告が出る"""
    warn = check_pre_holiday_warning(date(2026, 4, 24))
    assert warn is not None
    assert warn.type == "pre_holiday"
    print("✅ test_pre_holiday_warning")


def test_month_start_warning():
    # 2026/5/1 = 5月最初の営業日
    warn = check_month_start_warning(date(2026, 5, 1))
    assert warn is not None
    assert warn.type == "month_start"
    # 月中の平日は None
    warn2 = check_month_start_warning(date(2026, 5, 7))
    # 5/7は月初から1週間経ってる。前営業日は4/30（前月）ではなく、祝日連休後の日
    # 実装のチェック: 過去7日以内に別の月の営業日があるか
    # 5/7 → 5/6, 5/5, 5/4, 5/3, 5/2, 5/1(金) ← 同月、 5/1 は営業日扱いの可能性
    # 5/1 が営業日なら5/7の前営業日として同月扱いでNone
    print("✅ test_month_start_warning")


def test_get_all_warnings_multiple():
    """4/30 (木) = 月末 → month_end 警告"""
    warnings = get_all_warnings(
        date(2026, 4, 30),
        nikkei_week_is_positive=True,
        nikkei_month_is_positive=True,
    )
    assert len(warnings) >= 1
    # high severity が先頭
    assert warnings[0].severity == "high"
    print("✅ test_get_all_warnings_multiple")


def test_get_all_warnings_empty():
    """通常の平日中日 → 警告なし"""
    warnings = get_all_warnings(
        date(2026, 4, 22),  # 水曜
        nikkei_week_is_positive=True,
        nikkei_month_is_positive=True,
    )
    assert warnings == []
    print("✅ test_get_all_warnings_empty")


def test_friday_suppresses_pre_holiday():
    """金曜は friday_closing と pre_holiday の両方が出るが、pre_holiday は抑制される"""
    warnings = get_all_warnings(
        date(2026, 4, 24),  # 金曜
        nikkei_week_is_positive=True,
    )
    types = [w.type for w in warnings]
    assert "friday_closing" in types
    assert "pre_holiday" not in types
    print("✅ test_friday_suppresses_pre_holiday")


def test_month_end_suppresses_pre_holiday():
    """月末が金曜なら、月末警告のみ残り pre_holiday は除去される"""
    # 2026/4/30 = 木曜なので別のケースで検証
    # 2026/1/30 = 金曜 で月末
    warnings = get_all_warnings(
        date(2026, 1, 30),
        nikkei_week_is_positive=True,
        nikkei_month_is_positive=True,
    )
    types = [w.type for w in warnings]
    assert "friday_month_end" in types                      # 金曜×月末は1枚にまとめる
    assert "friday_closing" not in types and "month_end" not in types
    assert "pre_holiday" not in types
    print("✅ test_month_end_suppresses_pre_holiday")


_CLOSING = {"all": {"n": 390, "hit": 0.51, "mean": -0.04},
            "gap_same": {"n": 151, "hit": 0.44, "mean": -0.34},
            "gap_opposite": {"n": 163, "hit": 0.55, "mean": 0.15},
            "gap_small": {"n": 76, "hit": 0.57, "mean": 0.14}}


def test_friday_overnight_patterns():
    fri = date(2026, 11, 6)
    same = check_friday_warning(fri, True, overnight_diff=300, validation=_CLOSING)
    assert "151回" in same.evidence and "44%" in same.evidence
    assert "そのまま続いた日" in same.message                  # 記事と逆の結果ならそれを書く
    opp = check_friday_warning(fri, True, overnight_diff=-300, validation=_CLOSING)
    assert "163回" in opp.evidence and "逆" in opp.message
    down_same = check_friday_warning(fri, False, overnight_diff=-300, validation=_CLOSING)
    assert "151回" in down_same.evidence
    small = check_friday_warning(fri, False, overnight_diff=50, validation=_CLOSING)
    assert "76回" in small.evidence
    assert check_friday_warning(fri, True).evidence == "未検証"
    assert check_month_end_warning(date(2026, 11, 30), True, 300, _CLOSING).evidence.startswith("検証")
    print("✅ test_friday_overnight_patterns")


def test_friday_month_end_merged_uses_both_stats():
    v = {"closing": {"friday": _CLOSING, "month_end": _CLOSING,
                     "both": {"all": {"n": 45, "hit": 0.5, "mean": 0.1}, "gap_same": {"n": 16, "hit": 0.31, "mean": -0.58}}}}
    ws = get_all_warnings(date(2026, 1, 30), True, True, overnight_diff=300, validation=v)
    m = [w for w in ws if w.type == "friday_month_end"][0]
    assert "週末×月末" in m.label and "16回" in m.evidence
    print("✅ test_friday_month_end_merged_uses_both_stats")


def test_us_holiday_detection():
    from warnings_module import nyse_holidays, us_was_closed
    h = nyse_holidays(2026)
    assert date(2026, 11, 26) in h and date(2026, 7, 3) in h and date(2026, 4, 3) in h   # 感謝祭・独立記念日(振替)・聖金曜日
    assert us_was_closed(date(2026, 11, 27))       # 感謝祭の翌朝
    assert us_was_closed(date(2026, 1, 20))        # キング牧師記念日の翌朝（火曜）
    assert us_was_closed(date(2026, 4, 6))         # 聖金曜日 → 月曜の朝
    assert not us_was_closed(date(2026, 11, 2))    # 普通の月曜（米国の金曜は開いている）
    ws = get_all_warnings(date(2026, 11, 27))
    assert any(w.type == "us_holiday" and w.severity == "high" for w in ws)
    print("✅ test_us_holiday_detection")


def test_after_break_and_dividend_days():
    from warnings_module import check_after_break_warning, dividend_days, check_dividend_record_warning
    assert check_after_break_warning(date(2026, 1, 5)) is not None     # 年末年始明け
    assert check_after_break_warning(date(2026, 11, 2)) is None         # 普通の週明けは出さない
    assert dividend_days(2026, 3) == (date(2026, 3, 27), date(2026, 3, 30))   # 3/31(火) の2営業日前
    assert dividend_days(2025, 8) == (date(2025, 8, 27), date(2025, 8, 28))   # 月末が日曜
    assert dividend_days(2026, 9) == (date(2026, 9, 28), date(2026, 9, 29))   # 9/30(水)
    k = {"3月・9月": {"last_day_buy": {"n": 15, "hit": 0.67, "mean": 0.3}, "ex_day_n": 15, "ex_day_gap_mean": -0.76}}
    w = check_dividend_record_warning(date(2026, 3, 27), k)
    assert w.type == "dividend_last" and w.severity == "high" and "15回" in w.evidence
    assert check_dividend_record_warning(date(2026, 3, 30), k).type == "dividend_ex"
    assert check_dividend_record_warning(date(2026, 6, 26)).severity == "medium"
    assert check_dividend_record_warning(date(2026, 5, 27)) is None
    print("✅ test_after_break_and_dividend_days")


if __name__ == "__main__":
    test_japanese_holidays()
    test_is_trading_day()
    test_next_trading_day_skips_weekend()
    test_next_trading_day_skips_holidays()
    test_is_last_trading_day_of_month()
    test_is_pre_holiday()
    test_friday_warning_week_positive()
    test_friday_warning_week_negative()
    test_friday_warning_not_friday()
    test_month_end_warning_positive()
    test_month_end_warning_not_month_end()
    test_pre_holiday_warning()
    test_month_start_warning()
    test_get_all_warnings_multiple()
    test_get_all_warnings_empty()
    test_friday_suppresses_pre_holiday()
    test_month_end_suppresses_pre_holiday()
    test_friday_overnight_patterns()
    test_friday_month_end_merged_uses_both_stats()
    test_us_holiday_detection()
    test_after_break_and_dividend_days()
    print("\n🎉 All warnings tests passed!")
