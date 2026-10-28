"""
9時台の値幅の目安と、決算明けの注意

背景（2026/10 の検証）:
  - 1日の値幅の目安（直近10日の平均）に、米国株の動きの大きさ・窓の大きさを足しても、当たりはほとんど良くならない
  - 一方、9時台（9:00-10:00）の値幅は、1日の目安からかなりよく当たる（20銘柄・60日で r=0.74）。
    寄り直後の動きを足すと r=0.80 まで上がる
  - 決算発表の翌営業日は、銘柄によって値幅が目安の約2倍になる（フジクラ・村田・ルネサスなど）

ここでは、週次バックテストで
  - 銘柄ごとの「9時台の値幅 ÷ 1日の目安」（中央値）
  - 寄り直後10分の動きの大きさ別の「9時台の値幅 ÷ 1日の目安」（全銘柄まとめ）
  - 決算明けの「1日の値幅 ÷ 1日の目安」
を測り、毎朝のカードで円に直して出す。
"""

from __future__ import annotations

from datetime import date, datetime, time

import numpy as np
import pandas as pd

from expected_range import RANGE_WINDOW, daily_range_pct
from focus_backtest import _norm

MORNING_FROM, MORNING_TO = "09:00", "09:55"     # 5分足の始まりの時刻（9:55の足で10:00まで）
FIRST_END = "09:10"                             # 最初の10分 = これより前に始まった足
OPEN_BY = "09:05"                               # この時刻までに始まる足がない日は除く（寄りが遅れた日・データ欠け）
FIRST5_LEVELS = (0.15, 0.30, 0.45)              # 最初の10分の動き ÷ 1日の目安 の区切り
MIN_DAYS = 20
ANNOUNCE_AFTER = time(15, 0)                    # これ以降の発表は翌営業日に反応する


def morning_rows(m5: pd.DataFrame, daily: pd.DataFrame) -> pd.DataFrame:
    """5分足と日足から、日ごとの 9時台の値幅・1日の目安・最初の10分の動き（すべて前日終値に対する%）を作る。"""
    if m5 is None or daily is None or m5.empty or daily.empty:
        return pd.DataFrame(columns=["morning", "avg10", "first5"])
    x = m5.dropna(subset=["Open", "High", "Low", "Close"])
    if getattr(x.index, "tz", None) is not None:
        x = x.tz_convert("Asia/Tokyo")
    rng = daily_range_pct(daily)
    avg = rng.rolling(RANGE_WINDOW).mean().shift(1)
    close = _norm(daily["Close"])
    rows = []
    for day, g in x.groupby(x.index.date):
        a = g.between_time(MORNING_FROM, MORNING_TO)
        ts = pd.Timestamp(day)
        if len(a) < 6 or ts not in avg.index or pd.isna(avg[ts]):
            continue
        prev = close[close.index < ts]
        if prev.empty:
            continue
        pc = float(prev.iloc[-1])
        # 最初の10分 = 9:10より前に始まった足（1分足でも5分足でも同じ扱い）
        if a.index[0].strftime("%H:%M") > OPEN_BY:
            continue
        first = a[a.index.strftime("%H:%M") < FIRST_END]
        rows.append({
            "date": ts,
            "morning": (a["High"].max() - a["Low"].min()) / pc * 100,
            "avg10": float(avg[ts]),
            "first5": (first["High"].max() - first["Low"].min()) / pc * 100,
        })
    return pd.DataFrame(rows).set_index("date") if rows else pd.DataFrame(columns=["morning", "avg10", "first5"])


def morning_ratio(rows: pd.DataFrame) -> dict:
    """銘柄ごとの「9時台の値幅 ÷ 1日の目安」（中央値と、5日に1日の荒れた日）。"""
    if len(rows) < MIN_DAYS:
        return {"n": len(rows), "ratio": None, "rough_ratio": None}
    r = rows["morning"] / rows["avg10"]
    return {"n": len(rows), "ratio": float(r.median()), "rough_ratio": float(r.quantile(0.8))}


def first5_table(rows: pd.DataFrame) -> list[dict]:
    """最初の10分の動き（÷1日の目安）の大きさ別に、9時台の値幅 ÷ 1日の目安 の中央値を出す（全銘柄まとめ）。"""
    if len(rows) < MIN_DAYS * 4:
        return []
    f = rows["first5"] / rows["avg10"]
    m = rows["morning"] / rows["avg10"]
    edges = (0.0,) + FIRST5_LEVELS + (np.inf,)
    out = []
    for lo, hi in zip(edges[:-1], edges[1:]):
        sel = (f >= lo) & (f < hi)
        if sel.sum() >= 10:
            out.append({"from": lo, "to": None if np.isinf(hi) else hi, "n": int(sel.sum()), "ratio": float(m[sel].median())})
    return out


def reaction_days(announcements: list[datetime], trading_days: pd.DatetimeIndex) -> list[pd.Timestamp]:
    """決算発表の日時から、値動きに反応する営業日（引け後・時刻不明なら翌営業日、場中なら当日）を返す。"""
    out = []
    for a in announcements:
        ts = pd.Timestamp(a)
        if ts.tzinfo is not None:
            ts = ts.tz_convert("Asia/Tokyo").tz_localize(None)
        d = ts.normalize()
        after_close = ts.time() >= ANNOUNCE_AFTER or ts.time() == time(0, 0)
        later = trading_days[trading_days > d] if after_close else trading_days[trading_days >= d]
        if len(later):
            out.append(later[0])
    return out


def earnings_ratio(daily: pd.DataFrame, announcements: list[datetime]) -> dict:
    """決算明けの「1日の値幅 ÷ 1日の目安」の平均。"""
    rng = daily_range_pct(daily)
    avg = rng.rolling(RANGE_WINDOW).mean().shift(1)
    days = [d for d in reaction_days(announcements, rng.index) if d in avg.index and not pd.isna(avg[d])]
    if not days:
        return {"n": 0, "ratio": None}
    r = [float(rng[d] / avg[d]) for d in days]
    return {"n": len(r), "ratio": float(np.mean(r))}


def earnings_today(announcements: list[datetime], today: date, prev_trading_day: date) -> bool:
    """今日が決算明け（前営業日の引け後〜今日の寄り前に発表）か。"""
    for a in announcements:
        ts = pd.Timestamp(a)
        if ts.tzinfo is not None:
            ts = ts.tz_convert("Asia/Tokyo").tz_localize(None)
        d, t = ts.date(), ts.time()
        if d == prev_trading_day and (t >= ANNOUNCE_AFTER or t == time(0, 0)):
            return True
        if d == today and t != time(0, 0) and t < time(9, 0):
            return True
    return False


def next_announcement(announcements: list[datetime], today: date) -> str | None:
    """今日以降で一番近い決算発表日（YYYY-MM-DD）。"""
    future = []
    for a in announcements:
        ts = pd.Timestamp(a)
        if ts.tzinfo is not None:
            ts = ts.tz_convert("Asia/Tokyo").tz_localize(None)
        if ts.date() >= today:
            future.append(ts.date())
    return min(future).isoformat() if future else None


def fetch_announcements(ticker: str) -> list[datetime]:
    """yfinance から決算発表の日時（過去・予定）を取る。取れなければ空。"""
    try:
        import yfinance as yf
        ed = yf.Ticker(ticker).get_earnings_dates(limit=16)
    except Exception as e:
        print(f"⚠️ Failed to fetch earnings dates for {ticker}: {e}")
        return []
    if ed is None or len(ed) == 0:
        return []
    return [pd.Timestamp(t).to_pydatetime() for t in ed.index]


def build_morning_signal(
    rng: dict,
    validation: dict | None,
    first5: list[dict] | None,
    announcements: list[datetime],
    today: date,
) -> dict:
    """毎朝のカード用：9時台の値幅の目安（円）・寄り直後10分の確認・決算明けの注意。

    Args:
        rng: build_range_signal の結果（pred_yen = 1日の目安、as_of = 前営業日）
        validation: run_morning_validation の per_code の1銘柄分
        first5: run_morning_validation の first5_table
    """
    out: dict = {"available": False}
    if rng.get("available") and validation and validation.get("ratio"):
        pred = rng["pred_yen"]
        shares = rng["shares"]
        out.update({
            "available": True,
            "n": validation["n"],
            "morning_yen": pred * validation["ratio"],
            "morning_lot_yen": pred * validation["ratio"] * shares,
            "rough_lot_yen": pred * validation["rough_ratio"] * shares if validation.get("rough_ratio") else None,
        })
        big = [r for r in (first5 or []) if r["to"] is None]
        if big:
            out["first5_lot_yen"] = pred * big[0]["from"] * shares
            out["first5_morning_lot_yen"] = pred * big[0]["ratio"] * shares
    prev_day = date.fromisoformat(rng["as_of"]) if rng.get("as_of") else None
    out["earnings_today"] = bool(prev_day and earnings_today(announcements, today, prev_day))
    e = (validation or {}).get("earnings") or {}
    out["earnings_ratio"] = e.get("ratio")
    out["earnings_n"] = e.get("n", 0)
    out["next_earnings"] = next_announcement(announcements, today)
    return out


def run_morning_validation(period: str = "2y") -> dict:
    """承認・試用銘柄の 9時台の値幅の比・決算明けの比 と、全銘柄まとめの最初の10分の表を作る（週次）。"""
    import yfinance as yf
    from focus_stocks import FOCUS_STOCKS
    from stock_screen import SCREEN_JP

    focus = {s.ticker: s for s in FOCUS_STOCKS}
    tickers = sorted({t for _, t, _ in SCREEN_JP} | set(focus))
    print(f"Fetching {len(tickers)} tickers (5m 60d + 1d {period}) for morning range...")
    from yahoo_chart import fetch_intraday
    m5 = {t: fetch_intraday(t, "5m", days=59, chunk_days=59) for t in tickers}   # yfinance だと 9:00 の足が欠ける
    d1 = yf.download(" ".join(tickers), period=period, interval="1d", progress=False,
                     group_by="ticker", auto_adjust=True, threads=True)
    pooled, per_code = [], {}
    try:   # 診断用：5分足の各日の最初の足の時刻
        x = m5[tickers[0]].dropna(subset=["Open"])
        x = x.tz_convert("Asia/Tokyo") if getattr(x.index, "tz", None) is not None else x
        firsts = pd.Series([g.index.min().strftime("%H:%M") for _, g in x.groupby(x.index.date)]).value_counts()
        print(f"  {tickers[0]} の5分足 {x.index.normalize().nunique()}日・各日の最初の足: {firsts.head(5).to_dict()}")
    except Exception as e:
        print(f"  診断に失敗: {e}")
    for t in tickers:
        try:
            rows = morning_rows(m5[t], d1[t])
        except KeyError:
            continue
        pooled.append(rows)
        if t in focus:
            s = focus[t]
            per_code[s.code] = {"name": s.name, **morning_ratio(rows),
                                "earnings": earnings_ratio(d1[t], fetch_announcements(t))}
            v = per_code[s.code]
            print(f"  {s.name:8s} 9時台÷目安 {v['ratio'] or float('nan'):.2f}（n={v['n']}）"
                  f" 決算明け÷目安 {v['earnings']['ratio'] or float('nan'):.2f}（{v['earnings']['n']}回）")
    table = first5_table(pd.concat(pooled)) if pooled else []
    for row in table:
        print(f"  最初の10分が目安の{row['from'] * 100:.0f}%〜: 9時台は目安の{row['ratio']:.2f}倍（n={row['n']}）")
    return {"period": period, "per_code": per_code, "first5_table": table}
