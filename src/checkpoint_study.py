"""
節目ごとの値動きの方向を、寄り前・途中までにわかる情報で当てられるか（研究用・週次レポート）

節目: 寄り（9:00）・9:05・9:15・9:30・10:00
区間:
  寄り→9:05 / 寄り→9:15 / 寄り→9:30 / 寄り→10:00   （寄りで入る場合）
  9:05→10:00 / 9:15→10:00 / 9:30→10:00              （様子を見てから入る場合）

手がかり（区間の始まりの時点でわかるもの）:
  us:   米関連株の前日の騰落率（承認・試用銘柄はカードと同じ米国株、それ以外は SOXX・QQQ）
  gap:  その日の窓（前日終値 → 寄り）
  prev: 前日の寄り→引け
  so_far: 寄り→区間の始まりまでの動き（9:05 以降に始まる区間だけ。「動いた方向に乗る」の検証）

値段の取り方:
  寄り値は日足の始値（正式な寄り値）。各節目の値段は、その時刻に始まる5分足の始値。
  Yahoo の分足は 9:00〜9:05 の足が欠ける日が多いが、この取り方なら 9:05 以降の節目は測れる。

判定:
  銘柄ごとの相関は全銘柄×全区間×全手がかりで BH 補正（q<0.05）。
  全銘柄まとめは、同じ日の銘柄どうしが一緒に動く影響を避けるため、
  日ごとに銘柄平均をとってから日数で検定する（n=日数）。
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

from correlation_scan import Q_THRESHOLD, bh_qvalues
from focus_backtest import _corr, _norm

CHECKPOINTS = ("09:05", "09:15", "09:30", "10:00")
SEGMENTS: tuple[tuple[str, str], ...] = (
    ("open", "09:05"), ("open", "09:15"), ("open", "09:30"), ("open", "10:00"),
    ("09:05", "10:00"), ("09:15", "10:00"), ("09:30", "10:00"),
)
PREDICTORS = {
    "us": "米国株の前日の動き",
    "gap": "その日の窓",
    "prev": "前日の寄り→引け",
    "so_far": "寄り→区間の始まりまでの動き",
}
DEFAULT_PROXIES = ("SOXX", "QQQ")
MIN_DAYS = 30


def seg_label(seg: tuple[str, str]) -> str:
    a, b = seg
    return f"{'寄り' if a == 'open' else a}→{b}"


def checkpoint_prices(m5: pd.DataFrame, daily: pd.DataFrame) -> pd.DataFrame:
    """日ごとに 寄り値・前日終値・各節目の値段・前日の寄り→引け を並べる（純粋関数）。

    m5: 5分足（index は JST に直せる日時、Open/Close）
    daily: 日足（Open/Close）
    """
    if m5 is None or daily is None or m5.empty or daily.empty:
        return pd.DataFrame()
    x = m5.dropna(subset=["Open"])
    if getattr(x.index, "tz", None) is not None:
        x = x.tz_convert("Asia/Tokyo")
    o, c = _norm(daily["Open"]), _norm(daily["Close"])
    d = pd.DataFrame({"open": o, "close": c}).dropna()
    d["prev_close"] = d["close"].shift(1)
    d["prev_intraday"] = (d["close"] / d["open"] - 1).shift(1) * 100
    rows = []
    for day, g in x.groupby(x.index.date):
        ts = pd.Timestamp(day)
        if ts not in d.index or pd.isna(d.at[ts, "prev_close"]):
            continue
        starts = pd.Series(g["Open"].values, index=g.index.strftime("%H:%M"))
        starts = starts[~starts.index.duplicated(keep="first")]
        row = {"date": ts, "open": d.at[ts, "open"], "prev_close": d.at[ts, "prev_close"],
               "prev_intraday": d.at[ts, "prev_intraday"]}
        for cp in CHECKPOINTS:
            row[cp] = float(starts[cp]) if cp in starts.index else np.nan
        rows.append(row)
    return pd.DataFrame(rows).set_index("date") if rows else pd.DataFrame()


def segment_frame(prices: pd.DataFrame, us: pd.Series | None) -> dict[tuple[str, str], pd.DataFrame]:
    """区間ごとに、目的（区間の動き%）と手がかりをそろえる（純粋関数）。"""
    out = {}
    if prices.empty:
        return out
    gap = (prices["open"] / prices["prev_close"] - 1) * 100
    for a, b in SEGMENTS:
        start = prices["open"] if a == "open" else prices[a]
        df = pd.DataFrame({
            "target": (prices[b] / start - 1) * 100,
            "gap": gap,
            "prev": prices["prev_intraday"],
            "us": us.reindex(prices.index) if us is not None else np.nan,
        })
        if a != "open":
            df["so_far"] = (prices[a] / prices["open"] - 1) * 100
        out[(a, b)] = df
    return out


def us_prev_move(us_close: dict[str, pd.Series], proxies, jp_index: pd.DatetimeIndex) -> pd.Series:
    """日本の各営業日に、前日の米関連株の平均騰落率（%）をそろえる。"""
    rets = [us_close[t].pct_change() * 100 for t in proxies if t in us_close]
    if not rets:
        return pd.Series(index=jp_index, dtype=float)
    avg = pd.concat(rets, axis=1).mean(axis=1).dropna()
    pos = np.searchsorted(avg.index.values, (jp_index - pd.Timedelta(days=1)).values, side="right") - 1
    vals = np.where(pos >= 0, avg.values[np.clip(pos, 0, None)], np.nan)
    return pd.Series(vals, index=jp_index)


def stock_stats(frames: dict[tuple[str, str], pd.DataFrame], price: float | None) -> dict:
    """1銘柄の 区間×手がかり ごとの相関・当たり率・100株あたりの平均損益。"""
    out = {}
    for seg, df in frames.items():
        for key in PREDICTORS:
            if key not in df:
                continue
            d = df[[key, "target"]].dropna()
            d = d[d[key] != 0]
            if len(d) < MIN_DAYS:
                continue
            r, p = _corr(d[key], d["target"])
            if r is None:
                continue
            got = np.sign(r) * np.sign(d[key]) * d["target"]        # 相関の向きに入った場合の損益（%）
            out[f"{seg_label(seg)}|{key}"] = {
                "seg": seg_label(seg), "key": key, "n": len(d), "r": r, "p": p,
                "hit": float((got > 0).mean()),
                "edge_yen": float(got.mean() / 100 * price * 100) if price else None,
            }
    return out


def pooled_stats(all_frames: dict[str, dict[tuple[str, str], pd.DataFrame]]) -> list[dict]:
    """全銘柄まとめ：銘柄ごとに標準化した 手がかり×目的 を日ごとに平均し、日数で検定する。"""
    out = []
    for seg in SEGMENTS:
        for key in PREDICTORS:
            parts = []
            for frames in all_frames.values():
                df = frames.get(seg)
                if df is None or key not in df:
                    continue
                d = df[[key, "target"]].dropna()
                if len(d) < MIN_DAYS or d[key].std() == 0 or d["target"].std() == 0:
                    continue
                z = ((d[key] - d[key].mean()) / d[key].std()) * ((d["target"] - d["target"].mean()) / d["target"].std())
                hit = (np.sign(d[key]) * np.sign(d["target"]) > 0).astype(float)
                parts.append(pd.DataFrame({"z": z, "hit": hit}))
            if not parts:
                continue
            per_day = pd.concat(parts).groupby(level=0).mean()
            n = len(per_day)
            if n < MIN_DAYS:
                continue
            m, s = per_day["z"].mean(), per_day["z"].std(ddof=1)
            t = m / (s / math.sqrt(n)) if s > 0 else 0.0
            p = math.erfc(abs(t) / math.sqrt(2))                 # 日数が多いので正規近似
            out.append({"seg": seg_label(seg), "key": key, "days": n, "stocks": len(parts),
                        "r": float(m), "p": p, "same_dir": float(per_day["hit"].mean())})
    qs = bh_qvalues([o["p"] for o in out])
    for o, q in zip(out, qs):
        o["q"] = q
        o["significant"] = q is not None and q < Q_THRESHOLD
    return out


def run_checkpoint_study() -> dict:
    import yfinance as yf
    from focus_stocks import FOCUS_STOCKS
    from stock_screen import SCREEN_JP

    proxies = {s.code: s.us_proxies for s in FOCUS_STOCKS}
    jp = {t: (c, n) for c, t, n in SCREEN_JP}
    us_tickers = sorted({t for p in proxies.values() for t in p} | set(DEFAULT_PROXIES))
    print(f"Fetching {len(jp)} JP (5m 60d + 1d 6mo) + {len(us_tickers)} US for checkpoint study...")
    m5 = yf.download(" ".join(jp), period="60d", interval="5m", progress=False,
                     group_by="ticker", auto_adjust=True, threads=True)
    d1 = yf.download(" ".join(list(jp) + us_tickers), period="6mo", interval="1d", progress=False,
                     group_by="ticker", auto_adjust=True, threads=True)
    us_close = {}
    for t in us_tickers:
        try:
            us_close[t] = _norm(d1[t]["Close"])
        except KeyError:
            pass

    all_frames, per_stock = {}, []
    for t, (code, name) in jp.items():
        try:
            prices = checkpoint_prices(m5[t], d1[t])
        except KeyError:
            continue
        if prices.empty:
            continue
        us = us_prev_move(us_close, proxies.get(code, DEFAULT_PROXIES), prices.index)
        frames = segment_frame(prices, us)
        all_frames[code] = frames
        per_stock.append({"code": code, "name": name, "days": len(prices),
                          "stats": stock_stats(frames, float(prices["open"].iloc[-1]))})

    keys = [(i, k) for i, s in enumerate(per_stock) for k in s["stats"]]
    qs = bh_qvalues([per_stock[i]["stats"][k]["p"] for i, k in keys])
    for (i, k), q in zip(keys, qs):
        v = per_stock[i]["stats"][k]
        v["q"] = q
        v["significant"] = q is not None and q < Q_THRESHOLD
    pooled = pooled_stats(all_frames)

    days = max((s["days"] for s in per_stock), default=0)
    print(f"  {len(per_stock)}銘柄・最大{days}日 ／ 銘柄ごと {len(keys)}通り・全銘柄まとめ {len(pooled)}通り")
    for o in pooled:
        mark = "✅" if o["significant"] else "  "
        print(f"  {mark} {o['seg']:12s} ← {PREDICTORS[o['key']]:16s} r={o['r']:+.3f} 同じ向き{o['same_dir'] * 100:4.1f}% q={o['q']:.3f}")
    for s in per_stock:
        for v in s["stats"].values():
            if v["significant"]:
                print(f"    ✅ {s['name']} {v['seg']} ← {PREDICTORS[v['key']]} r={v['r']:+.2f} 当たり{v['hit'] * 100:.0f}%")
    return {"source": "Yahoo 5分足・直近60日（寄り値は日足の始値）", "segments": [seg_label(s) for s in SEGMENTS],
            "predictors": PREDICTORS, "pooled": pooled,
            "per_stock": [{"code": s["code"], "name": s["name"], "days": s["days"],
                           "hits": [v for v in s["stats"].values() if v["significant"]]} for s in per_stock],
            "n_tests": len(keys)}
