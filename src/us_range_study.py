"""
米国関連株が大きく動いた翌日は、承認・試用銘柄の値幅が普段より広がるか（研究用・週次レポート＋毎朝の表示）

各銘柄の「米関連株」（focus_stocks の us_proxies）の前日の平均騰落率の大きさで日を分け、
その日の値幅（高値−安値）が「普段の目安」（直近10日の平均値幅）の何倍だったかを比べる。
決算明けの日は値幅が別の理由で広がるので除く。

  区分: |米関連株の平均| が 1%未満 / 1〜2% / 2〜3% / 3%以上
  比べ方: 各区分の「実際 ÷ 目安」の平均を、1%未満の日と比べる（Welch・正規近似）。3銘柄×3区分を BH 補正

効いていた区分（補正後 q<0.05 で、1%未満の日より大きい）だけ、毎朝の値幅・作戦の幅にその倍率を掛ける。
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

from expected_range import RANGE_WINDOW

BUCKETS = ((0.0, 1.0, "1%未満"), (1.0, 2.0, "1〜2%"), (2.0, 3.0, "2〜3%"), (3.0, float("inf"), "3%以上"))
Q_TH = 0.05
MIN_N = 15


def bucket_of(abs_move: float) -> str:
    for lo, hi, lab in BUCKETS:
        if lo <= abs_move < hi:
            return lab
    return BUCKETS[-1][2]


def range_ratios(ohlc: pd.DataFrame, us_avg: pd.Series, skip_days: list[pd.Timestamp] | None = None) -> pd.DataFrame:
    """日ごとの 実際の値幅 ÷ 目安（前日までの10日平均） と、前日の米関連株の平均騰落率（純粋関数）

    us_avg: 米国の日付 → 米関連株の平均騰落率(%)。日本の日付 t には、t より前で最新の値を使う。
    """
    d = ohlc.copy()
    d.index = pd.to_datetime(d.index)
    if d.index.tz is not None:
        d.index = d.index.tz_localize(None)
    d.index = d.index.normalize()
    rng = (d["High"] - d["Low"]) / d["Close"].shift(1) * 100
    pred = rng.rolling(RANGE_WINDOW).mean().shift(1)
    us = us_avg.copy()
    us.index = pd.to_datetime(us.index)
    if us.index.tz is not None:
        us.index = us.index.tz_localize(None)
    us = us.sort_index()
    before = us.reindex(d.index - pd.Timedelta(days=1), method="ffill")
    out = pd.DataFrame({"ratio": (rng / pred).values, "us": before.values}, index=d.index).dropna()
    if skip_days:
        out = out[~out.index.isin(pd.DatetimeIndex(skip_days).normalize())]
    out["bucket"] = out["us"].abs().map(bucket_of)
    return out


def _diff_p(a: np.ndarray, b: np.ndarray) -> float:
    if len(a) < 3 or len(b) < 3:
        return 1.0
    se = math.sqrt(a.var(ddof=1) / len(a) + b.var(ddof=1) / len(b))
    return 1.0 if se == 0 else math.erfc(abs(a.mean() - b.mean()) / se / math.sqrt(2))


def summarize(rows: pd.DataFrame) -> dict:
    """区分ごとの 実際÷目安 の平均・中央値と、1%未満の日との差の p 値"""
    base = rows[rows["bucket"] == BUCKETS[0][2]]["ratio"].values
    out = []
    for _, _, lab in BUCKETS:
        x = rows[rows["bucket"] == lab]["ratio"].values
        out.append({"bucket": lab, "n": int(len(x)),
                    "mean": float(x.mean()) if len(x) else None,
                    "median": float(np.median(x)) if len(x) else None,
                    "p_vs_calm": _diff_p(x, base) if lab != BUCKETS[0][2] else None})
    return {"buckets": out, "n": int(len(rows))}


def bh(ps: list[float]) -> list[float]:
    n = len(ps)
    if not n:
        return []
    order = np.argsort(ps)
    q = np.empty(n)
    prev = 1.0
    for rank, i in reversed(list(enumerate(order, start=1))):
        prev = min(prev, ps[i] * n / rank)
        q[i] = prev
    return [float(v) for v in q]


def mark_significant(per_code: dict[str, dict]) -> None:
    """3銘柄×3区分の p 値を BH 補正し、効いている区分に multiplier を付ける（その場で書き換え）"""
    cells = [(c, b) for c, r in per_code.items() for b in r["buckets"] if b["p_vs_calm"] is not None]
    qs = bh([b["p_vs_calm"] for _, b in cells])
    for (c, b), q in zip(cells, qs):
        base = per_code[c]["buckets"][0]["mean"] or 1.0
        b["q"] = q
        b["significant"] = bool(q < Q_TH and b["n"] >= MIN_N and b["mean"] and b["mean"] > base)
        # 毎朝に掛ける倍率は「1%未満の日に対して何倍か」ではなく「目安（10日平均）の何倍か」＝その区分の平均
        b["multiplier"] = b["mean"] if b["significant"] else None


def today_multiplier(validation: dict | None, code: str, us_avg_today: float | None) -> dict | None:
    """毎朝用：今日の米関連株の動きの区分と、効いていればその倍率"""
    if us_avg_today is None:
        return None
    lab = bucket_of(abs(us_avg_today))
    r = ((validation or {}).get("per_code") or {}).get(code)
    if not r:
        return {"bucket": lab, "multiplier": None, "n": 0, "evidence": "未検証"}
    b = next((x for x in r["buckets"] if x["bucket"] == lab), None)
    if not b:
        return None
    base = r["buckets"][0]
    ev = (f"検証（{r['n']}日）：米関連株が{lab}動いた翌日の値幅は目安の {b['mean']:.2f}倍（{b['n']}回）・"
          f"1%未満の日は {base['mean']:.2f}倍")
    return {"bucket": lab, "multiplier": b.get("multiplier"), "n": b["n"], "evidence": ev,
            "significant": b.get("significant", False)}


def run_us_range_study(period: str = "2y") -> dict:
    import yfinance as yf
    from focus_stocks import FOCUS_STOCKS
    from morning_range import fetch_announcements, reaction_days

    us_t = sorted({t for s in FOCUS_STOCKS for t in s.us_proxies})
    jp_t = [s.ticker for s in FOCUS_STOCKS]
    data = yf.download(" ".join(us_t + jp_t), period=period, interval="1d", progress=False,
                       group_by="ticker", auto_adjust=True, threads=True)
    per_code = {}
    for s in FOCUS_STOCKS:
        try:
            ohlc = data[s.ticker][["Open", "High", "Low", "Close"]].dropna()
            moves = pd.concat({t: data[t]["Close"].dropna().pct_change() * 100 for t in s.us_proxies}, axis=1)
        except KeyError:
            continue
        us_avg = moves.mean(axis=1, skipna=True).dropna()
        idx = pd.to_datetime(ohlc.index)
        idx = idx.tz_localize(None) if idx.tz is not None else idx
        skip = reaction_days(fetch_announcements(s.ticker), idx.normalize())
        rows = range_ratios(ohlc, us_avg, skip)
        per_code[s.code] = {"name": s.name, **summarize(rows), "earnings_skipped": len(skip)}
    mark_significant(per_code)
    res = {"period": period, "per_code": per_code, "window": RANGE_WINDOW}
    for c, r in per_code.items():
        print(f"  {r['name']}（{r['n']}日・決算明け{r['earnings_skipped']}日を除く）: " + "  ".join(
            f"{b['bucket']} {b['mean']:.2f}倍({b['n']})" + (f" q={b['q']:.3f}{'✅' if b['significant'] else ''}" if b.get("q") is not None else "")
            for b in r["buckets"] if b["mean"] is not None))
    return res
