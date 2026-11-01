"""
Daytrade Signal Board - メインエントリポイント (v6)

変更点:
  - エラー時にスタブデータを使わず、エラーページを生成して exit 1
  - 判定履歴を docs/history.json に追記
  - キリバン水準セクションを削除（運用上ほとんど効かないため）
  - その日の判定一式を docs/archive/YYYY-MM-DD.json に残す（振り返りページ用）
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from kiriban import build_daily_signal
from market_direction import build_market_direction, build_market_direction_v2
from card_notes import build_card_notes
from trade_plan import build_trade_plan
from us_range_study import today_multiplier
from stock_page import write_stock_pages
from sector_mapping import build_all_sector_signals, get_all_us_tickers
from warnings_module import check_index_deviation_warning, get_all_warnings, us_was_closed
from market_context import build_market_context
from archive import backfill_from_history, save_daily_archive, write_archive_index
from history import append_today_signal, load_history, get_recent_entries
from stock_picks import build_stock_picks
from focus_stocks import build_all_focus_signals, get_focus_us_tickers
from trading_rules import build_trading_rules
from run_timing import late_notice, read_existing_timestamp, should_skip_scheduled_run
from focus_stocks import FOCUS_STOCKS
from morning_range import build_morning_signal, fetch_announcements
from expected_range import RANGE_WINDOW, build_range_signal, ohlc_to_records, records_to_ohlc
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


def _load_range_validation(docs_dir: Path) -> dict[str, dict] | None:
    """過去のバックテスト結果から、銘柄コード → 想定値幅の検証結果（RANGE_WINDOW分）を返す"""
    backtest_path = docs_dir / "backtest_report.json"
    if not backtest_path.exists():
        return None
    try:
        with backtest_path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        out = {}
        for r in (data.get("range_validation") or {}).get("results", []):
            for v in r.get("by_window", []):
                if v.get("window") == RANGE_WINDOW and v.get("r") is not None:
                    out[r["code"]] = v
        return out
    except (json.JSONDecodeError, OSError, KeyError) as e:
        print(f"⚠️ Failed to load range validation: {e}")
        return None


def _load_morning_validation(docs_dir: Path) -> dict:
    """過去のバックテスト結果から、9時台の値幅の比・決算明けの比・寄り直後5分の表を返す。"""
    backtest_path = docs_dir / "backtest_report.json"
    if not backtest_path.exists():
        return {}
    try:
        with backtest_path.open("r", encoding="utf-8") as f:
            return json.load(f).get("morning_range") or {}
    except (json.JSONDecodeError, OSError) as e:
        print(f"⚠️ Failed to load morning range validation: {e}")
        return {}


def _load_idea_validation(docs_dir: Path) -> dict:
    """週次レポートの「記事の考え方の検証」（金曜・月末・権利日の警告に検証結果を添える）"""
    backtest_path = docs_dir / "backtest_report.json"
    if not backtest_path.exists():
        return {}
    try:
        with backtest_path.open("r", encoding="utf-8") as f:
            return json.load(f).get("ideas") or {}
    except (json.JSONDecodeError, OSError) as e:
        print(f"⚠️ Failed to load idea validation: {e}")
        return {}


def _load_report_key(docs_dir: Path, key: str) -> dict:
    """週次レポート（backtest_report.json）の1項目"""
    path = docs_dir / "backtest_report.json"
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f).get(key) or {}
    except (json.JSONDecodeError, OSError) as e:
        print(f"⚠️ Failed to load {key}: {e}")
        return {}


def _load_level_validation(docs_dir: Path) -> dict:
    """週次レポートの「上値・下値の目安は止まりやすいか」"""
    path = docs_dir / "backtest_report.json"
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f).get("levels") or {}
    except (json.JSONDecodeError, OSError) as e:
        print(f"⚠️ Failed to load level validation: {e}")
        return {}


def _load_us_events(root: Path) -> list[dict]:
    """data/us_events.csv（雇用統計の日付と結果の良し悪しを手で書く）"""
    path = root / "data" / "us_events.csv"
    if not path.exists():
        return []
    import csv
    with path.open(encoding="utf-8") as f:
        return [r for r in csv.DictReader(f) if r.get("date")]


def _fetch_dividend(ticker: str, today) -> float | None:
    """権利落ち日だけ、直近の1株配当を取る"""
    from warnings_module import dividend_days
    days = dividend_days(today.year, today.month) if today.month in (3, 6, 9, 12) else None
    if not days or today != days[1]:
        return None
    try:
        import yfinance as yf
        d = yf.Ticker(ticker).dividends
        return float(d.iloc[-1]) if len(d) else None
    except Exception as e:
        print(f"⚠️ Failed to fetch dividend for {ticker}: {e}")
        return None


def _fetch_focus_ohlc(use_stub: bool) -> dict[str, list[dict]]:
    """承認銘柄の日足を取得してJSON用の形で返す。失敗してもページ生成は続ける。"""
    if use_stub:
        return _build_stub_focus_ohlc()
    try:
        from fetch_data import fetch_daily_ohlc
        raw = fetch_daily_ohlc([s.ticker for s in FOCUS_STOCKS], period="2y")
        # 2年分すべて残す（25日線乖離のσ・52週高値に使う）
        return {s.code: ohlc_to_records(raw[s.ticker], n=600) for s in FOCUS_STOCKS if s.ticker in raw}
    except Exception as e:
        print(f"⚠️ Failed to fetch focus OHLC (想定値幅はスキップ): {e}")
        return {}


def _fetch_older_highs(focus_ohlc_full: dict[str, list[dict]], use_stub: bool) -> dict[str, float]:
    """日足（2年分）より前の期間の最高値（月足・上場来）。上場来高値の判定に使う。取れなければ空"""
    if use_stub or not focus_ohlc_full:
        return {}
    try:
        import pandas as pd
        import yfinance as yf
        out = {}
        for s in FOCUS_STOCKS:
            recs = focus_ohlc_full.get(s.code)
            if not recs:
                continue
            m = yf.download(s.ticker, period="max", interval="1mo", progress=False, auto_adjust=True)
            if isinstance(m.columns, pd.MultiIndex):
                m.columns = m.columns.get_level_values(0)
            first = pd.Timestamp(recs[0]["date"]).to_period("M")
            older = m[m.index.to_period("M") < first]["High"].dropna()
            if len(older):
                out[s.code] = float(older.max())
        return out
    except Exception as e:
        print(f"⚠️ Failed to fetch all-time highs (上場来高値の判定はスキップ): {e}")
        return {}


def main() -> int:
    output_dir = Path(__file__).resolve().parent.parent / "docs"
    output_dir.mkdir(exist_ok=True)
    html_path = output_dir / "index.html"
    history_path = output_dir / "history.json"

    # 遅れて動いた定期実行が、今日の寄り前の版を上書きしないようにする
    event_name = os.environ.get("GITHUB_EVENT_NAME", "")
    now_jst = datetime.now(JST)
    existing_ts = read_existing_timestamp(output_dir / "signal.json")
    if should_skip_scheduled_run(event_name, now_jst, existing_ts):
        print(f"ℹ️ 今日の寄り前の版（{existing_ts:%H:%M}）があるため、"
              f"{now_jst:%H:%M} に遅れて動いた定期実行では上書きしません。")
        return 0

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

    generated_at = datetime.fromisoformat(market["timestamp"])
    today = generated_at.date()
    notice = late_notice(generated_at)

    # 警告（時間外の動きは今朝の現対で見る）
    futures_diff = market["nikkei_futures"]["price"] - market["nikkei"]["prev_close"]
    idea_validation = _load_idea_validation(output_dir)
    sp500_change = market["us_markets"].get("sp500_change_pct")
    warnings = get_all_warnings(
        today=today,
        nikkei_week_is_positive=market["nikkei"].get("week_is_positive"),
        nikkei_month_is_positive=market["nikkei"].get("month_is_positive"),
        overnight_diff=futures_diff,
        validation=idea_validation,
        sp500_change_pct=sp500_change,
        us_events=_load_us_events(output_dir.parent),
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
    direction_signal = build_market_direction(
        nasdaq_change_pct=market["us_markets"]["nasdaq_change_pct"],
        dow_change_pct=market["us_markets"]["dow_change_pct"],
        sox_change_pct=market["us_markets"]["sox_change_pct"],
        futures_diff=futures_diff,
        us_closed=us_was_closed(today),
    )
    # 指数の理想乖離（判定の向きによって出す警告）
    idx_warn = check_index_deviation_warning(direction_signal.verdict, market.get("index_deviation"),
                                             (idea_validation.get("extra") or {}).get("index_dev"))
    if idx_warn:
        warnings.append(idx_warn)
        warnings.sort(key=lambda w: {"high": 0, "medium": 1, "low": 2}.get(w.severity, 9))
    # 参考：ドル円・S&P500 を足した判定（検証では当たりが上がらなかったので、判定には使わない）
    direction_v2 = None
    if sp500_change is not None:
        direction_v2 = build_market_direction_v2(
            market["us_markets"]["nasdaq_change_pct"], market["us_markets"]["dow_change_pct"],
            market["us_markets"]["sox_change_pct"], sp500_change,
            market.get("fx_commodities", {}).get("usdjpy_change_pct", 0.0), futures_diff).as_dict()
        direction_v2["sp500_change_pct"] = sp500_change
        direction_v2["usdjpy_change_pct"] = market.get("fx_commodities", {}).get("usdjpy_change_pct", 0.0)
        v = idea_validation.get("v2") or {}
        if v.get("v1") and v.get("v2"):
            direction_v2["evidence"] = (f'検証（{v["split"]}〜）：今の判定 的中 {v["v1"]["hit"] * 100:.0f}% ／ '
                                        f'ドル円・S&P500 入り {v["v2"]["hit"] * 100:.0f}%（手出し無用が {v["v2"]["hands_off_share"] * 100:.0f}% に増える）')

    # キリバン（参考用にJSONには保存するが、ダッシュボードには表示しない）
    kiriban_signal = build_daily_signal(
        nikkei_prev_close=market["nikkei"]["prev_close"],
        nikkei_futures=market["nikkei_futures"]["price"],
    )

    # 節目（大台）：日経・1570・1357 と、その日の判定に合わせた使い方（L6-2・L7-3）
    from kiriban import build_big_levels, kiriban_day_hint
    from round_study import levels_evidence
    round_val = _load_report_key(output_dir, "round_study")
    levels = {
        "rows": build_big_levels(market["nikkei"].get("prev_close"), (market.get("etf_1570") or {}).get("prev_close"),
                                 (market.get("etf_1357") or {}).get("prev_close")),
        "hint": kiriban_day_hint(direction_signal.verdict),
        "evidence": levels_evidence(round_val, direction_signal.verdict),
    }
    kiriban_signal["big_levels"] = levels

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

    # 想定値幅（承認銘柄）
    focus_ohlc_full = _fetch_focus_ohlc(use_stub)          # 25日線の乖離のσに2年分使う
    focus_ohlc = {k: v[-130:] for k, v in focus_ohlc_full.items()}   # JSON に残すのは約半年分
    market["focus_ohlc"] = focus_ohlc
    range_validation = _load_range_validation(output_dir) or {}
    for f in focus_signals_dict:
        f["range"] = build_range_signal(
            records_to_ohlc(focus_ohlc.get(f["code"])),
            today=today,
            validation=range_validation.get(f["code"]),
        )

    # 9時台の値幅の目安・決算明けの注意（承認・試用銘柄）
    morning_val = _load_morning_validation(output_dir)
    tickers = {s.code: s.ticker for s in FOCUS_STOCKS}
    for f in focus_signals_dict:
        announcements = [] if use_stub else fetch_announcements(tickers[f["code"]])
        f["range"]["morning"] = build_morning_signal(
            f["range"],
            (morning_val.get("per_code") or {}).get(f["code"]),
            morning_val.get("first5_table"),
            announcements,
            today,
        )

    # 今日の作戦（100株の損切り・利確の幅）と上値・下値の目安
    level_val = _load_level_validation(output_dir)
    us_range_val = _load_report_key(output_dir, "us_range")
    for f in focus_signals_dict:
        f["us_range"] = today_multiplier(us_range_val, f["code"], f.get("us_avg_change_pct"))
        mult = (f["us_range"] or {}).get("multiplier")
        f["plan"] = build_trade_plan(records_to_ohlc(focus_ohlc_full.get(f["code"])), f["range"],
                                     f["range"].get("morning"), level_val, mult)

    # カードの注意書き（25日線乖離・月足、金曜・月末の週足、権利落ち日の配当、SOX優先、決算月）
    by_code = {s.code: s for s in FOCUS_STOCKS}
    older_highs = _fetch_older_highs(focus_ohlc_full, use_stub)
    for f in focus_signals_dict:
        st = by_code[f["code"]]
        f["notes"] = build_card_notes(
            code=f["code"], ohlc=records_to_ohlc(focus_ohlc_full.get(f["code"])), today=today,
            validation=idea_validation, fiscal_month=st.fiscal_month, us_proxies=st.us_proxies,
            sox_change_pct=market["us_markets"].get("sox_change_pct"),
            nikkei_week_positive=market["nikkei"].get("week_is_positive"),
            nikkei_month_positive=market["nikkei"].get("month_is_positive"),
            dividend=None if use_stub else _fetch_dividend(st.ticker, today),
            verdict=direction_signal.verdict, older_high=older_highs.get(f["code"]),
            high_evidence=(f.get("validation") or {}).get("at_high"),
        )
        # 節目を少し割ったところの反発（L6-3）：検証で有意なときだけ
        from round_study import big_number_note, dip_card_note
        recs = [r for r in (focus_ohlc_full.get(f["code"]) or []) if r["date"] < today.isoformat()]
        last_close = recs[-1]["close"] if recs else None
        dip = dip_card_note(round_val.get("dip"), last_close)
        # 株価の大台の手前（L6-4・手法10）
        big = big_number_note(last_close, (round_val.get("big_number") or {}).get("all"))
        f["notes"] = [n for n in (big,) if n] + f["notes"] + [n for n in (dip,) if n]

    # レンジ型の日（L6-5）：手出し無用の日だけ。承認・試用銘柄が決算明けなら出さない
    from market_direction import build_range_day
    us = market["us_markets"]
    rd_val = (_load_report_key(output_dir, "checkpoints") or {}).get("range_day") or {}
    rd_ev = ""
    if rd_val.get("n_range") and rd_val.get("significant"):
        rd_ev = (f'検証（直近60日・{rd_val["n_range"]}日）：寄り→10:00 が上げた日 {rd_val["up_range"] * 100:.0f}%'
                 f'（ほかの日 {rd_val["up_other"] * 100:.0f}%）')
    range_day = build_range_day(
        direction_signal.verdict,
        [us.get("nasdaq_change_pct"), us.get("dow_change_pct"), us.get("sox_change_pct")],
        direction_signal.futures_diff,
        earnings_names=[f["name"] for f in focus_signals_dict if ((f.get("range") or {}).get("morning") or {}).get("earnings_today")],
        evidence=rd_ev,
    )

    # JSON
    signal = {
        "range_day": range_day,
        "market": market,
        "warnings": warnings_dict,
        "market_context": market_ctx.as_dict(),
        "direction": direction_signal.as_dict(),
        "direction_v2_reference": direction_v2,
        "kiriban": kiriban_signal,
        "sectors": sector_signals_dict,
        "recent_history": recent_history,
        "stock_picks": stock_picks.as_dict(),
        "trading_rules": trading_rules.as_dict(),
        "generated_after_open": notice is not None,
        "focus_stocks": focus_signals_dict,
    }
    json_path = output_dir / "signal.json"
    with json_path.open("w", encoding="utf-8") as f:
        json.dump(signal, f, ensure_ascii=False, indent=2, default=str)
    print(f"✅ Signal JSON: {json_path}")

    # 日付ごとの記録（daytrade-journal の振り返りページで使う）
    archive_dir = output_dir / "archive"
    if not save_daily_archive(archive_dir, signal, generated_at):
        print(f"ℹ️ archive/{today}.json は寄り前の版を残します（今回は寄り後の生成）")
    backfilled = backfill_from_history(archive_dir, history)
    if backfilled:
        print(f"ℹ️ archive に履歴から {backfilled} 日分を補完しました")
    write_archive_index(archive_dir)

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
        late_notice=notice,
        direction_v2=direction_v2,
        levels=levels,
        range_day=range_day,
    )

    # 銘柄ごとのページ（図と作戦）
    write_stock_pages(output_dir / "stock", focus_signals_dict, focus_ohlc, direction_signal.as_dict(),
                      warnings_dict, trading_rules.as_dict(), f"{generated_at:%Y-%m-%d %H:%M} JST")

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
    if notice:
        print(f"⚠️ 寄り付き後の生成（{generated_at:%H:%M}）")
    print("=" * 60)

    return 0


def _build_stub_focus_ohlc() -> dict[str, list[dict]]:
    """開発用: 承認銘柄の合成日足"""
    import numpy as np
    import pandas as pd

    out = {}
    idx = pd.bdate_range(end="2026-04-23", periods=90)
    for k, (code, base) in enumerate((("5803", 4200.0), ("9984", 6500.0), ("6981", 6600.0))):
        rng = np.random.default_rng(10 + k)
        close = base * np.exp(rng.normal(0, 0.02, len(idx)).cumsum())
        open_ = close * np.exp(rng.normal(0, 0.01, len(idx)))
        spread = np.abs(rng.normal(0.025, 0.008, len(idx)))
        high = np.maximum(open_, close) * (1 + spread / 2)
        low = np.minimum(open_, close) * (1 - spread / 2)
        df = pd.DataFrame({"Open": open_, "High": high, "Low": low, "Close": close}, index=idx)
        out[code] = ohlc_to_records(df)
    return out


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
        "etf_1357": {"prev_close": 9850.0},
        "us_markets": {
            "nasdaq_change_pct": -1.2,
            "dow_change_pct": -0.8,
            "sox_change_pct": -2.5,
            "sp500_change_pct": -1.0,
        },
        "index_deviation": {"ナスダック": {"dev": -6.0, "z": -3.2, "date": "2026-10-30"},
                            "SOX": {"dev": -9.0, "z": -2.6, "date": "2026-10-30"}},
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
            "ARM": 1.4, "QQQ": -1.1, "SOXX": -2.4,
        },
    }


if __name__ == "__main__":
    sys.exit(main())
