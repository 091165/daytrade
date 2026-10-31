"""
メンサロ記事（過去ログ1〜4）の考え方を、過去データで確かめる（研究用・週次レポート）

確かめること（どれも「寄り→引け」で測る。デイトレで取れるのは寄り後だけなので）:
  v2:       判定材料にドル円・S&P500 を足すと、方向の的中率が上がるか（後半の期間だけで比較）
  sp05:     S&P500 が ±0.5% 以上動いた翌日、寄りで同じ向きに入り引けで手仕舞うと勝てるか
  friday:   金曜は今週の向きと逆に動きやすいか。前日15時→当日9時（窓）が同じ向きなら強まるか
  month_end:月末最終営業日も同じ（今月の向きと逆に動きやすいか）
  weekday:  月曜（連休明け）・金曜はトレンド型、火〜木はレンジ型か（|引け−寄り| ÷ 値幅）
  kenri:    権利付き最終日は上げやすいか・権利落ち日は窓を下に開けやすいか
  dev25:    25日線から大きく離れた銘柄は、その日の寄り後にどちらへ動きやすいか
  strength: 判定の確度が高い日は、寄り→10:00 の向きが 10:00→引け も続くか（1時間足・約2年）

複数の検定をまとめて Benjamini–Hochberg 補正（q<0.05）。件数の少ないもの（権利日など）は参考扱い。
"""

from __future__ import annotations

import math
from datetime import date

import numpy as np
import pandas as pd

from market_direction import build_market_direction, build_market_direction_v2
from warnings_module import is_last_trading_day_of_month

START = "2019-01-01"
SP_THRESHOLD = 0.5
GAP_THRESHOLD = 0.2          # 窓 ±0.2% 以上を「時間外で動いた」とみなす
DEV_SIGMA_LOOKBACK = 250
STOCKS = {"5803": "フジクラ", "9984": "SBG", "6981": "村田"}
Q_TH = 0.05


# ========== 小さな統計 ==========

def hit_p(k: int, n: int) -> float:
    """当たり k/n が 50% と違うか（両側・正規近似）"""
    if n == 0:
        return 1.0
    z = abs(k - n / 2) / math.sqrt(n / 4)
    return math.erfc(z / math.sqrt(2))


def mean_p(x: np.ndarray) -> float:
    """平均が 0 と違うか（両側・正規近似）"""
    x = np.asarray(x, dtype=float)
    if len(x) < 3 or x.std(ddof=1) == 0:
        return 1.0
    z = abs(x.mean()) / (x.std(ddof=1) / math.sqrt(len(x)))
    return math.erfc(z / math.sqrt(2))


def diff_p(a: np.ndarray, b: np.ndarray) -> float:
    """2群の平均の差（Welch・正規近似）"""
    a, b = np.asarray(a, float), np.asarray(b, float)
    if len(a) < 3 or len(b) < 3:
        return 1.0
    se = math.sqrt(a.var(ddof=1) / len(a) + b.var(ddof=1) / len(b))
    return 1.0 if se == 0 else math.erfc(abs(a.mean() - b.mean()) / se / math.sqrt(2))


def bh_qvalues(ps: list[float]) -> list[float]:
    n = len(ps)
    if n == 0:
        return []
    order = np.argsort(ps)
    q = np.empty(n)
    prev = 1.0
    for rank, i in reversed(list(enumerate(order, start=1))):
        prev = min(prev, ps[i] * n / rank)
        q[i] = prev
    return [float(v) for v in q]


def signed_summary(ret: pd.Series, side: pd.Series) -> dict:
    """side（+1=買い / -1=売り）で入ったときの成績。ret は寄り→引けの%"""
    m = side.notna() & (side != 0) & ret.notna()
    pnl = (ret[m] * side[m]).astype(float)
    n = len(pnl)
    k = int((pnl > 0).sum())
    return {"n": n, "hit": k / n if n else None, "mean": float(pnl.mean()) if n else None,
            "sum": float(pnl.sum()) if n else None, "p_hit": hit_p(k, n), "p_mean": mean_p(pnl.values)}


# ========== 日ごとの表 ==========

def _norm(s: pd.Series) -> pd.Series:
    s = s.dropna().copy()
    idx = pd.to_datetime(s.index)
    if getattr(idx, "tz", None) is not None:
        idx = idx.tz_localize(None)
    s.index = idx.normalize()
    return s[~s.index.duplicated(keep="last")]


def build_day_table(ohlc: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """日本の営業日ごとに、寄り前にわかる材料と、その日の結果を並べる（純粋関数）。

    ohlc: ティッカー → 日足（Open/High/Low/Close）。^N225・^IXIC・^DJI・^SOX・^GSPC・JPY=X・NKD=F
    米国の材料は「日本の日付の前日までに確定した最新の終値」。前日の米国が休場なら us_closed=True。
    """
    n = {k: _norm(ohlc["^N225"][k]) for k in ("Open", "High", "Low", "Close")}
    days = n["Close"].index
    us_pct = {t: _norm(ohlc[t]["Close"]).pct_change() * 100 for t in ("^IXIC", "^DJI", "^SOX", "^GSPC", "JPY=X")}
    us_days = set(_norm(ohlc["^GSPC"]["Close"]).index)
    nkd = _norm(ohlc["NKD=F"]["Close"])
    rows = []
    for i in range(1, len(days)):
        t, tp = days[i], days[i - 1]
        before = t - pd.Timedelta(days=1)
        vals = {k: s.asof(before) for k, s in us_pct.items()}
        fut = nkd.asof(before)
        if any(pd.isna(v) for v in vals.values()) or pd.isna(fut):
            continue
        us_session = before
        while us_session.weekday() >= 5:
            us_session -= pd.Timedelta(days=1)
        rows.append({
            "date": t, "prev_date": tp,
            "open": n["Open"][t], "high": n["High"][t], "low": n["Low"][t], "close": n["Close"][t],
            "prev_close": n["Close"][tp],
            "nas": vals["^IXIC"], "dow": vals["^DJI"], "sox": vals["^SOX"], "sp": vals["^GSPC"],
            "usdjpy": vals["JPY=X"], "futures_diff": float(fut - n["Close"][tp]),
            "us_closed": us_session not in us_days,
            "gap_days": (t - tp).days,
        })
    df = pd.DataFrame(rows).set_index("date")
    df["ret"] = (df["close"] / df["open"] - 1) * 100
    df["gap"] = (df["open"] / df["prev_close"] - 1) * 100
    df["trendiness"] = (df["close"] - df["open"]).abs() / (df["high"] - df["low"]).replace(0, np.nan)
    df["range_pct"] = (df["high"] - df["low"]) / df["open"] * 100
    return df


def add_verdicts(df: pd.DataFrame) -> pd.DataFrame:
    v1, conf, v2, agree = [], [], [], []
    for r in df.itertuples():
        a = build_market_direction(r.nas, r.dow, r.sox, r.futures_diff)
        b = build_market_direction_v2(r.nas, r.dow, r.sox, r.sp, r.usdjpy, r.futures_diff)
        v1.append(a.verdict)
        conf.append(a.confidence)
        v2.append(b.verdict)
        agree.append(b.agreement_count)
    side = {"buy_bias": 1, "sell_bias": -1, "hands_off": 0}
    out = df.copy()
    out["v1"], out["conf"], out["v2"], out["agree"] = v1, conf, v2, agree
    out["side_v1"] = out["v1"].map(side)
    out["side_v2"] = out["v2"].map(side)
    return out


# ========== 各検証（純粋関数） ==========

def study_v2(df: pd.DataFrame) -> dict:
    """現行版と v2 を、後半の期間で比べる。一致数ごとの的中率も（全期間）。"""
    half = df.index[len(df) // 2]
    late = df[df.index >= half]
    out = {"split": str(half.date())}
    for k in ("v1", "v2"):
        s = signed_summary(late["ret"], late[f"side_{k}"])
        s["hands_off_share"] = float((late[k] == "hands_off").mean())
        out[k] = s
    trend = df[df["side_v1"] != 0]
    out["by_agree"] = []
    for a, g in trend.groupby("agree"):
        s = signed_summary(g["ret"], g["side_v1"])
        out["by_agree"].append({"agree": int(a), **s})
    return out


def study_sp05(df: pd.DataFrame, rets: dict[str, pd.Series]) -> dict:
    """S&P500 ±0.5% の翌日に、寄りで同じ向きに入って引けで手仕舞う。rets: 名前 → 寄り→引け%"""
    side = pd.Series(0, index=df.index)
    side[df["sp"] >= SP_THRESHOLD] = 1
    side[df["sp"] <= -SP_THRESHOLD] = -1
    out = {}
    for name, r in rets.items():
        r = r.reindex(df.index)
        res = {}
        for lab, sgn in (("up", 1), ("down", -1)):
            s = side.where(side == sgn, 0)
            res[lab] = signed_summary(r, s)
            pnl = (r * s)[(s != 0) & r.notna()].sort_values()
            trimmed = pnl.iloc[3:-3] if len(pnl) > 10 else pnl
            res[lab]["mean_trim"] = float(trimmed.mean()) if len(trimmed) else None
            res[lab]["by_year"] = {int(y): round(float(g.mean()), 3) for y, g in pnl.groupby(pnl.index.year)}
        out[name] = res
    return out


def _period_reversal(df: pd.DataFrame, mask: pd.Series, period_key) -> dict:
    """mask の日に、その期間（週・月）の寄り値→前日引けの向きと逆に入る。窓の向きで分ける。"""
    first_open = df.groupby(period_key)["open"].transform("first")
    trend = np.sign(df["prev_close"] - first_open)
    gap_dir = np.where(df["gap"] >= GAP_THRESHOLD, 1, np.where(df["gap"] <= -GAP_THRESHOLD, -1, 0))
    d = df[mask & (trend != 0)].copy()
    d["side"] = -trend[d.index]
    g = pd.Series(gap_dir, index=df.index)[d.index]
    out = {"all": signed_summary(d["ret"], d["side"])}
    tr = trend[d.index]
    for lab, m in (("gap_same", g == tr), ("gap_opposite", g == -tr), ("gap_small", g == 0)):
        out[lab] = signed_summary(d["ret"][m], d["side"][m])
    return out


def study_closing(df: pd.DataFrame) -> dict:
    week = df.index.to_period("W")
    month = df.index.to_period("M")
    fri = pd.Series(df.index.weekday == 4, index=df.index)
    # 金曜が休みの週は、その週の最終営業日を使う
    last_of_week = pd.Series(df.index, index=df.index).groupby(week).transform("max") == df.index
    me = pd.Series([is_last_trading_day_of_month(d.date()) for d in df.index], index=df.index)
    return {"friday": _period_reversal(df, last_of_week & fri, week),
            "month_end": _period_reversal(df, me, month),
            "both": _period_reversal(df, me & last_of_week, month)}


def weekday_group(df: pd.DataFrame) -> pd.Series:
    """週明け（月曜・連休明け）／金曜／火〜木"""
    wd = df.index.weekday
    after_break = (df["gap_days"] >= 3).values
    return pd.Series(np.where(after_break, "週明け", np.where(wd == 4, "金曜", "火〜木")), index=df.index)


def study_weekday(df: pd.DataFrame, extra: dict[str, pd.DataFrame]) -> dict:
    """トレンドらしさ |引け−寄り| ÷（高値−安値）と値幅を、曜日の型ごとに比べる。"""
    out = {}
    for name, d in {"日経平均": df, **extra}.items():
        g = weekday_group(d)
        base = d["trendiness"][g == "火〜木"].dropna()
        res = {}
        for lab in ("週明け", "金曜", "火〜木"):
            x = d["trendiness"][g == lab].dropna()
            r = d["range_pct"][g == lab].dropna()
            res[lab] = {"n": len(x), "trendiness": float(x.mean()), "range_pct": float(r.mean()),
                        "p_vs_midweek": diff_p(x.values, base.values) if lab != "火〜木" else None}
        out[name] = res
    return out


def study_after_break(df: pd.DataFrame) -> dict:
    """連休明け（前営業日から4日以上あいた日）の窓の大きさと値幅を、普段の日と比べる"""
    brk = df["gap_days"] >= 4
    out = {}
    for lab, m in (("連休明け", brk), ("普段の日", ~brk)):
        out[lab] = {"n": int(m.sum()), "abs_gap": float(df["gap"][m].abs().mean()),
                    "range_pct": float(df["range_pct"][m].mean())}
    out["p_gap"] = diff_p(df["gap"][brk].abs().values, df["gap"][~brk].abs().values)
    return out


def kenri_days(trading_days: list[date]) -> tuple[set[date], set[date]]:
    """権利付き最終日（月の最終営業日の2営業日前）と権利落ち日（その翌営業日）。2019/7以降の受渡しルール。"""
    days = sorted(trading_days)
    by_month: dict[tuple[int, int], list[date]] = {}
    for d in days:
        by_month.setdefault((d.year, d.month), []).append(d)
    last, ex = set(), set()
    pos = {d: i for i, d in enumerate(days)}
    for ds in by_month.values():
        i = pos[ds[-1]] - 2
        if i >= 0 and i + 1 < len(days):
            last.add(days[i])
            ex.add(days[i + 1])
    return last, ex


def study_kenri(df: pd.DataFrame) -> dict:
    last, ex = kenri_days([d.date() for d in df.index])
    dd = pd.Series([d.date() for d in df.index], index=df.index)
    m = df.index.month
    out = {}
    for lab, months in (("3月・9月", (3, 9)), ("6月・12月", (6, 12)), ("全部の月", tuple(range(1, 13)))):
        mm = pd.Series(np.isin(m, months), index=df.index)
        lm = dd.isin(last) & mm
        em = dd.isin(ex) & mm
        out[lab] = {"last_day_buy": signed_summary(df["ret"][lm], pd.Series(1, index=df.index)[lm]),
                    "ex_day_gap_mean": float(df["gap"][em].mean()) if em.any() else None,
                    "ex_day_n": int(em.sum())}
    return out


def deviation_frame(daily: pd.DataFrame) -> pd.DataFrame:
    """前日終値ベースの25日線乖離と、その銘柄自身の過去250日の乖離の ±2σ。当日の足は使わない。"""
    c = daily["Close"]
    dev = (c / c.rolling(25).mean() - 1) * 100
    mu = dev.rolling(DEV_SIGMA_LOOKBACK, min_periods=120).mean()
    sd = dev.rolling(DEV_SIGMA_LOOKBACK, min_periods=120).std()
    out = pd.DataFrame({"dev": dev.shift(1), "lo": (mu - 2 * sd).shift(1), "hi": (mu + 2 * sd).shift(1),
                        "ret": (daily["Close"] / daily["Open"] - 1) * 100})
    return out.dropna()


def study_dev25(stocks: dict[str, pd.DataFrame]) -> dict:
    out = {}
    for name, d in stocks.items():
        f = deviation_frame(d)
        res = {}
        for lab, m in (("下方乖離（-2σ以下）", f["dev"] <= f["lo"]), ("上方乖離（+2σ以上）", f["dev"] >= f["hi"]),
                       ("その間", (f["dev"] > f["lo"]) & (f["dev"] < f["hi"]))):
            r = f["ret"][m]
            k = int((r > 0).sum())
            res[lab] = {"n": len(r), "up": k / len(r) if len(r) else None,
                        "mean": float(r.mean()) if len(r) else None, "p_mean": mean_p(r.values)}
        out[name] = res
    return out


def study_strength(hourly: dict[str, pd.DataFrame], conf_by_day: pd.Series) -> dict:
    """1時間足で、寄り→10:00 と 10:00→引け が同じ向きになる割合を確度ごとに。"""
    out = {}
    for name, h in hourly.items():
        if h is None or h.empty:
            continue
        hm = h.index.strftime("%H:%M")
        rows = []
        for day, g in h.groupby(h.index.normalize()):
            ghm = g.index.strftime("%H:%M")
            if "09:00" not in ghm or "10:00" not in ghm:
                continue
            o = float(g["Open"].iloc[0])
            p10 = float(g.loc[ghm == "10:00", "Open"].iloc[0])
            c = float(g["Close"].iloc[-1])
            if o == p10 or p10 == c:
                continue
            rows.append({"date": day, "same": np.sign(p10 - o) == np.sign(c - p10)})
        del hm
        d = pd.DataFrame(rows)
        if d.empty:
            continue
        d["conf"] = d["date"].map(conf_by_day)
        res = {}
        for lab in ("high", "medium", "low"):
            x = d[d["conf"] == lab]["same"]
            res[lab] = {"n": len(x), "same": float(x.mean()) if len(x) else None, "p": hit_p(int(x.sum()), len(x))}
        out[name] = res
    return out


def collect_pvalues(res: dict) -> list[tuple[str, float]]:
    """補正にかける主な検定（向きを決めずに試したもの）"""
    ps = []
    for k in ("v1", "v2"):
        ps.append((f"v2比較:{k} 的中", res["v2"][k]["p_hit"]))
    for name, r in res["sp05"].items():
        for lab in ("up", "down"):
            ps.append((f"S&P±0.5%:{name}:{lab}", r[lab]["p_mean"]))
    for kind, r in res["closing"].items():
        for lab, s in r.items():
            ps.append((f"手仕舞い:{kind}:{lab}", s["p_mean"]))
    for name, r in res["weekday"].items():
        for lab in ("週明け", "金曜"):
            ps.append((f"曜日:{name}:{lab}", r[lab]["p_vs_midweek"]))
    for name, r in res["dev25"].items():
        for lab, s in r.items():
            if lab != "その間":
                ps.append((f"乖離:{name}:{lab}", s["p_mean"]))
    for name, r in res.get("strength", {}).items():
        for lab, s in r.items():
            ps.append((f"確度:{name}:{lab}", s["p"]))
    return ps


# ========== 実行（データ取得あり） ==========

def run_idea_study() -> dict:
    import yfinance as yf

    idx_t = ["^N225", "^IXIC", "^DJI", "^SOX", "^GSPC", "JPY=X", "NKD=F"]
    stk_t = {f"{c}.T": n for c, n in STOCKS.items()}
    raw = yf.download(" ".join(idx_t + list(stk_t)), start=START, interval="1d", progress=False,
                      group_by="ticker", auto_adjust=True, threads=True)
    ohlc = {}
    for t in idx_t + list(stk_t):
        try:
            x = raw[t][["Open", "High", "Low", "Close"]].dropna()
        except KeyError:
            x = pd.DataFrame()
        if x.empty:  # まとめて取ると時々空で返るので、1つずつ取り直す
            x = yf.download(t, start=START, interval="1d", progress=False, auto_adjust=True)
            if isinstance(x.columns, pd.MultiIndex):
                x.columns = x.columns.get_level_values(0)
            x = x[["Open", "High", "Low", "Close"]].dropna() if not x.empty else x
        if x.empty:
            raise RuntimeError(f"{t} の日足を取得できませんでした")
        ohlc[t] = x
    df = add_verdicts(build_day_table(ohlc))

    stocks = {}
    for t, name in stk_t.items():
        d = ohlc[t].copy()
        d.index = pd.to_datetime(d.index).tz_localize(None).normalize() if d.index.tz is not None else pd.to_datetime(d.index).normalize()
        d = d[(d["High"] > d["Low"])]
        dd = d.assign(ret=(d["Close"] / d["Open"] - 1) * 100,
                      trendiness=(d["Close"] - d["Open"]).abs() / (d["High"] - d["Low"]),
                      range_pct=(d["High"] - d["Low"]) / d["Open"] * 100)
        dd = dd.join(df[["gap_days"]], how="inner")
        stocks[name] = dd

    res = {"period": f"{df.index[0].date()}〜{df.index[-1].date()}", "n_days": len(df),
           "us_closed_days": int(df["us_closed"].sum())}
    ok = df[~df["us_closed"]]
    res["v2"] = study_v2(ok)
    res["sp05"] = study_sp05(ok, {"日経平均": ok["ret"], **{n: s["ret"] for n, s in stocks.items()}})
    res["closing"] = study_closing(df)
    res["weekday"] = study_weekday(df, stocks)
    res["after_break"] = study_after_break(df)
    res["kenri"] = study_kenri(df[df.index >= "2019-07-16"])
    res["dev25"] = study_dev25({"日経平均": ohlc["^N225"], **{n: ohlc[t] for t, n in stk_t.items()}})
    # 米国休場の翌朝、現行版がどう判定していたか（古い騰落率をもう一度使っていないか）
    closed = df[df["us_closed"]]
    res["us_closed"] = {"n": len(closed), "trend_verdicts": int((closed["side_v1"] != 0).sum()),
                        "signed": signed_summary(closed["ret"], closed["side_v1"])}

    try:
        h = yf.download(" ".join(["^N225"] + list(stk_t)), period="730d", interval="1h", progress=False,
                        group_by="ticker", auto_adjust=True, threads=True)
        hourly = {}
        for t, name in [("^N225", "日経平均")] + list(stk_t.items()):
            x = h[t].dropna(subset=["Open", "Close"])
            if getattr(x.index, "tz", None) is not None:
                x = x.tz_convert("Asia/Tokyo").tz_localize(None)
            hourly[name] = x
        res["strength"] = study_strength(hourly, df["conf"].where(df["side_v1"] != 0, "low"))
    except Exception as e:  # 1時間足が取れなくても他の結果は出す
        print(f"  ⚠️ 1時間足を取得できませんでした: {e}")
        res["strength"] = {}

    ps = collect_pvalues(res)
    qs = bh_qvalues([p for _, p in ps])
    res["tests"] = [{"name": n, "p": p, "q": q, "significant": q < Q_TH} for (n, p), q in zip(ps, qs)]
    _print(res)
    return res


def _f(x, pct=True):
    if x is None:
        return "—"
    return f"{x * 100:.1f}%" if pct else f"{x:+.3f}"


def _print(res: dict) -> None:
    print(f"  期間 {res['period']}（{res['n_days']}日・米国休場明け {res['us_closed_days']}日）")
    v = res["v2"]
    for k in ("v1", "v2"):
        s = v[k]
        print(f"  [{k}] 後半 {v['split']}〜: 方向の日 {s['n']}日 的中 {_f(s['hit'])} 平均 {_f(s['mean'], False)}% 手出し無用 {_f(s['hands_off_share'])}")
    print("   一致数ごと(現行版で方向あり): " + "  ".join(f"{a['agree']}:{_f(a['hit'])}({a['n']})" for a in v["by_agree"]))
    for name, r in res["sp05"].items():
        print(f"  S&P±0.5% {name}: " + "  ".join(
            f"{lab} n={r[lab]['n']} 的中{_f(r[lab]['hit'])} 平均{_f(r[lab]['mean'], False)} 外れ値除く{_f(r[lab]['mean_trim'], False)} 年別{r[lab]['by_year']}"
            for lab in ("up", "down")))
    for kind, r in res["closing"].items():
        print(f"  {kind}: " + "  ".join(f"{lab} n={s['n']} 反転{_f(s['hit'])} 平均{_f(s['mean'], False)}" for lab, s in r.items()))
    for name, r in res["weekday"].items():
        print(f"  曜日 {name}: " + "  ".join(f"{lab} トレンド度{s['trendiness']:.3f} 値幅{s['range_pct']:.2f}%({s['n']})" for lab, s in r.items()))
    for lab, r in res["kenri"].items():
        s = r["last_day_buy"]
        print(f"  権利 {lab}: 付き最終日 買い n={s['n']} 的中{_f(s['hit'])} 平均{_f(s['mean'], False)} / 落ち日の窓 {_f(r['ex_day_gap_mean'], False)}%({r['ex_day_n']})")
    for name, r in res["dev25"].items():
        print(f"  乖離 {name}: " + "  ".join(f"{lab} n={s['n']} 上げ{_f(s['up'])} 平均{_f(s['mean'], False)}" for lab, s in r.items()))
    for name, r in res.get("strength", {}).items():
        print(f"  確度 {name}: " + "  ".join(f"{lab} 続く{_f(s['same'])}({s['n']})" for lab, s in r.items()))
    ab = res["after_break"]
    print(f"  連休明け: 窓 {ab['連休明け']['abs_gap']:.2f}%（{ab['連休明け']['n']}日） vs 普段 {ab['普段の日']['abs_gap']:.2f}%  値幅 {ab['連休明け']['range_pct']:.2f}% vs {ab['普段の日']['range_pct']:.2f}%  p={ab['p_gap']:.3f}")
    u = res["us_closed"]
    print(f"  米国休場明け: {u['n']}日中 現行版で方向ありの判定 {u['trend_verdicts']}日 的中{_f(u['signed']['hit'])}")
    sig = [t for t in res["tests"] if t["significant"]]
    print(f"  補正後に有意（q<{Q_TH}）: " + (", ".join(f"{t['name']} q={t['q']:.3f}" for t in sig) if sig else "なし"))
