"""
上値・下値の目安（レジサポ）は本当に止まりやすいか（研究用・週次レポート）

前日までの日足で作った水準（前日の高値・安値・終値、5日・20日の高値・安値、25日線、キリのいい値段）に、
9時台（9:00〜10:00）に初めて触れたあと、止まった（反発）か抜けたかを数える。
比較用に、前日終値から 1日の目安 × 0.2・0.4・0.6・0.8 離れた「ただの値段」でも同じことをして、
水準ごとの止まった割合を比べる（(銘柄, 日) 単位のブートストラップで差の95%区間）。

止まった・抜けた: 水準から 1日の目安 × STEP_FRAC だけ戻したか・進んだか（fib_study と同じ判定）。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from fib_study import _resolve
from trade_plan import price_levels

STEP_FRAC = 0.1
CONTROL_U = (0.2, 0.4, 0.6, 0.8)
RANGE_DAYS = 10
END = "10:00"
MIN_EVENTS = 30
N_BOOT = 2000
KINDS = ("前日高値", "前日安値", "5日高値", "5日安値", "20日高値", "20日安値", "25日線", "キリ（大）", "キリ")


def day_events(daily: pd.DataFrame, bars: pd.DataFrame, day: pd.Timestamp) -> list[dict]:
    """1銘柄1日分（純粋関数）。daily は日足（day を含んでよい。使うのは前日まで）、bars はその日の分足"""
    hist = daily[daily.index < day]
    if len(hist) < RANGE_DAYS + 1:
        return []
    width = float((hist["High"] - hist["Low"]).iloc[-RANGE_DAYS:].mean())
    if width <= 0:
        return []
    b = bars[bars.index.strftime("%H:%M") < END]
    if b.empty:
        return []
    o = float(b["Open"].iloc[0])
    pc = float(hist["Close"].iloc[-1])
    step = STEP_FRAC * width
    levels = [(x["kind"], x["price"]) for x in price_levels(hist, width, merge=False)]
    levels += [("比較用", pc + sgn * u * width) for u in CONTROL_U for sgn in (1, -1)]
    out = []
    for kind, px in levels:
        if abs(px - o) < step:            # 寄りの時点ですでに水準の上にいる
            continue
        down = px < o
        res = _resolve(b, px, step, down=down)
        if res:
            out.append({"kind": kind, "result": res})
    return out


def summarize(events: pd.DataFrame, seed: int = 0) -> dict:
    """水準の種類ごとの止まった割合と、比較用との差（(銘柄, 日) 単位のブートストラップ）"""
    if events.empty:
        return {"kinds": [], "n": 0}
    ev = events.assign(b=(events["result"] == "bounce").astype(float))
    ctl = ev[ev["kind"] == "比較用"]
    out = {"n": len(ev), "n_control": len(ctl), "control_bounce": float(ctl["b"].mean()) if len(ctl) else None, "kinds": []}
    groups = [g for _, g in ev.groupby(["code", "date"])]
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(groups), size=(N_BOOT, len(groups))) if groups else None
    for kind in KINDS:
        k = ev[ev["kind"] == kind]
        row = {"kind": kind, "n": len(k), "bounce": float(k["b"].mean()) if len(k) else None,
               "diff": None, "ci": None, "significant": False}
        if len(k) >= MIN_EVENTS and len(ctl) >= MIN_EVENTS:
            row["diff"] = row["bounce"] - out["control_bounce"]
            sums = np.array([[g.loc[g["kind"] == kind, "b"].sum(), (g["kind"] == kind).sum(),
                              g.loc[g["kind"] == "比較用", "b"].sum(), (g["kind"] == "比較用").sum()] for g in groups])
            s = sums[idx].sum(axis=1)
            with np.errstate(invalid="ignore", divide="ignore"):
                diffs = s[:, 0] / s[:, 1] - s[:, 2] / s[:, 3]
            diffs = diffs[np.isfinite(diffs)]
            lo, hi = np.percentile(diffs, [2.5, 97.5])
            row["ci"] = [float(lo), float(hi)]
            row["significant"] = bool(lo > 0 or hi < 0)
        out["kinds"].append(row)
    return out


def run_level_study() -> dict:
    import yfinance as yf
    from focus_stocks import FOCUS_STOCKS
    from stock_screen import SCREEN_JP

    names = {t: (c, n) for c, t, n in SCREEN_JP}
    for s in FOCUS_STOCKS:
        names.setdefault(s.ticker, (s.code, s.name))
    tickers = " ".join(names)
    d1 = yf.download(tickers, period="1y", interval="1d", progress=False, group_by="ticker", auto_adjust=True, threads=True)
    m5 = yf.download(tickers, period="60d", interval="5m", progress=False, group_by="ticker", auto_adjust=True, threads=True)
    rows = []
    per_code: dict[str, list] = {}
    for t, (code, _) in names.items():
        try:
            daily = d1[t][["Open", "High", "Low", "Close"]].dropna()
            bars = m5[t][["Open", "High", "Low", "Close"]].dropna()
        except KeyError:
            continue
        daily.index = pd.to_datetime(daily.index).tz_localize(None) if daily.index.tz is not None else pd.to_datetime(daily.index)
        if getattr(bars.index, "tz", None) is not None:
            bars = bars.tz_convert("Asia/Tokyo").tz_localize(None)
        for day, g in bars.groupby(bars.index.normalize()):
            for e in day_events(daily, g, day):
                rows.append({**e, "code": code, "date": day})
                per_code.setdefault(code, []).append({**e, "code": code, "date": day})
    ev = pd.DataFrame(rows)
    res = summarize(ev)
    res["source"] = f"売買代金上位{len(names)}銘柄・5分足60日・9時台"
    res["per_code"] = {}
    for s in FOCUS_STOCKS:
        e = pd.DataFrame(per_code.get(s.code, []))
        res["per_code"][s.code] = summarize(e) if not e.empty else {"kinds": [], "n": 0}
    print(f"  水準の止まりやすさ（{res['source']}・比較用 {res.get('control_bounce') or 0:.0%} / {res.get('n_control', 0)}回）")
    for k in res["kinds"]:
        if k["bounce"] is not None:
            ci = f"差 {k['diff'] * 100:+.1f}pt [{k['ci'][0] * 100:+.1f}, {k['ci'][1] * 100:+.1f}]" if k["ci"] else "件数不足"
            print(f"    {k['kind']:8s} {k['bounce'] * 100:5.1f}%（{k['n']}回） {ci} {'✅' if k['significant'] else ''}")
    return res
