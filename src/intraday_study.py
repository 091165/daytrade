"""
9時台の動き方の検証（研究用・週次レポート＋銘柄ページの「始値から見た上下の目安」）

  excursion: 寄り値から10:00までに、上と下にそれぞれどこまで動いたか（1日の目安 D に対する倍率の中央値・80%点）
  brackets:  9:10 に入り、損切り＝9時台の目安の1/3、利確＝その 1・1.5・2・3 倍のとき、10:00 までに
             どちらが先に当たったか（どちらも当たらなければ 10:00 で手仕舞い）。損益は損切り幅を1とした単位（R）
  follow:    最初の10分の値幅が大きい日・小さい日に分けて、9:10 に最初の10分の向きに入る（順張り）と、
             逆に入る（逆張り）のどちらが成績が良いか
  vix:       前日の VIX（米国株の恐怖指数）の水準・変化で、翌日の値幅（目安の何倍か）が変わるか

1分足ではなく Yahoo の5分足（直近60日）を使う。寄り値は日足の始値（9:00〜9:05の足が欠ける日があるため）。
同じ5分足の中で損切りと利確の両方に届いたときは、損切りが先とみなす（厳しめ）。
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

from expected_range import RANGE_WINDOW, daily_range_pct

ENTRY_AT = "09:10"
END = "10:00"
MORNING_RATIO = 0.62             # 9時台の値幅 ÷ 1日の目安（morning_range の銘柄平均。銘柄ごとの値がなければこれ）
TARGET_MULTS = (1.0, 1.5, 2.0, 3.0)
Q_TH = 0.05


def _p_mean(x: np.ndarray) -> float:
    x = np.asarray(x, float)
    if len(x) < 3 or x.std(ddof=1) == 0:
        return 1.0
    return math.erfc(abs(x.mean()) / (x.std(ddof=1) / math.sqrt(len(x))) / math.sqrt(2))


def _p_diff(a: np.ndarray, b: np.ndarray) -> float:
    a, b = np.asarray(a, float), np.asarray(b, float)
    if len(a) < 3 or len(b) < 3:
        return 1.0
    se = math.sqrt(a.var(ddof=1) / len(a) + b.var(ddof=1) / len(b))
    return 1.0 if se == 0 else math.erfc(abs(a.mean() - b.mean()) / se / math.sqrt(2))


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


def day_frames(m5: pd.DataFrame, daily: pd.DataFrame) -> list[dict]:
    """1銘柄分：日ごとの 寄り値・1日の目安（円）・9:00〜10:00 の5分足（純粋関数）"""
    if m5 is None or daily is None or m5.empty or daily.empty:
        return []
    d = daily.copy()
    d.index = pd.to_datetime(d.index)
    if d.index.tz is not None:
        d.index = d.index.tz_localize(None)
    d.index = d.index.normalize()
    rng = daily_range_pct(d)
    avg = rng.rolling(RANGE_WINDOW).mean().shift(1)
    x = m5.dropna(subset=["Open", "High", "Low", "Close"])
    if getattr(x.index, "tz", None) is not None:
        x = x.tz_convert("Asia/Tokyo").tz_localize(None)
    out = []
    for day, g in x.groupby(x.index.normalize()):
        if day not in d.index or pd.isna(avg.get(day)):
            continue
        prev = d[d.index < day]
        if prev.empty:
            continue
        pc = float(prev["Close"].iloc[-1])
        hm = g.index.strftime("%H:%M")
        bars = g[hm < END]
        if bars.empty or hm[0] > "09:07":
            continue
        out.append({"date": day, "open": float(d.loc[day, "Open"]), "D": float(avg[day]) / 100 * pc, "bars": bars})
    return out


def excursion(day: dict) -> dict:
    """寄り値から10:00までの上・下への動き（1日の目安 D に対する倍率）"""
    b, o, D = day["bars"], day["open"], day["D"]
    return {"up": (float(b["High"].max()) - o) / D, "down": (o - float(b["Low"].min())) / D}


def first10(day: dict) -> dict | None:
    """最初の10分（9:10より前に始まった足）の向きと値幅、9:10 の入り値と、そのあとの足"""
    b = day["bars"]
    hm = b.index.strftime("%H:%M")
    first, after = b[hm < ENTRY_AT], b[hm >= ENTRY_AT]
    if first.empty or after.empty:
        return None
    entry = float(after["Open"].iloc[0])
    return {"move": (entry - day["open"]) / day["D"],
            "range": (float(first["High"].max()) - float(first["Low"].min())) / day["D"],
            "entry": entry, "after": after}


def simulate(after: pd.DataFrame, entry: float, side: int, stop: float, target: float) -> dict:
    """side=+1 買い / -1 売り。損切り・利確のどちらが先か。同じ足で両方なら損切り。結果は損切り幅を1とした R"""
    for _, r in after.iterrows():
        hi, lo = float(r["High"]), float(r["Low"])
        hit_stop = lo <= entry - stop if side > 0 else hi >= entry + stop
        hit_take = hi >= entry + target if side > 0 else lo <= entry - target
        if hit_stop:
            return {"r": -1.0, "decided": True, "win": False}
        if hit_take:
            return {"r": target / stop, "decided": True, "win": True}
    last = float(after["Close"].iloc[-1])
    r = (last - entry) * side / stop
    return {"r": r, "decided": False, "win": r > 0}


def trade_rows(days: list[dict], ratio: float = MORNING_RATIO) -> pd.DataFrame:
    """1銘柄分：9:10 に順張り・逆張りで入った場合を、利確の倍率ごとに（純粋関数）"""
    rows = []
    for d in days:
        f = first10(d)
        if f is None or f["move"] == 0:
            continue
        stop = ratio * d["D"] / 3
        if stop <= 0:
            continue
        follow = 1 if f["move"] > 0 else -1
        for mode, side in (("順張り", follow), ("逆張り", -follow)):
            for k in TARGET_MULTS:
                s = simulate(f["after"], f["entry"], side, stop, stop * k)
                rows.append({"date": d["date"], "mode": mode, "k": k, "first_range": f["range"], **s})
    return pd.DataFrame(rows)


def summarize_trades(t: pd.DataFrame) -> dict:
    """利確の倍率ごとの成績（順張り・逆張りを合わせた全体）と、最初の10分の大きさ別の 順張り vs 逆張り（利確1.5倍）"""
    if t.empty:
        return {"by_k": [], "by_size": [], "n_days": 0}
    out = {"n_days": int(t["date"].nunique()), "by_k": [], "by_size": [], "cuts": None}
    for k, g in t.groupby("k"):
        out["by_k"].append({"k": float(k), "n": int(len(g)), "win": float(g["win"].mean()),
                            "decided": float(g["decided"].mean()), "mean_r": float(g["r"].mean())})
    base = t[t["k"] == 1.5].copy()
    cuts = base.drop_duplicates("date")["first_range"].quantile([1 / 3, 2 / 3]).values
    out["cuts"] = [float(cuts[0]), float(cuts[1])]     # 最初の10分の値幅（1日の目安 D に対する倍率）の 1/3・2/3 点
    base["size"] = np.where(base["first_range"] <= cuts[0], "小さい", np.where(base["first_range"] >= cuts[1], "大きい", "ふつう"))
    for size in ("小さい", "ふつう", "大きい"):
        g = base[base["size"] == size]
        a, b = g[g["mode"] == "順張り"]["r"].values, g[g["mode"] == "逆張り"]["r"].values
        out["by_size"].append({"size": size, "n": int(len(a)),
                               "follow_r": float(a.mean()) if len(a) else None, "follow_win": float((a > 0).mean()) if len(a) else None,
                               "fade_r": float(b.mean()) if len(b) else None, "fade_win": float((b > 0).mean()) if len(b) else None,
                               "p_follow": _p_mean(a), "p_fade": _p_mean(b)})
    return out


def summarize_excursion(days: list[dict]) -> dict | None:
    if not days:
        return None
    ex = pd.DataFrame([excursion(d) for d in days])
    return {"n": int(len(ex)),
            "up_med": float(ex["up"].median()), "up_p80": float(ex["up"].quantile(0.8)),
            "down_med": float(ex["down"].median()), "down_p80": float(ex["down"].quantile(0.8))}


def vix_rows(daily: pd.DataFrame, vix: pd.Series) -> pd.DataFrame:
    """日ごとの 値幅÷目安 と、前日の VIX の水準・変化率（純粋関数）"""
    d = daily.copy()
    d.index = pd.to_datetime(d.index)
    if d.index.tz is not None:
        d.index = d.index.tz_localize(None)
    d.index = d.index.normalize()
    rng = daily_range_pct(d)
    ratio = rng / rng.rolling(RANGE_WINDOW).mean().shift(1)
    v = vix.copy()
    v.index = pd.to_datetime(v.index)
    if v.index.tz is not None:
        v.index = v.index.tz_localize(None)
    v = v.sort_index()
    chg = v.pct_change() * 100
    before = ratio.index - pd.Timedelta(days=1)
    out = pd.DataFrame({"ratio": ratio.values,
                        "vix": v.reindex(before, method="ffill").values,
                        "vix_chg": chg.reindex(before, method="ffill").values}, index=ratio.index)
    return out.dropna()


VIX_GROUPS = (("VIXが前日比+10%以上", lambda r: r["vix_chg"] >= 10), ("VIXが前日比-10%以下", lambda r: r["vix_chg"] <= -10),
              ("VIX 25以上", lambda r: r["vix"] >= 25), ("VIX 15未満", lambda r: r["vix"] < 15))


def summarize_vix(rows: pd.DataFrame) -> list[dict]:
    out = []
    calm = rows[(rows["vix_chg"].abs() < 5) & (rows["vix"].between(15, 25))]["ratio"].values
    for lab, f in VIX_GROUPS:
        x = rows[f(rows)]["ratio"].values
        out.append({"group": lab, "n": int(len(x)), "mean": float(x.mean()) if len(x) else None,
                    "calm_mean": float(calm.mean()) if len(calm) else None, "p": _p_diff(x, calm)})
    return out


def vix_signal(validation: dict | None, vix_change_pct: float | None) -> dict | None:
    """毎朝用：前日の VIX が +10% 以上上がっていて、検証で値幅が広がっていたら、その倍率と警告"""
    if vix_change_pct is None or vix_change_pct < 10:
        return None
    g = next((x for x in ((validation or {}).get("pooled") or {}).get("vix", []) if x["group"].startswith("VIXが前日比+10%")), None)
    if not g or g.get("mean") is None:
        return {"multiplier": None, "warning": {
            "type": "vix_jump", "severity": "medium", "label": f"😨 VIXが前日比 {vix_change_pct:+.0f}%",
            "message": "米国株の恐怖指数が大きく上がった翌日。値幅が広がりやすいとされる。",
            "action": "損切りの幅を守る。株数は増やさない。", "evidence": "未検証"}}
    ok = (g.get("q_vix") or 1) < Q_TH and g["mean"] > (g.get("calm_mean") or 0)
    ev = f"検証（{g['n']}日）：翌日の値幅は目安の {g['mean']:.2f}倍（落ち着いた日は {g['calm_mean']:.2f}倍）"
    return {"multiplier": g["mean"] if ok else None, "warning": {
        "type": "vix_jump", "severity": "medium" if ok else "low", "label": f"😨 VIXが前日比 {vix_change_pct:+.0f}%",
        "message": ("米国株の恐怖指数が大きく上がった翌日。値幅がふだんより広がりやすい。作戦の幅も広げてある。" if ok
                    else "米国株の恐怖指数が大きく上がった翌日。ただし過去の検証では値幅の差ははっきりしない。"),
        "action": "損切りの幅を守る。株数は増やさない。", "evidence": ev}}


def run_intraday_study() -> dict:
    import yfinance as yf
    from focus_stocks import FOCUS_STOCKS
    from stock_screen import SCREEN_JP

    names = {t: (c, n) for c, t, n in SCREEN_JP}
    for s in FOCUS_STOCKS:
        names.setdefault(s.ticker, (s.code, s.name))
    tick = " ".join(names)
    d1 = yf.download(tick + " ^VIX", period="2y", interval="1d", progress=False, group_by="ticker", auto_adjust=True, threads=True)
    m5 = yf.download(tick, period="60d", interval="5m", progress=False, group_by="ticker", auto_adjust=True, threads=True)
    vix = d1["^VIX"]["Close"].dropna()
    focus = {s.code for s in FOCUS_STOCKS}
    per_code, all_trades, all_days, all_vix = {}, [], [], []
    for t, (code, name) in names.items():
        try:
            daily = d1[t][["Open", "High", "Low", "Close"]].dropna()
            bars = m5[t][["Open", "High", "Low", "Close"]].dropna()
        except KeyError:
            continue
        days = day_frames(bars, daily)
        tr = trade_rows(days)
        vr = vix_rows(daily, vix)
        all_days += days
        all_vix.append(vr)
        if not tr.empty:
            all_trades.append(tr.assign(code=code))
        if code in focus:
            per_code[code] = {"name": name, "excursion": summarize_excursion(days), "trades": summarize_trades(tr),
                              "vix": summarize_vix(vr)}
    pooled_t = pd.concat(all_trades) if all_trades else pd.DataFrame()
    res = {"source": f"売買代金上位{len(names)}銘柄・5分足60日（VIX は日足2年）",
           "pooled": {"excursion": summarize_excursion(all_days), "trades": summarize_trades(pooled_t),
                      "vix": summarize_vix(pd.concat(all_vix)) if all_vix else []},
           "per_code": per_code, "target_mults": list(TARGET_MULTS)}
    # 補正：全体の 順張り・逆張り（大きさ別）と VIX の区分
    cells = [("pooled", "follow", x) for x in res["pooled"]["trades"]["by_size"]] + \
            [("pooled", "fade", x) for x in res["pooled"]["trades"]["by_size"]] + \
            [("pooled", "vix", x) for x in res["pooled"]["vix"]]
    ps = [c[2]["p_follow"] if c[1] == "follow" else c[2]["p_fade"] if c[1] == "fade" else c[2]["p"] for c in cells]
    for (scope, kind, x), q in zip(cells, bh(ps)):
        x[f"q_{kind}"] = q
    _print(res)
    return res


def _print(res: dict) -> None:
    p = res["pooled"]
    e = p["excursion"] or {}
    print(f"  寄りから10:00までの動き（全体・{e.get('n', 0)}日）：上 中央 {e.get('up_med', 0):.2f}D・80% {e.get('up_p80', 0):.2f}D ／ 下 中央 {e.get('down_med', 0):.2f}D・80% {e.get('down_p80', 0):.2f}D")
    for x in p["trades"]["by_k"]:
        print(f"  利確 {x['k']:.1f}倍：勝ち {x['win'] * 100:.0f}%・10時までに決着 {x['decided'] * 100:.0f}%・平均 {x['mean_r']:+.3f}R（{x['n']}回）")
    for x in p["trades"]["by_size"]:
        print(f"  最初の10分が{x['size']}：順張り {x['follow_r']:+.3f}R 勝ち{x['follow_win'] * 100:.0f}% q={x.get('q_follow', 1):.3f} ／ 逆張り {x['fade_r']:+.3f}R 勝ち{x['fade_win'] * 100:.0f}% q={x.get('q_fade', 1):.3f}（{x['n']}日）")
    for x in p["vix"]:
        print(f"  {x['group']}：翌日の値幅 目安の{(x['mean'] or 0):.2f}倍（{x['n']}日）・落ち着いた日 {(x['calm_mean'] or 0):.2f}倍 q={x.get('q_vix', 1):.3f}")
    for c, r in res["per_code"].items():
        e = r["excursion"] or {}
        ks = " ".join(f"{x['k']:.1f}倍:{x['mean_r']:+.2f}R/{x['win'] * 100:.0f}%" for x in r["trades"]["by_k"])
        print(f"  {r['name']}：上 {e.get('up_med', 0):.2f}/{e.get('up_p80', 0):.2f}D 下 {e.get('down_med', 0):.2f}/{e.get('down_p80', 0):.2f}D ／ {ks}")
