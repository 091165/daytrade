"""
寄り直後の動きは10時まで続くか（研究用・週次レポート）

「寄りで飛びつかず、動いた方向に乗る」が数字で裏付けられるかを確かめる。
  最初の動き: 寄り（9:00の始値） → 9:05 または 9:15 の終値
  その後:     9:05 / 9:15 の終値 → 10:00 の直前の終値
最初の動きと同じ向きにその後も動くか（続く）、逆に戻すか（戻す）を、
銘柄ごとと全銘柄まとめで測る。

データ:
  data/minutes/ に1分足が MIN_SAVED_DAYS 日以上たまったらそれを使い、
  それまでは Yahoo の5分足（直近60日）を使う（予備検証）。
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

from correlation_scan import Q_THRESHOLD, bh_qvalues
from focus_backtest import _corr

WINDOWS = ("09:05", "09:15")     # 最初の動きを測る区切り
END = "10:00"
MIN_SAVED_DAYS = 40
MIN_DAYS = 20


def drive_rows(bars: pd.DataFrame, bar_minutes: int) -> pd.DataFrame:
    """1銘柄の分足（index=JSTの日時、Open/Close）から、日ごとの 最初の動き・その後 を%で作る（純粋関数）。

    bar_minutes: 足の長さ（1 または 5）。足の終値の時刻 = 始まり + bar_minutes
    """
    rows = []
    for day, g in bars.groupby(bars.index.date):
        g = g.sort_index()
        start = g[g.index.strftime("%H:%M") == "09:00"]
        if start.empty:
            continue
        o = float(start["Open"].iloc[0])
        ends = g.index + pd.Timedelta(minutes=bar_minutes)
        closes = pd.Series(g["Close"].values, index=ends.strftime("%H:%M"))
        closes = closes[~closes.index.duplicated(keep="last")]
        if END not in closes.index:
            continue
        row = {"date": pd.Timestamp(day)}
        for w in WINDOWS:
            if w not in closes.index:
                continue
            c_w = float(closes[w])
            row[f"first_{w}"] = (c_w / o - 1) * 100
            row[f"after_{w}"] = (float(closes[END]) / c_w - 1) * 100
        rows.append(row)
    return pd.DataFrame(rows).set_index("date") if rows else pd.DataFrame()


def follow_stats(df: pd.DataFrame, window: str, price: float | None = None) -> dict:
    """最初の動きと同じ向きに乗った場合の 続いた率・平均（%と100株あたり円）・相関。"""
    f, a = df.get(f"first_{window}"), df.get(f"after_{window}")
    if f is None or a is None:
        return {"n": 0}
    d = pd.DataFrame({"f": f, "a": a}).dropna()
    d = d[d["f"] != 0]
    n = len(d)
    if n < MIN_DAYS:
        return {"n": n}
    got = np.sign(d["f"]) * d["a"]                     # 最初の動きの向きに乗ったときの損益（%）
    hit = float((got > 0).mean())
    r, p = _corr(d["f"], d["a"])
    return {
        "n": n, "follow_rate": hit,
        "mean_pct": float(got.mean()),
        "mean_yen": float(got.mean() / 100 * price * 100) if price else None,
        "r": r, "p": p,
    }


def binom_p(k: int, n: int) -> float:
    """続いた率が50%と違うかの両側p値（正規近似）。"""
    if n == 0:
        return 1.0
    z = abs(k - n / 2) / math.sqrt(n / 4)
    return math.erfc(z / math.sqrt(2))


def summarize(per_stock: dict[str, dict]) -> dict:
    """銘柄ごとのp値にBH補正をかけ、全銘柄まとめの続いた率を出す。"""
    keys = [(c, w) for c, v in per_stock.items() for w in WINDOWS if v[w].get("p") is not None]
    qs = bh_qvalues([per_stock[c][w]["p"] for c, w in keys])
    for (c, w), q in zip(keys, qs):
        per_stock[c][w]["q"] = q
        per_stock[c][w]["significant"] = q is not None and q < Q_THRESHOLD
    pooled = {}
    for w in WINDOWS:
        vs = [v[w] for v in per_stock.values() if v[w].get("n", 0) >= MIN_DAYS]
        n = sum(v["n"] for v in vs)
        k = round(sum(v["follow_rate"] * v["n"] for v in vs))
        pooled[w] = {"n": n, "follow_rate": k / n if n else None, "p": binom_p(k, n),
                     "mean_pct": float(np.average([v["mean_pct"] for v in vs], weights=[v["n"] for v in vs])) if vs else None}
    return pooled


def run_opening_drive() -> dict:
    import yfinance as yf
    from collect_minutes import load_minutes
    from stock_screen import SCREEN_JP

    saved = load_minutes()
    n_saved = saved["date"].nunique() if not saved.empty else 0
    names = {c: n for c, _, n in SCREEN_JP}
    bars_by_code: dict[str, pd.DataFrame] = {}
    if n_saved >= MIN_SAVED_DAYS:
        source, bar_minutes = f"保存した1分足（{n_saved}日）", 1
        for code, g in saved.groupby("code"):
            idx = pd.to_datetime(g["date"].dt.strftime("%Y-%m-%d") + " " + g["time"]).dt.tz_localize("Asia/Tokyo")
            bars_by_code[code] = pd.DataFrame({"Open": g["open"].values, "Close": g["close"].values}, index=idx.values)
    else:
        source, bar_minutes = f"Yahoo 5分足・直近60日（1分足は{n_saved}日分たまったところ）", 5
        tickers = {t: c for c, t, _ in SCREEN_JP}
        m5 = yf.download(" ".join(tickers), period="60d", interval="5m", progress=False,
                         group_by="ticker", auto_adjust=True, threads=True)
        for t, code in tickers.items():
            try:
                x = m5[t].dropna(subset=["Open", "Close"])
            except KeyError:
                continue
            bars_by_code[code] = x.tz_convert("Asia/Tokyo") if getattr(x.index, "tz", None) is not None else x

    per_stock = {}
    for code, bars in bars_by_code.items():
        bars.index = pd.DatetimeIndex(bars.index)
        if getattr(bars.index, "tz", None) is not None:
            bars.index = bars.index.tz_convert("Asia/Tokyo")
        rows = drive_rows(bars, bar_minutes)
        if rows.empty:
            continue
        price = float(bars["Close"].iloc[-1])
        per_stock[code] = {"name": names.get(code, code), **{w: follow_stats(rows, w, price) for w in WINDOWS}}
    pooled = summarize(per_stock)
    print(f"  データ: {source}")
    for w in WINDOWS:
        p = pooled[w]
        if p["n"]:
            print(f"  寄り→{w} の向きに乗る（〜{END}）: 続いた {p['follow_rate'] * 100:.1f}%（{p['n']}件, p={p['p']:.3f}）平均 {p['mean_pct']:+.3f}%")
    for code, v in per_stock.items():
        sig = [w for w in WINDOWS if v[w].get("significant")]
        if sig:
            print(f"    ✅ {v['name']}: " + "・".join(f"{w} 続いた{v[w]['follow_rate'] * 100:.0f}%" for w in sig))
    return {"source": source, "windows": list(WINDOWS), "end": END, "pooled": pooled,
            "per_stock": [{"code": c, **v} for c, v in per_stock.items()]}
