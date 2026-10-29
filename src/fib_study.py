"""
フィボナッチの水準（38.2%・50%・61.8%）で反発しやすいか（研究用・週次レポート）

どんな水準を引いても「そこで反発した日」は一定の割合である。そこで、フィボナッチの水準の反発率を、
比較用の水準（30%・45%・55%・70%）の反発率と比べ、差が偶然かどうかを確かめる。

線の引き方（2パターン）:
  gap:   窓を開けた日（前日比 ±MIN_GAP_PCT 以上）。前日終値と寄り値の間を100%として、
         寄り値から窓を埋める方向に k% 戻った値段（窓を上に開けたなら 寄り値 − k×窓の幅）
  swing: 寄り後30分（9:00〜9:30）の高値と安値の間を100%として、9:30の値段が近い側から
         k% 戻った値段（上で終わったなら 高値 − k×値幅）。9:30 以降の動きで判定

反発したか（水準に初めて触れたあと、前場の引け 11:30 まで）:
  反発: 線から幅の BOUNCE_FRAC だけ元の方向に戻した
  抜けた: 線から幅の BOUNCE_FRAC だけ先に進んだ（触れた足でもう進んでいたら抜けた）
  どちらも同じ足で起きた・どちらも起きなかった は数えない

差の確からしさ:
  同じ日の同じ銘柄の水準どうしは一緒に動くので、(銘柄, 日) の単位で選び直すブートストラップで
  「フィボナッチの反発率 − 比較用の反発率」の95%区間を出す。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

FIB_LEVELS = (0.382, 0.5, 0.618)
CONTROL_LEVELS = (0.3, 0.45, 0.55, 0.7)
MIN_GAP_PCT = 1.0
BOUNCE_FRAC = 0.15          # 反発／抜けた の判定幅（窓・値幅に対する割合）
SWING_END = "09:30"
SESSION_END = "11:30"
MIN_EVENTS = 30
N_BOOT = 2000


def _resolve(bars: pd.DataFrame, level: float, step: float, down: bool) -> str | None:
    """水準に初めて触れたあとの結果。down=True なら上から下りてきて触れる（下で反発を待つ）。

    bars: 時刻順の足（High/Low）。step: 判定幅（円）。
    戻り値: "bounce" / "break" / None（触れなかった・決まらなかった）
    """
    hi, lo = bars["High"].values, bars["Low"].values
    touched = np.where(lo <= level)[0] if down else np.where(hi >= level)[0]
    if len(touched) == 0:
        return None
    i = touched[0]
    if (down and lo[i] <= level - step) or (not down and hi[i] >= level + step):
        return "break"
    for j in range(i + 1, len(hi)):
        up_hit = hi[j] >= level + step
        dn_hit = lo[j] <= level - step
        if down:
            bounce, brk = up_hit, dn_hit
        else:
            bounce, brk = dn_hit, up_hit
        if bounce and brk:
            return None
        if bounce:
            return "bounce"
        if brk:
            return "break"
    return None


def gap_events(day_bars: pd.DataFrame, open_: float, prev_close: float) -> list[dict]:
    """窓を開けた日の、窓の幅に引いた各水準の結果（純粋関数）。day_bars は 9:00〜11:30 の足。"""
    gap = open_ - prev_close
    if prev_close <= 0 or abs(gap) / prev_close * 100 < MIN_GAP_PCT:
        return []
    up = gap > 0
    width = abs(gap)
    bars = day_bars[day_bars.index.strftime("%H:%M") < SESSION_END]
    out = []
    for k in FIB_LEVELS + CONTROL_LEVELS:
        level = open_ - k * gap
        res = _resolve(bars, level, BOUNCE_FRAC * width, down=up)
        if res:
            out.append({"pattern": "gap", "level": k, "fib": k in FIB_LEVELS, "result": res})
    return out


def swing_events(day_bars: pd.DataFrame) -> list[dict]:
    """寄り後30分の値幅に引いた各水準の、9:30 以降の結果（純粋関数）。"""
    hm = day_bars.index.strftime("%H:%M")
    first = day_bars[hm < SWING_END]
    after = day_bars[(hm >= SWING_END) & (hm < SESSION_END)]
    if len(first) < 3 or after.empty:
        return []
    hi, lo = float(first["High"].max()), float(first["Low"].min())
    width = hi - lo
    if width <= 0:
        return []
    last = float(first["Close"].iloc[-1])
    up = (hi - last) < (last - lo)          # 9:30 の値段が高値に近い = 上に動いた
    out = []
    for k in FIB_LEVELS + CONTROL_LEVELS:
        level = hi - k * width if up else lo + k * width
        res = _resolve(after, level, BOUNCE_FRAC * width, down=up)
        if res:
            out.append({"pattern": "swing", "level": k, "fib": k in FIB_LEVELS, "result": res})
    return out


def summarize_pattern(events: pd.DataFrame, seed: int = 0) -> dict:
    """1パターン分：水準ごとの反発率と、フィボナッチ − 比較用 の差（(銘柄, 日) 単位のブートストラップ）。"""
    if events.empty:
        return {"levels": [], "n": 0}
    ev = events.assign(b=(events["result"] == "bounce").astype(float))
    levels = [{"level": k, "fib": k in FIB_LEVELS, "n": int((g := ev[ev["level"] == k]).shape[0]),
               "bounce": float(g["b"].mean()) if len(g) else None}
              for k in sorted(FIB_LEVELS + CONTROL_LEVELS)]
    fib, ctl = ev[ev["fib"]], ev[~ev["fib"]]
    out = {"levels": levels, "n": len(ev), "n_fib": len(fib), "n_control": len(ctl),
           "fib_bounce": float(fib["b"].mean()) if len(fib) else None,
           "control_bounce": float(ctl["b"].mean()) if len(ctl) else None,
           "diff": None, "ci": None, "p": None, "significant": False}
    if len(fib) < MIN_EVENTS or len(ctl) < MIN_EVENTS:
        return out
    out["diff"] = out["fib_bounce"] - out["control_bounce"]
    groups = [g for _, g in ev.groupby(["code", "date"])]
    sums = np.array([[g.loc[g["fib"], "b"].sum(), g["fib"].sum(), g.loc[~g["fib"], "b"].sum(), (~g["fib"]).sum()]
                     for g in groups])
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(groups), size=(N_BOOT, len(groups)))
    s = sums[idx].sum(axis=1)
    with np.errstate(invalid="ignore", divide="ignore"):
        diffs = s[:, 0] / s[:, 1] - s[:, 2] / s[:, 3]
    diffs = diffs[np.isfinite(diffs)]
    lo, hi = np.percentile(diffs, [2.5, 97.5])
    out["ci"] = [float(lo), float(hi)]
    out["p"] = float(min(1.0, 2 * min((diffs <= 0).mean(), (diffs >= 0).mean())))
    out["significant"] = bool(lo > 0 or hi < 0)
    return out


def run_fib_study() -> dict:
    import yfinance as yf
    from collect_minutes import load_minutes
    from focus_backtest import _norm
    from stock_screen import SCREEN_JP

    jp = {t: (c, n) for c, t, n in SCREEN_JP}
    d1 = yf.download(" ".join(jp), period="6mo", interval="1d", progress=False,
                     group_by="ticker", auto_adjust=True, threads=True)
    saved = load_minutes()
    n_saved = saved["date"].nunique() if not saved.empty else 0
    bars_by_code: dict[str, pd.DataFrame] = {}
    if n_saved >= 40:
        source = f"保存した1分足（{n_saved}日）"
        for code, g in saved.groupby("code"):
            idx = pd.to_datetime(g["date"].dt.strftime("%Y-%m-%d") + " " + g["time"])
            bars_by_code[code] = pd.DataFrame({"High": g["high"].values, "Low": g["low"].values,
                                               "Close": g["close"].values}, index=idx.values)
    else:
        source = f"Yahoo 5分足・直近60日（1分足は{n_saved}日分たまったところ）"
        m5 = yf.download(" ".join(jp), period="60d", interval="5m", progress=False,
                         group_by="ticker", auto_adjust=True, threads=True)
        for t, (code, _) in jp.items():
            try:
                x = m5[t].dropna(subset=["High", "Low", "Close"])
            except KeyError:
                continue
            if getattr(x.index, "tz", None) is not None:
                x = x.tz_convert("Asia/Tokyo").tz_localize(None)
            bars_by_code[code] = x[["High", "Low", "Close"]]

    rows = []
    for t, (code, _) in jp.items():
        bars = bars_by_code.get(code)
        if bars is None or bars.empty:
            continue
        try:
            o, c = _norm(d1[t]["Open"]), _norm(d1[t]["Close"])
        except KeyError:
            continue
        prev = c.shift(1)
        bars = bars.sort_index()
        for day, g in bars.groupby(bars.index.date):
            ts = pd.Timestamp(day)
            if ts not in o.index or pd.isna(prev.get(ts)):
                continue
            for e in gap_events(g, float(o[ts]), float(prev[ts])) + swing_events(g):
                rows.append({**e, "code": code, "date": ts})
    ev = pd.DataFrame(rows)
    result = {"source": source, "fib_levels": list(FIB_LEVELS), "control_levels": list(CONTROL_LEVELS),
              "min_gap_pct": MIN_GAP_PCT, "bounce_frac": BOUNCE_FRAC, "patterns": {}}
    for pat in ("gap", "swing"):
        s = summarize_pattern(ev[ev["pattern"] == pat] if not ev.empty else ev)
        result["patterns"][pat] = s
        if s.get("diff") is not None:
            print(f"  {pat:5s} フィボ {s['fib_bounce'] * 100:.1f}%（{s['n_fib']}回） vs 比較用 {s['control_bounce'] * 100:.1f}%（{s['n_control']}回）"
                  f" 差 {s['diff'] * 100:+.1f}pt 95%区間 [{s['ci'][0] * 100:+.1f}, {s['ci'][1] * 100:+.1f}] {'✅' if s['significant'] else ''}")
            print("        " + "  ".join(f"{lv['level'] * 100:.1f}%:{(lv['bounce'] or 0) * 100:.0f}%({lv['n']})" for lv in s["levels"]))
        else:
            print(f"  {pat:5s} 件数不足（{s.get('n', 0)}回）")
    print(f"  データ: {source}")
    return result
