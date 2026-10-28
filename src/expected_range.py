"""
承認銘柄（フジクラ・SBG）の想定値幅

目的:
  方向の予測ではなく「今日はどれくらい動きそうか」を寄り前に円で見積もり、
  1トレードの損切り（-5,000円）や1日の上限（-1万円）と比べられるようにする。

値幅の定義:
  高値 − 安値（前日終値に対する%）。寄り前の窓は含めない。
  デイトレで実際に経験するのは寄り後の値動きなので。

予測:
  直近 RANGE_WINDOW 営業日の値幅の平均。
  「最近の値幅が大きいと、今日も大きい」という性質（ボラティリティの持続性）を使う。
  当たり具合は週次バックテスト（validate_range_prediction）で検証する。

注意:
  自動実行が場中にずれ込むと、未完成の「今日の足」が混ざる。必ず今日より前の足だけを使う。
"""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

RANGE_WINDOW = 10        # 予測に使う直近の営業日数
BASELINE_WINDOW = 60     # 「いつもの値幅」を測る期間
VALIDATION_WINDOWS = (5, 10, 20)
SHARES = 100
STOP_YEN = 5_000
DAILY_LIMIT_YEN = 10_000
ROUGH_DAY_QUANTILE = 0.8   # 「荒れた日」= 5日に1日


def _norm_index(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    idx = pd.to_datetime(df.index)
    if getattr(idx, "tz", None) is not None:
        idx = idx.tz_localize(None)
    df.index = idx.normalize()
    df = df[~df.index.duplicated(keep="last")].sort_index()
    return df.dropna(subset=["High", "Low", "Close"])


def daily_range_pct(ohlc: pd.DataFrame) -> pd.Series:
    """各日の値幅（高値−安値）を前日終値に対する%で返す。"""
    df = _norm_index(ohlc)
    prev_close = df["Close"].shift(1)
    rng = (df["High"] - df["Low"]) / prev_close * 100
    return rng[(prev_close > 0) & (df["High"] >= df["Low"])].dropna()


def completed_bars(ohlc: pd.DataFrame, today: date) -> pd.DataFrame:
    """今日（未完成の可能性がある足）以降を除いた、確定済みの日足だけを返す。"""
    df = _norm_index(ohlc)
    return df[df.index < pd.Timestamp(today)]


def predicted_vs_actual(ohlc: pd.DataFrame, window: int = RANGE_WINDOW) -> pd.DataFrame:
    """各日について「前日までの直近window日の平均値幅（予測）」と「その日の値幅（実際）」を並べる。"""
    rng = daily_range_pct(ohlc)
    pred = rng.rolling(window).mean().shift(1)     # 当日を含まない過去だけで予測
    df = pd.DataFrame({"pred": pred, "actual": rng}).dropna()
    return df[df["pred"] > 0]


def validate_range_prediction(ohlc: pd.DataFrame, window: int = RANGE_WINDOW) -> dict:
    """直近window日の平均値幅で、翌日の値幅をどれだけ当てられるかを検証する（純粋関数）。

    Returns:
        n, r, p, within_rate（実際がこの予測以下に収まった率）,
        median_ratio（実際/予測 の中央値）, rough_ratio（同80%点 = 5日に1日の荒れ具合）
    """
    from scipy import stats

    df = predicted_vs_actual(ohlc, window)
    n = len(df)
    if n < 30:
        return {"window": window, "n": n, "r": None, "p": None, "within_rate": None,
                "median_ratio": None, "rough_ratio": None}
    r, p = stats.pearsonr(df["pred"].values, df["actual"].values)
    ratio = df["actual"] / df["pred"]
    return {
        "window": window,
        "n": n,
        "r": float(r),
        "p": float(p),
        "within_rate": float((df["actual"] <= df["pred"]).mean()),
        "median_ratio": float(ratio.median()),
        "rough_ratio": float(ratio.quantile(ROUGH_DAY_QUANTILE)),
    }


def build_range_signal(
    ohlc: pd.DataFrame | None,
    today: date,
    validation: dict | None = None,
) -> dict:
    """今日の想定値幅を円で組み立てる。

    Args:
        ohlc: 日足（Open/High/Low/Close）。今日の足が混ざっていてもよい（除外する）
        today: 日本の今日の日付
        validation: validate_range_prediction の結果（RANGE_WINDOW分）。あれば荒れた日の幅を出す
    """
    if ohlc is None or len(ohlc) == 0:
        return {"available": False, "reason": "データなし"}
    bars = completed_bars(ohlc, today)
    rng = daily_range_pct(bars)
    if len(rng) < RANGE_WINDOW:
        return {"available": False, "reason": "データ不足"}

    pred_pct = float(rng.iloc[-RANGE_WINDOW:].mean())
    last_close = float(bars["Close"].iloc[-1])
    pred_yen = pred_pct / 100 * last_close            # 1株あたりの値幅（円）
    lot_yen = pred_yen * SHARES

    base = rng.iloc[-BASELINE_WINDOW:]
    baseline_pct = float(base.median()) if len(base) >= 20 else None
    level_ratio = pred_pct / baseline_pct if baseline_pct else None
    if level_ratio is None:
        level, level_text = "unknown", ""
    elif level_ratio >= 1.3:
        level, level_text = "high", f"最近の値幅はいつもより大きい（普段の{level_ratio:.1f}倍）"
    elif level_ratio <= 0.75:
        level, level_text = "low", f"最近の値幅はいつもより小さい（普段の{level_ratio:.1f}倍）"
    else:
        level, level_text = "normal", "最近の値幅は普段どおり"

    rough_ratio = (validation or {}).get("rough_ratio")
    rough_yen = pred_yen * rough_ratio if rough_ratio else None

    stop_width = STOP_YEN / SHARES                    # 損切り-5,000円 = 1株あたり何円幅か
    stop_frac = stop_width / pred_yen if pred_yen > 0 else None

    return {
        "available": True,
        "as_of": bars.index[-1].strftime("%Y-%m-%d"),
        "last_close": last_close,
        "window": RANGE_WINDOW,
        "pred_pct": pred_pct,
        "pred_yen": pred_yen,
        "lot_yen": lot_yen,
        "shares": SHARES,
        "rough_yen": rough_yen,
        "level": level,
        "level_text": level_text,
        "stop_yen": STOP_YEN,
        "stop_width": stop_width,
        "stop_frac": stop_frac,
        "validation": validation,
    }


def ohlc_to_records(ohlc: pd.DataFrame, n: int = 90) -> list[dict]:
    """JSONに入れるための形に変換（直近n本）。"""
    df = _norm_index(ohlc).tail(n)
    return [
        {"date": d.strftime("%Y-%m-%d"), "open": float(r["Open"]), "high": float(r["High"]),
         "low": float(r["Low"]), "close": float(r["Close"])}
        for d, r in df.iterrows()
    ]


def records_to_ohlc(records: list[dict] | None) -> pd.DataFrame | None:
    if not records:
        return None
    df = pd.DataFrame(records)
    df.index = pd.to_datetime(df.pop("date"))
    return df.rename(columns={"open": "Open", "high": "High", "low": "Low", "close": "Close"})


def run_range_validation(period: str = "1y") -> list[dict]:
    """承認銘柄の想定値幅の当たり具合を、実データで検証する。"""
    import yfinance as yf
    from focus_stocks import FOCUS_STOCKS

    tickers = [s.ticker for s in FOCUS_STOCKS]
    print(f"Fetching {len(tickers)} tickers (OHLC) for range validation, {period}...")
    data = yf.download(tickers=" ".join(tickers), period=period, interval="1d",
                       progress=False, group_by="ticker", auto_adjust=True, threads=True)
    results = []
    for s in FOCUS_STOCKS:
        try:
            ohlc = data[s.ticker][["Open", "High", "Low", "Close"]]
        except KeyError:
            results.append({"code": s.code, "name": s.name, "by_window": []})
            continue
        by_window = [validate_range_prediction(ohlc, w) for w in VALIDATION_WINDOWS]
        results.append({"code": s.code, "name": s.name, "by_window": by_window})
        for v in by_window:
            if v["r"] is not None:
                print(f"  {s.name:8s} 直近{v['window']:2d}日平均 r={v['r']:+.3f} n={v['n']} "
                      f"予測以内{v['within_rate']*100:.0f}% 実際/予測 中央{v['median_ratio']:.2f} 80%点{v['rough_ratio']:.2f}")
    return results
