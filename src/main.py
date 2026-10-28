"""
Daytrade Signal Board - メインエントリポイント (v6)

変更点:
  - エラー時にスタブデータを使わず、エラーページを生成して exit 1
  - 判定履歴を docs/history.json に追記
  - キリバン水準セクションを削除（運用上ほとんど効かないため）
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from kiriban import build_daily_signal
from market_direction import build_market_direction
from sector_mapping import build_all_sector_signals, get_all_us_tickers
from warnings_module import get_all_warnings
from market_context import build_market_context
from history import append_today_signal, load_history, get_recent_entries
from stock_picks import build_stock_picks
from focus_stocks import build_all_focus_signals, get_focus_us_tickers
from trading_rules import build_trading_rules
from dashboard import render_dashboard, render_error_page


JST = ZoneInfo("Asia/Tokyo")

# Backtest結果（任意）を読み込んで、各セクターの実測 r 値を反映する
def _load_sector_validation(docs_dir: Path) -> dict[str, dict] | None:
    """過去のバックテスト結果から、セクター名 → {verdict, pearson_r} のマップを返す"""
    backtest_path = docs_dir / "backtest_report.json"
    if not backtest_path.exists():
        return None
    try:
        with backtest_path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        results = data.get("sector_validation", {}).get("results", [])
        return {
            r["sector_name"]: {
                "verdict": r["verdict"],
                "pearson_r": r["pearson_r"],
                "verdict_label": r["verdict_label"],
            }
            for r in results
        }
    except (json.JSONDecodeError, OSError, KeyError) as e:
        print(f"⚠️ Failed to load backtest report: {e}")
        return None


def _load_focus_validation(docs_dir: Path) -> dict[str, dict] | None:
    """過去のバックテスト結果から、銘柄コード → 個別予測の検証結果 のマップを返す"""
    backtest_path = docs_dir / "backtest_report.json"
    if not backtest_path.exists():
        return None
    try:
        with backtest_path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        fv = data.get("focus_validation") or {}
        return {r["code"]: r for r in fv.get("results", []) if r.get("n", 0) > 0}
    except (json.JSONDecodeError, OSError, KeyError) as e:
        print(f"⚠️ Failed to load focus validation: {e}")
        return None


def main() -> int:
    output_dir = Path(__file__).resolve().parent.parent / "docs"
    output_dir.mkdir(exist_ok=True)
    html_path = output_dir / "index.html"
    history_path = output_dir / "history.json"

    us_tickers = sorted(set(get_all_us_tickers()) | set(get_focus_us_tickers()))

    # データ取得（失敗時はエラーページを出して終了）
    use_stub = os.environ.get("USE_STUB", "0") == "1"
    if use_stub:
        print("ℹ️ USE_STUB=1: Using synthetic data for development.")
        market = _build_stub_data()
    else:
        try:
            from fetch_data import fetch_all_market_data
            market = fetch_all_market_data(us_sector_tickers=us_tickers)
        except Exception as e:
            err_msg = f"Failed to fetch live market data: {e}"
            print(f"❌ {err_msg}")
            render_error_page(html_path, err_msg)
            return 1

    today = datetime.fromisoformat(market["timestamp"]).date()

    # 警告
    warnings = get_all_warnings(
        today=today,
        nikkei_week_is_positive=market["nikkei"].get("week_is_positive"),
        nikkei_month_is_positive=market["nikkei"].get("month_is_positive"),
    )

    # 為替・コモディティ
    fx = market.get("fx_commodities", {})
    market_ctx = build_market_context(
        usdjpy_price=fx.get("usdjpy_price", 150.0),
        usdjpy_change_pct=fx.get("usdjpy_change_pct", 0.0),
        gold_price=fx.get("gold_price", 2600.0),
        gold_change_pct=fx.get("gold_change_pct", 0.0),
        oil_price=fx.get("oil_price", 70.0),
        oil_change_pct=fx.get("oil_change_pct", 0.0),
    )

    # 日経方針
    futures_diff = market["nikkei_futures"]["price"] - market["nikkei"]["prev_close"]
    direction_signal = build_market_direction(
        nasdaq_change_pct=market["us_markets"]["nasdaq_change_pct"],
        dow_change_pct=market["us_markets"]["dow_change_pct"],
        sox_change_pct=market["us_markets"]["sox_change_pct"],
        futures_diff=futures_diff,
    )

    # キリバン（参考用にJSONには保存するが、ダッシュボードには表示しない）
    kiriban_signal = build_daily_signal(
        nikkei_prev_close=market["nikkei"]["prev_close"],
        nikkei_futures=market["nikkei_futures"]["price"],
    )

    # セクター
    sector_signals = build_all_sector_signals(market.get("us_sector_changes", {}))
    sector_signals_dict = [s.as_dict() for s in sector_signals]

    # バックテスト結果でセクターを補完
    sector_validation = _load_sector_validation(output_dir)
    if sector_validation:
        for s in sector_signals_dict:
            v = sector_validation.get(s["sector_name"])
            if v:
                s["validation_verdict"] = v["verdict"]
                s["validation_pearson_r"] = v["pearson_r"]
                s["validation_label"] = v["verdict_label"]

    # 履歴に追記
    warnings_dict = [w.as_dict() for w in warnings]
    history = append_today_signal(
        history_path=history_path,
        direction_signal=direction_signal.as_dict(),
        market=market,
        warnings=warnings_dict,
    )
    recent_history = get_recent_entries(history, n=5)

    # 注目銘柄（手出し無用日のみ表示）
    stock_picks = build_stock_picks(
        direction_verdict=direction_signal.verdict,
        sector_signals=sector_signals_dict,
    )

    # マイルール連動バナー
    trading_rules = build_trading_rules(
        verdict=direction_signal.verdict,
        confidence=direction_signal.confidence,
    )

    # 承認銘柄（フジクラ・SBG）の個別予測
    focus_signals = build_all_focus_signals(
        us_changes=market.get("us_sector_changes", {}),
        market_verdict=direction_signal.verdict,
        validation_by_code=_load_focus_validation(output_dir),
    )
    focus_signals_dict = [f.as_dict() for f in focus_signals]

    # JSON
    json_path = output_dir / "signal.json"
    with json_path.open("w", encoding="utf-8") as f:
        json.dump(
            {
                "market": market,
                "warnings": warnings_dict,
                "market_context": market_ctx.as_dict(),
                "direction": direction_signal.as_dict(),
                "kiriban": kiriban_signal,
                "sectors": sector_signals_dict,
                "recent_history": recent_history,
                "stock_picks": stock_picks.as_dict(),
                "trading_rules": trading_rules.as_dict(),
                "focus_stocks": focus_signals_dict,
            },
            f,
            ensure_ascii=False,
            indent=2,
            default=str,
        )
    print(f"✅ Signal JSON: {json_path}")

    # HTML
    render_dashboard(
        warnings=warnings_dict,
        market_context=market_ctx.as_dict(),
        direction_signal=direction_signal.as_dict(),
        market=market,
        sector_signals=sector_signals_dict,
        recent_history=recent_history,
        stock_picks=stock_picks.as_dict(),
        output_path=html_path,
        trading_rules=trading_rules.as_dict(),
        focus_signals=focus_signals_dict,
    )

    # サマリ
    print("\n" + "=" * 60)
    if warnings:
        print("⚠️  警告:")
        for w in warnings:
            print(f"   [{w.severity:>6}] {w.label}")
    print(f"📊 本日の日経デイトレ方針: {direction_signal.verdict_label} (確度: {direction_signal.confidence})")
    print(f"📐 現対: {futures_diff:+,.0f}円")
    if market_ctx.combined_note:
        print(f"💱 マクロ警戒: {market_ctx.combined_note}")
    print(f"📅 履歴: {len(history)} 件")
    print("=" * 60)

    return 0


def _build_stub_data() -> dict:
    """開発用スタブデータ（USE_STUB=1のときのみ使用）"""
    return {
        "timestamp": "2026-04-24T08:40:00+09:00",
        "nikkei": {
            "prev_close": 38500.0,
            "change_pct": -0.5,
            "week_is_positive": True,
            "month_is_positive": True,
        },
        "nikkei_futures": {"price": 37400.0},
        "etf_1570": {"prev_close": 26000.0},
        "us_markets": {
            "nasdaq_change_pct": -1.2,
            "dow_change_pct": -0.8,
            "sox_change_pct": -2.5,
        },
        "fx_commodities": {
            "usdjpy_price": 150.2,
            "usdjpy_change_pct": -0.8,
            "gold_price": 2650.0,
            "gold_change_pct": 1.2,
            "oil_price": 72.5,
            "oil_change_pct": -0.3,
        },
        "us_sector_changes": {
            "AMAT": -2.8, "LRCX": -3.1, "KLAC": -2.5, "AMD": -3.5, "NVDA": -2.9,
            "SNDK": -2.2, "WDC": -1.9, "STX": -1.5,
            "CAT": 0.3, "DE": -0.1,
            "XOM": 1.8, "CVX": 1.6, "COP": 2.1,
            "LMT": 2.3, "RTX": 1.9, "BA": 0.8, "HON": 0.4, "NOC": 1.7,
            "VZ": 0.7, "T": 0.5, "TMUS": 0.2,
            "CRM": -1.4, "MSFT": -0.8, "ORCL": -2.1, "ADBE": -1.6, "INTU": -1.2, "NOW": -1.8,
            "UPS": -0.2, "FDX": 0.1,
            "KO": 0.3, "PG": 0.1, "CL": -0.1, "MCD": 0.4,
            "GLW": -1.8, "COHR": -3.2, "LITE": -2.6, "VRT": -2.0,
            "ARM": 1.4, "QQQ": -1.1,
        },
    }


if __name__ == "__main__":
    sys.exit(main())
