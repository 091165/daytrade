"""
stock_page のユニットテスト
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from stock_page import _spread_labels, chart_svg, render_stock_page, ruler_svg


def _plan():
    return {"prev_close": 3918.0,
            "bracket": {"stop_width": 40, "target_width": 120, "stop_yen": 4000, "target_yen": 12000,
                        "morning_width": 120.0, "capped": False, "reachable": True, "skip": False, "notes": []},
            "levels": {"up": [{"kind": "キリ（大）", "price": 4000.0, "dist": 82.0, "side": "up"}],
                       "down": [{"kind": "前日安値", "price": 3866.0, "dist": -52.0, "side": "down"}]},
            "level_evidence": "検証…"}


def _records():
    return [{"date": f"2026-10-{d:02d}", "open": 3900 + d, "high": 3950 + d, "low": 3850 + d, "close": 3910 + d}
            for d in range(1, 31)]


def test_chart_and_ruler():
    svg = chart_svg(_records(), _plan(), {"as_of": "2026-10-30", "pred_yen": 198})
    assert svg.startswith("<svg") and "4,000 キリ（大）" in svg and "前日終値" in svg and "clip-path" in svg
    assert svg.count('class="c-up"') + svg.count('class="c-down"') == 10
    assert "データ" in chart_svg(None, _plan(), None)
    r = ruler_svg(_plan(), "buy")
    assert "損切り -40円" in r and "利確 +120円" in r and "売りで入った" not in r
    both = ruler_svg(_plan(), "both")
    assert "買いで入った" in both and "売りで入った" in both and "損切り +40円" in both
    assert _spread_labels([10, 12, 30]) == [10, 23, 36]
    print("✅ test_chart_and_ruler")


def test_render_page():
    f = {"code": "5803", "name": "フジクラ", "direction": "bearish", "trial": False, "plan": _plan(),
         "range": {"available": False, "reason": "x"}, "us_avg_change_pct": -2.4, "us_label": "光", "us_changes": {"GLW": -1.8},
         "gap_text": "安く", "intraday_text": "読めない", "validation": None, "notes": [{"text": "注意", "level": "info"}],
         "conflict_with_market": False}
    html = render_stock_page(f, _records(), {"verdict": "sell_bias", "verdict_label": "売りポジ主体"}, [],
                             {"day_rules": ["売りで統一"], "always_rules": ["10:00前に終了"]}, "2026-11-02 06:40 JST")
    assert "場中モード" in html and '"stop":40' in html and '"take":120' in html and '"maxLosses":2' in html
    assert "フジクラ" in html and "../index.html" in html and "売りで入ったとき" in html and "10:00前に終了" in html
    print("✅ test_render_page")


if __name__ == "__main__":
    test_chart_and_ruler()
    test_render_page()
    print("\n🎉 All stock_page tests passed!")
