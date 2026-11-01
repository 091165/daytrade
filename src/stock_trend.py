"""
承認・試用銘柄の日足から見る「25日線の乖離」「月足・週足の向き」（純粋関数）

どれも前日までに確定した足だけを使う（当日の未完成の足は除く）。
「下方乖離なら買い中心」などの目安は、週次レポートで当たりが確かめられた場合だけ出す。
"""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

MA_DAYS = 25
SIGMA_LOOKBACK = 250
HIGH_WINDOW = 250           # 52週 ≒ 250営業日
HIGH_RECENT = 5             # 直近5営業日のうちに高値を更新していれば「更新中」


def completed(ohlc: pd.DataFrame, today: date) -> pd.DataFrame:
    """今日より前の足だけ"""
    return ohlc[ohlc.index.normalize() < pd.Timestamp(today)]


def deviation_25ma(closes: pd.Series) -> float | None:
    """最後の終値の25日線乖離率（%）"""
    c = closes.dropna()
    if len(c) < MA_DAYS:
        return None
    return float((c.iloc[-1] / c.iloc[-MA_DAYS:].mean() - 1) * 100)


def deviation_sigma(closes: pd.Series, lookback_days: int = SIGMA_LOOKBACK) -> tuple[float, float, float] | None:
    """その銘柄自身の過去の乖離率の 平均・-2σ・+2σ。データが短ければあるぶんで（最低60日）"""
    c = closes.dropna()
    dev = (c / c.rolling(MA_DAYS).mean() - 1) * 100
    hist = dev.dropna().iloc[-lookback_days:]
    if len(hist) < 60:
        return None
    mu, sd = float(hist.mean()), float(hist.std())
    return mu, mu - 2 * sd, mu + 2 * sd


def at_high(closes: pd.Series, highs: pd.Series | None = None, older_high: float | None = None,
            window: int = HIGH_WINDOW, recent: int = HIGH_RECENT) -> str | None:
    """最高値を更新中か（前日までの確定足を渡す）。

    直近 recent 日の高値が、それより前の 52週（window 日）の高値以上なら「52週高値更新中」。
    さらに older_high（日足より前の期間の最高値。月足などで別に取る）以上なら「上場来高値更新中」。
    older_high を渡さないときは上場来の判定はしない。highs がなければ終値で判定する。
    """
    h = (highs if highs is not None else closes).dropna()
    if len(h) < window:
        return None
    recent_high = float(h.iloc[-recent:].max())
    prior = h.iloc[-window:-recent]
    if prior.empty or recent_high < float(prior.max()):
        return None
    if older_high is not None and recent_high >= float(older_high) and recent_high >= float(h.iloc[:-recent].max()):
        return "上場来高値更新中"
    return "52週高値更新中"


def at_high_flags(highs: pd.Series, window: int = HIGH_WINDOW, recent: int = HIGH_RECENT) -> pd.Series:
    """日ごとに「その日の前日までで 52週高値を更新中だったか」（検証用。at_high と同じ決め方）"""
    h = highs.dropna()
    rec = h.rolling(recent).max()
    prior = h.shift(recent).rolling(window - recent).max()
    flag = (rec >= prior) & prior.notna()
    return flag.shift(1).fillna(False).astype(bool)


def bar_streak(ohlc: pd.DataFrame, freq: str, today: date) -> tuple[str, int] | None:
    """確定した月足（freq="M"）・週足（"W"）の、直近の陽線・陰線が何本続いているか。今月（今週）の足は除く"""
    d = completed(ohlc, today)
    if d.empty:
        return None
    period = d.index.to_period(freq)
    bars = d.groupby(period).agg(o=("Open", "first"), c=("Close", "last"))
    current = pd.Timestamp(today).to_period(freq)
    bars = bars[bars.index < current]
    if bars.empty:
        return None
    color = np.sign(bars["c"] - bars["o"])
    last = color.iloc[-1]
    if last == 0:
        return "flat", 1
    n = 0
    for v in color.iloc[::-1]:
        if v != last:
            break
        n += 1
    return ("up" if last > 0 else "down"), n


def monthly_trend(ohlc: pd.DataFrame, today: date) -> str:
    s = bar_streak(ohlc, "M", today)
    return s[0] if s else "flat"


def this_week_bar(ohlc: pd.DataFrame, today: date) -> dict | None:
    """今週（今日を除く）の週足の向きと、その週の値幅が直近10週の平均より小さいか"""
    d = completed(ohlc, today)
    if d.empty:
        return None
    week = d.index.to_period("W")
    cur = pd.Timestamp(today).to_period("W")
    wk = d[week == cur]
    if wk.empty:
        return None
    up = float(wk["Close"].iloc[-1]) > float(wk["Open"].iloc[0])
    ranges = d.groupby(week).apply(lambda g: g["High"].max() - g["Low"].min())
    past = ranges[ranges.index < cur].iloc[-10:]
    small = bool(len(past) >= 5 and (wk["High"].max() - wk["Low"].min()) < past.mean())
    return {"up": up, "small_range": small}


def bias_hint(dev: float | None, s2l: float | None, s2u: float | None) -> str:
    if dev is None or s2l is None or s2u is None:
        return ""
    if dev <= s2l:
        return "下方乖離：買い中心が有利になりやすい"
    if dev >= s2u:
        return "上方乖離：急騰後の上ヒゲは売り候補"
    return ""


def trend_summary(ohlc: pd.DataFrame | None, today: date) -> dict | None:
    """カードの1行用：乖離・±2σ・月足の連続"""
    if ohlc is None or ohlc.empty:
        return None
    d = completed(ohlc, today)
    dev = deviation_25ma(d["Close"])
    sig = deviation_sigma(d["Close"])
    streak = bar_streak(ohlc, "M", today)
    if dev is None:
        return None
    out = {"dev": dev, "mu": sig[0] if sig else None, "s2l": sig[1] if sig else None, "s2u": sig[2] if sig else None,
           "monthly": streak[0] if streak else None, "monthly_n": streak[1] if streak else 0}
    zone = ""
    if sig:
        if dev <= sig[1]:
            zone = "（-2σ以下）"
        elif dev >= sig[2]:
            zone = "（+2σ以上）"
    mtxt = ""
    if streak and streak[0] != "flat":
        mtxt = f"・月足 {'陽線' if streak[0] == 'up' else '陰線'}{streak[1]}本連続"
    out["text"] = f"25日線乖離 {dev:+.1f}%{zone}{mtxt}"
    out["hint"] = bias_hint(dev, out["s2l"], out["s2u"])
    return out
