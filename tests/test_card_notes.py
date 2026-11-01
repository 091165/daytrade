"""
card_notes のユニットテスト
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from card_notes import (
    build_card_notes,
    closing_note,
    closing_reversal_dir,
    dividend_note,
    fiscal_note,
    sox_priority_note,
    trend_note,
)


def _ohlc(end="2026-11-06", n=300, seed=0):
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range(end=end, periods=n)
    c = 1000 * np.exp(rng.normal(0, 0.01, n).cumsum())
    o = c * (1 + rng.normal(0, 0.003, n))
    return pd.DataFrame({"Open": o, "High": np.maximum(o, c) * 1.01, "Low": np.minimum(o, c) * 0.99, "Close": c}, index=idx)


def test_trend_note_depends_on_validation():
    d = _ohlc()
    t = date(2026, 11, 6)
    assert "25日線乖離" in trend_note(d, t, "5803", None)["text"]
    v = {"tests": [{"name": "乖離:フジクラ:下方乖離（-2σ以下）", "significant": False}]}
    assert "関係なし" in trend_note(d, t, "5803", v)["text"]
    print("✅ test_trend_note_depends_on_validation")


def test_fiscal_and_dividend_notes():
    assert "3月決算（中間 9月）" in fiscal_note(3)["text"]
    assert fiscal_note(None) is None
    assert dividend_note(date(2026, 3, 30), 25.0, 2500.0)["text"].count("1.00%") == 1   # 3/30 は権利落ち日
    assert dividend_note(date(2026, 3, 27), 25.0, 2500.0) is None                       # 権利付き最終日には出さない
    print("✅ test_fiscal_and_dividend_notes")


def test_closing_and_sox_notes():
    d = _ohlc()
    fri = date(2026, 11, 6)
    n = closing_note(d, fri, "6981", True, None)
    assert n and n["text"].startswith("今週の週足")
    assert closing_note(d, date(2026, 11, 4), "6981", True, None) is None              # 水曜は出さない
    assert closing_reversal_dir(fri, True, None) == -1
    assert closing_reversal_dir(date(2026, 11, 30), True, False) == 1                   # 月末は月足で見る
    assert sox_priority_note(("SOXX", "QQQ"), 1.5, -1) is not None
    assert sox_priority_note(("SOXX",), -1.5, -1) is None                               # 同じ向き
    assert sox_priority_note(("GLW",), 1.5, -1) is None                                 # SOX を見ない銘柄
    notes = build_card_notes(code="6981", ohlc=d, today=fri, validation=None, fiscal_month=3,
                             us_proxies=("SOXX",), sox_change_pct=1.5, nikkei_week_positive=True,
                             nikkei_month_positive=None)
    assert len(notes) >= 3
    print("✅ test_closing_and_sox_notes")


if __name__ == "__main__":
    test_trend_note_depends_on_validation()
    test_fiscal_and_dividend_notes()
    test_closing_and_sox_notes()
    print("\n🎉 All card_notes tests passed!")
