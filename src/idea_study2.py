"""
メンサロ記事の考え方の検証・その2（研究用・週次レポート。idea_study から呼ぶ）

  threshold: 「変動なし」の幅を ±0.3%（今）と ±0.5%（記事）で比べる
  europe:    欧州（DAX・FTSE）の前日の向きを判定に足すと当たりが良くなるか
  asym:      買い判定日と売り判定日で、判定方向への進みやすさ・速さが違うか
  jump:      現対が大きくプラスで高く寄った日、寄り→10:00 は下げやすいか（逆も）
  us_fut:    米国の引け→日本の寄りまでの米国株先物（ES=F）の向きが、10:00→引け に効くか
  cp1030:    10:30 は向きが変わりやすい節目か（30分足、ほかの節目と比べる）
  stock_week:承認・試用銘柄の金曜・月末も、その銘柄自身の週足・月足と逆に動くか
  month_bar: 先月の月足（陽線・陰線）と、その日の上げやすさ
  prev_line: 9時台に前日終値を割ったあと、10:00 までに戻したか・そのまま進んだか（5分足）
  dividend:  権利落ち日に、配当ほど下がらずに寄った銘柄はその日に下げやすいか
  big_caps:  寄与度の大きい値嵩株が複数、キリのいい値段に触れた日の日経の向き
  index_dev: ナスダック・SOX が25日線から ±3σ 離れた翌日、日経の前場と後場の向き
  hands_off: 手出し無用の日に、窓と逆に入る（寄り→10:00）と当たるか。9〜10時の型は続くか

どれも件数・的中・平均を出し、p値は idea_study の BH 補正にまとめて入れる。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from idea_study import _norm, hit_p, mean_p, signed_summary, wilson_ci
from index_deviation import dev_sigma
from market_direction import build_market_direction

BIG_CAPS = {"9983.T": "ファストリ", "8035.T": "東エレク", "6857.T": "アドバンテスト", "9984.T": "SBG",
            "6762.T": "TDK", "4063.T": "信越化学", "9433.T": "KDDI", "6954.T": "ファナック",
            "4543.T": "テルモ", "6367.T": "ダイキン"}
JUMP_FUTURES = 300          # 現対 +300円 以上
JUMP_GAP = 0.8              # かつ 寄りの窓 +0.8% 以上


def _rate(x: pd.Series) -> dict:
    x = x.dropna().astype(bool)
    n, k = len(x), int(x.sum())
    return {"n": n, "rate": k / n if n else None, "ci": wilson_ci(k, n), "p": hit_p(k, n)}


def _half(df: pd.DataFrame) -> pd.DataFrame:
    return df[df.index >= df.index[len(df) // 2]]


# ========== 判定の作り方（しきい値・欧州） ==========

def study_threshold(df: pd.DataFrame) -> dict:
    """後半の期間で、しきい値 0.3% と 0.5% の判定の成績を比べる"""
    late = _half(df)
    out = {}
    for th in (0.3, 0.5):
        side = pd.Series([{"buy_bias": 1, "sell_bias": -1}.get(
            build_market_direction(r.nas, r.dow, r.sox, r.futures_diff, weak_threshold=th).verdict, 0)
            for r in late.itertuples()], index=late.index)
        s = signed_summary(late["ret"], side)
        s["hands_off_share"] = float((side == 0).mean())
        out[f"{th:.1f}"] = s
    return out


def study_europe(df: pd.DataFrame, eu: dict[str, pd.Series]) -> dict:
    """今の判定で方向ありの日を、欧州2指数の多数がその向きと逆かどうかで分ける（後半）"""
    late = _half(df[df["side_v1"] != 0])
    before = late.index - pd.Timedelta(days=1)
    votes = sum(np.sign(np.where(np.abs(s.reindex(before, method="ffill").values) >= 0.3,
                                 s.reindex(before, method="ffill").values, 0)) for s in eu.values())
    against = pd.Series(np.sign(votes) == -late["side_v1"].values, index=late.index)
    agree = pd.Series(np.sign(votes) == late["side_v1"].values, index=late.index)
    return {"agree": signed_summary(late["ret"][agree], late["side_v1"][agree]),
            "against": signed_summary(late["ret"][against], late["side_v1"][against]),
            "all": signed_summary(late["ret"], late["side_v1"])}


# ========== 1時間足・30分足・5分足 ==========

def day_points(h: pd.DataFrame) -> pd.DataFrame:
    """1時間足から日ごとの 寄り・10:00・前場の引け・後場の寄り・引け と、寄り後の高値・安値（純粋関数）"""
    rows = []
    for day, g in h.groupby(h.index.normalize()):
        hm = g.index.strftime("%H:%M")
        if "09:00" not in hm or "10:00" not in hm:
            continue
        am, pm = g[hm < "12:00"], g[hm >= "12:00"]
        rows.append({"date": day, "open": float(g["Open"].iloc[0]), "p10": float(g.loc[hm == "10:00", "Open"].iloc[0]),
                     "am_close": float(am["Close"].iloc[-1]), "pm_open": float(pm["Open"].iloc[0]) if len(pm) else np.nan,
                     "close": float(g["Close"].iloc[-1]),
                     "hi_first": float(g["High"].iloc[0]), "lo_first": float(g["Low"].iloc[0]),
                     "hi2": float(g["High"].iloc[:2].max()), "lo2": float(g["Low"].iloc[:2].min())})
    return pd.DataFrame(rows).set_index("date") if rows else pd.DataFrame()


def study_asym(df: pd.DataFrame, pts: pd.DataFrame) -> dict:
    """買い判定日と売り判定日：寄り→引けの成績と、判定方向に 0.5% 進んだのが 9時台か・10時台までか"""
    out = {}
    for lab, sgn in (("買いポジ主体", 1), ("売りポジ主体", -1)):
        d = df[df["side_v1"] == sgn]
        s = signed_summary(d["ret"], d["side_v1"])
        p = pts.reindex(d.index).dropna(subset=["open"])
        if len(p):
            far1 = (p["hi_first"] / p["open"] - 1) * 100 if sgn > 0 else (1 - p["lo_first"] / p["open"]) * 100
            far2 = (p["hi2"] / p["open"] - 1) * 100 if sgn > 0 else (1 - p["lo2"] / p["open"]) * 100
            s["reach_9h"] = _rate(far1 >= 0.5)
            s["reach_10h"] = _rate(far2 >= 0.5)
        out[lab] = s
    return out


def study_jump(df: pd.DataFrame, pts: pd.DataFrame) -> dict:
    """現対が大きく、窓も同じ向きに大きく開けた日の、寄り→10:00 の向き（逆に動いたら当たり）"""
    p = pts.reindex(df.index)
    r10 = (p["p10"] / p["open"] - 1) * 100
    out = {}
    for lab, sgn in (("高寄り（現対+・窓+）", 1), ("安寄り（現対−・窓−）", -1)):
        m = (sgn * df["futures_diff"] >= JUMP_FUTURES) & (sgn * df["gap"] >= JUMP_GAP) & r10.notna()
        out[lab] = signed_summary(r10[m], pd.Series(-sgn, index=df.index)[m])
    return out


def us_futures_move(es: pd.DataFrame, jp_days: pd.DatetimeIndex) -> pd.Series:
    """米国の現物の引け（ニューヨーク16:00）→ 日本の寄り（9:00）までの ES=F の騰落%（純粋関数）"""
    s = es["Close"].dropna()
    idx = s.index if s.index.tz is not None else s.index.tz_localize("UTC")
    # 1時間足の時刻は「足の始まり」。終値が確定するのは1時間後なので、確定した時刻に付け替える（先の値段を使わない）
    s = pd.Series(s.values, index=idx + pd.Timedelta(hours=1))
    out = {}
    for day in jp_days:
        jp_open = pd.Timestamp(day.date()).tz_localize("Asia/Tokyo") + pd.Timedelta(hours=9)
        ny = jp_open.tz_convert("America/New_York")
        us_close = ny.normalize() + pd.Timedelta(hours=16)
        if us_close > ny:
            us_close -= pd.Timedelta(days=1)
        while us_close.weekday() >= 5:
            us_close -= pd.Timedelta(days=1)
        a = s[:us_close]
        b = s[:jp_open]
        if a.empty or b.empty or a.index[-1] < us_close - pd.Timedelta(hours=3) or b.index[-1] <= a.index[-1]:
            continue
        out[day] = (b.iloc[-1] / a.iloc[-1] - 1) * 100
    return pd.Series(out, dtype=float)


def study_us_futures(fut: pd.Series, pts: dict[str, pd.DataFrame]) -> dict:
    out = {}
    for name, p in pts.items():
        side = np.sign(fut.where(fut.abs() >= 0.2)).reindex(p.index)
        for seg, a, b in (("9:00→10:00", "open", "p10"), ("10:00→引け", "p10", "close")):
            r = (p[b] / p[a] - 1) * 100
            out[f"{name}:{seg}"] = signed_summary(r, side)
    return out


def study_cp1030(m30: dict[str, pd.DataFrame]) -> dict:
    """節目の前後30分で向きが変わった割合（30分足）。10:30 とほかの節目を比べる"""
    flips = {k: [] for k in ("09:30", "10:00", "10:30", "11:00")}
    for h in m30.values():
        for _, g in h.groupby(h.index.normalize()):
            hm = list(g.index.strftime("%H:%M"))
            for cp in flips:
                if cp not in hm:
                    continue
                i = hm.index(cp)
                if i == 0:
                    continue
                before = g["Close"].iloc[i - 1] - g["Open"].iloc[i - 1]
                after = g["Close"].iloc[i] - g["Open"].iloc[i]
                if before != 0 and after != 0:
                    flips[cp].append(np.sign(before) != np.sign(after))
    out = {cp: _rate(pd.Series(v, dtype=bool)) for cp, v in flips.items()}
    others = sum((flips[c] for c in ("09:30", "10:00", "11:00")), [])
    a, b = np.array(flips["10:30"], float), np.array(others, float)
    from idea_study import diff_p
    out["p_vs_others"] = diff_p(a, b)
    return out


# ========== 日足（個別株） ==========

def stock_day_frame(d: pd.DataFrame) -> pd.DataFrame:
    d = d.copy()
    d.index = pd.to_datetime(d.index)
    if d.index.tz is not None:
        d.index = d.index.tz_localize(None)
    d.index = d.index.normalize()
    out = pd.DataFrame({"open": d["Open"], "high": d["High"], "low": d["Low"], "close": d["Close"]})
    out["prev_close"] = out["close"].shift(1)
    out["ret"] = (out["close"] / out["open"] - 1) * 100
    out["gap"] = (out["open"] / out["prev_close"] - 1) * 100
    return out.dropna()


def study_stock_week(stocks: dict[str, pd.DataFrame], nikkei: pd.DataFrame) -> dict:
    """銘柄自身の週足・月足と逆に入る（金曜・月末）。その週の値幅が小さい週、日経と向きが逆の週も分ける"""
    from idea_study import study_closing, _period_reversal
    out = {}
    nk_week = np.sign(nikkei["prev_close"] - nikkei.groupby(nikkei.index.to_period("W"))["open"].transform("first"))
    for name, d in stocks.items():
        r = study_closing(d)
        week = d.index.to_period("W")
        fri = pd.Series((d.index.weekday == 4), index=d.index)
        wk_range = d.groupby(week)["high"].transform("max") - d.groupby(week)["low"].transform("min")
        weekly = wk_range.groupby(week).last()
        avg10 = weekly.rolling(10).mean().shift(1)
        small = pd.Series(week.map(lambda w: weekly.get(w, np.nan) < avg10.get(w, np.nan)), index=d.index)
        own = np.sign(d["prev_close"] - d.groupby(week)["open"].transform("first"))
        mixed = pd.Series(own.values != nk_week.reindex(d.index).values, index=d.index)
        r["small_week"] = _period_reversal(d, fri & small, week)["all"]
        r["vs_nikkei_mixed"] = _period_reversal(d, fri & mixed, week)["all"]
        out[name] = r
    return out


def study_month_bar(stocks: dict[str, pd.DataFrame]) -> dict:
    """先月の月足が陽線か陰線か（確定した足だけ）と、今月の各日の寄り→引け"""
    out = {}
    for name, d in stocks.items():
        month = d.index.to_period("M")
        bar = d.groupby(month).agg(o=("open", "first"), c=("close", "last"))
        up = (bar["c"] > bar["o"]).shift(1)
        prev_up = pd.Series(month.map(lambda m: up.get(m, np.nan)), index=d.index)
        res = {}
        for lab, val in (("先月陽線", True), ("先月陰線", False)):
            r = d["ret"][prev_up == val]
            res[lab] = {"n": len(r), "up": float((r > 0).mean()) if len(r) else None,
                        "mean": float(r.mean()) if len(r) else None, "p_mean": mean_p(r.values)}
        out[name] = res
    return out


def study_prev_line(m5: dict[str, pd.DataFrame], daily: dict[str, pd.DataFrame]) -> dict:
    """寄りが前日終値より上の日、9時台に前日終値を割ったら、10:00 に前日終値より上へ戻っていたか"""
    out = {}
    for name, h in m5.items():
        d = daily.get(name)
        if d is None:
            continue
        rows = []
        for day, g in h.groupby(h.index.normalize()):
            if day not in d.index:
                continue
            pc = float(d.loc[day, "prev_close"])
            hm = g.index.strftime("%H:%M")
            nine = g[hm < "10:00"]
            at10 = g[hm >= "10:00"]
            if nine.empty or at10.empty or float(nine["Open"].iloc[0]) <= pc:
                continue
            if float(nine["Low"].min()) < pc:
                rows.append(float(at10["Open"].iloc[0]) > pc)
        out[name] = _rate(pd.Series(rows, dtype=bool))
    return out


def dividend_events(d: pd.DataFrame, divs: pd.Series) -> pd.DataFrame:
    """権利落ち日ごとの 配当 ÷ 前日終値 と、寄りの窓、寄り→引け（純粋関数）"""
    rows = []
    for t, amt in divs.items():
        t = pd.Timestamp(t)
        t = (t.tz_localize(None) if t.tz is not None else t).normalize()
        if t not in d.index or amt <= 0:
            continue
        r = d.loc[t]
        yld = amt / r["prev_close"] * 100
        rows.append({"date": t, "div_pct": yld, "gap": r["gap"], "ret": r["ret"],
                     "drop_ratio": -r["gap"] / yld if yld else np.nan})
    return pd.DataFrame(rows)


def study_dividend(events: pd.DataFrame) -> dict:
    if events.empty:
        return {"n": 0}
    small = events["drop_ratio"] < 1.0       # 配当ほど下がらずに寄った
    out = {"n": len(events)}
    for lab, m in (("配当ほど下がらず寄った", small), ("配当以上に下がって寄った", ~small)):
        r = events["ret"][m]
        out[lab] = {"n": int(m.sum()), "down": float((r < 0).mean()) if len(r) else None,
                    "mean": float(r.mean()) if len(r) else None}
    return out


def round_step(price: float) -> float:
    return 1000.0 if price < 10000 else 5000.0


def touch_days(d: pd.DataFrame) -> pd.Series:
    """前日終値の下にあるキリ（1,000円・1万円以上は5,000円）に、上から下りてきて触れた日（純粋関数）"""
    out = []
    for r in d.itertuples():
        step = round_step(r.prev_close)
        level = np.floor(r.prev_close / step) * step
        out.append(bool(r.low <= level < r.prev_close and r.open > level))
    return pd.Series(out, index=d.index)


def study_big_caps(caps: dict[str, pd.DataFrame], nikkei: pd.DataFrame) -> dict:
    touched = pd.concat({n: touch_days(d) for n, d in caps.items()}, axis=1).reindex(nikkei.index).fillna(False)
    cnt = touched.sum(axis=1)
    out = {}
    for lab, m in (("2銘柄以上が触れた日", cnt >= 2), ("1銘柄", cnt == 1), ("なし", cnt == 0)):
        r = nikkei["ret"][m]
        # 触れた日は寄りより下で触れているので、「触れた後に反発したか」は引けが安値からどれだけ戻したかで見る
        rebound = ((nikkei["close"] - nikkei["low"]) / (nikkei["high"] - nikkei["low"]).replace(0, np.nan))[m]
        out[lab] = {"n": int(m.sum()), "up": float((r > 0).mean()) if len(r) else None,
                    "mean": float(r.mean()) if len(r) else None, "rebound": float(rebound.mean()) if len(r) else None,
                    "p_mean": mean_p(r.values)}
    return out


def study_index_dev(us: dict[str, pd.Series], nikkei: pd.DataFrame, pts: pd.DataFrame) -> dict:
    """ナスダックかSOXが -3σ 以下（+3σ 以上）の翌朝の日経：寄り→引け（日足）と、前場・後場（1時間足）"""
    zs = pd.concat({n: dev_sigma(_norm(s))["z"] for n, s in us.items()}, axis=1)
    before = nikkei.index - pd.Timedelta(days=1)
    z = zs.reindex(before, method="ffill")
    z.index = nikkei.index
    out = {}
    for lab, m in (("-3σ以下", (z <= -3).any(axis=1)), ("-2σ〜-3σ", ((z <= -2) & (z > -3)).any(axis=1) & ~(z <= -3).any(axis=1)),
                   ("+3σ以上", (z >= 3).any(axis=1))):
        r = nikkei["ret"][m]
        p = pts.reindex(nikkei.index[m]).dropna(subset=["open", "am_close", "pm_open"])
        am = (p["am_close"] / p["open"] - 1) * 100
        pm = (p["close"] / p["pm_open"] - 1) * 100
        out[lab] = {"n": int(m.sum()), "up": float((r > 0).mean()) if len(r) else None,
                    "mean": float(r.mean()) if len(r) else None,
                    "n_hourly": len(p), "am_mean": float(am.mean()) if len(p) else None,
                    "pm_mean": float(pm.mean()) if len(p) else None}
    return out


def study_hands_off(df: pd.DataFrame, pts: dict[str, pd.DataFrame],
                    stock_gaps: dict[str, pd.Series] | None = None) -> dict:
    """手出し無用の日：窓と逆に入る（寄り→10:00）。9〜10時と10時以降が同じ向きだった日が直近3日で2日以上なら、今日も同じか"""
    out = {}
    ho = df.index[df["side_v1"] == 0]
    for name, p in pts.items():
        p = p.dropna(subset=["open", "p10", "close"])
        gap = df["gap"] if name == "日経平均" else (stock_gaps or {}).get(name)
        if gap is None:
            continue
        d = p.join(gap.rename("g"), how="inner")
        r10 = (d["p10"] / d["open"] - 1) * 100
        side = -np.sign(d["g"].where(d["g"].abs() >= 0.2))
        m = d.index.isin(ho)
        out["gap_fade" if name == "日経平均" else f"gap_fade:{name}"] = signed_summary(r10[m], side[m])
    for name, p in pts.items():
        p = p.dropna(subset=["open", "p10", "close"])
        same = np.sign(p["p10"] - p["open"]) == np.sign(p["close"] - p["p10"])
        recent = same.astype(float).rolling(3).sum().shift(1)
        res = {}
        for lab, m in (("直近3日で2日以上つづいた", recent >= 2), ("直近3日で1日以下", recent <= 1)):
            res[lab] = _rate(same[m])
        out[f"pattern:{name}"] = res
    return out


# ========== 実行 ==========

def _download(yf, tickers: list[str], **kw) -> dict[str, pd.DataFrame]:
    raw = yf.download(" ".join(tickers), progress=False, group_by="ticker", auto_adjust=True, threads=True, **kw)
    out = {}
    for t in tickers:
        try:
            if isinstance(raw.columns, pd.MultiIndex) and t in raw.columns.get_level_values(0):
                x = raw[t]
            elif isinstance(raw.columns, pd.MultiIndex):
                x = raw.xs(t, axis=1, level=-1)
            else:
                x = raw
            x = x.dropna(subset=["Open", "Close"])
        except KeyError:
            continue
        if not x.empty:
            out[t] = x
    return out


def _to_jst(x: pd.DataFrame) -> pd.DataFrame:
    if getattr(x.index, "tz", None) is not None:
        x = x.tz_convert("Asia/Tokyo").tz_localize(None)
    return x


def run_part2(df: pd.DataFrame, ohlc: dict, stk_t: dict[str, str], hourly: dict[str, pd.DataFrame], start: str) -> dict:
    """idea_study.run_idea_study から呼ぶ。失敗した項目は飛ばす"""
    import yfinance as yf

    res: dict = {}
    pts = {name: day_points(h) for name, h in hourly.items() if h is not None and not h.empty}
    nk_pts = pts.get("日経平均", pd.DataFrame())
    nikkei = stock_day_frame(ohlc["^N225"])
    stocks = {n: stock_day_frame(ohlc[t]) for t, n in stk_t.items()}

    def step(key, fn):
        try:
            res[key] = fn()
        except Exception as e:
            print(f"  ⚠️ {key} の検証に失敗: {e}")

    ok = df[~df["us_closed"]]
    step("threshold", lambda: study_threshold(ok))
    eu = _download(yf, ["^GDAXI", "^FTSE"], start=start, interval="1d")
    step("europe", lambda: study_europe(ok, {t: _norm(x["Close"]).pct_change() * 100 for t, x in eu.items()}))
    if not nk_pts.empty:
        step("asym", lambda: study_asym(ok, nk_pts))
        step("jump", lambda: study_jump(df, nk_pts))
        step("hands_off", lambda: study_hands_off(ok, pts, {n: d["gap"] for n, d in stocks.items()}))
        step("jump_stocks", lambda: {n: study_jump(ok.assign(gap=stocks[n]["gap"].reindex(ok.index)), p)
                                     for n, p in pts.items() if n in stocks})
    es = _download(yf, ["ES=F"], period="730d", interval="1h").get("ES=F")
    if es is not None and pts:
        step("us_fut", lambda: study_us_futures(us_futures_move(es, nk_pts.index if not nk_pts.empty else df.index), pts))
    m30 = {n: _to_jst(x) for t, x in _download(yf, ["^N225"] + list(stk_t), period="60d", interval="30m").items()
           for n in [("日経平均" if t == "^N225" else stk_t[t])]}
    step("cp1030", lambda: study_cp1030(m30))
    step("stock_week", lambda: study_stock_week(stocks, nikkei))
    step("month_bar", lambda: study_month_bar(stocks))
    m5 = {stk_t[t]: _to_jst(x) for t, x in _download(yf, list(stk_t), period="60d", interval="5m").items()}
    step("prev_line", lambda: study_prev_line(m5, stocks))

    def _div():
        ev = []
        for t, n in stk_t.items():
            dv = yf.Ticker(t).dividends
            e = dividend_events(stocks[n], dv)
            if not e.empty:
                ev.append(e.assign(name=n))
        return study_dividend(pd.concat(ev) if ev else pd.DataFrame())
    step("dividend", _div)
    caps = {BIG_CAPS[t]: stock_day_frame(x) for t, x in _download(yf, list(BIG_CAPS), start=start, interval="1d").items()}
    step("big_caps", lambda: study_big_caps(caps, nikkei))
    step("index_dev", lambda: study_index_dev({"ナスダック": ohlc["^IXIC"]["Close"], "SOX": ohlc["^SOX"]["Close"]},
                                              nikkei, nk_pts))
    _print2(res)
    return res


def collect_pvalues2(res: dict) -> list[tuple[str, float]]:
    ps = []
    for k, s in (res.get("threshold") or {}).items():
        ps.append((f"しきい値:{k}", s["p_hit"]))
    for k in ("agree", "against"):
        if k in (res.get("europe") or {}):
            ps.append((f"欧州:{k}", res["europe"][k]["p_mean"]))
    for k, s in (res.get("asym") or {}).items():
        ps.append((f"非対称:{k}", s["p_mean"]))
    for k, s in (res.get("jump") or {}).items():
        ps.append((f"先物ジャンプ:{k}", s["p_mean"]))
    for k, s in (res.get("us_fut") or {}).items():
        ps.append((f"米先物:{k}", s["p_mean"]))
    if "cp1030" in res:
        ps.append(("10:30節目", res["cp1030"]["p_vs_others"]))
    for n, r in (res.get("stock_week") or {}).items():
        for kind in ("friday", "month_end"):
            ps.append((f"銘柄の手仕舞い:{n}:{kind}", r[kind]["all"]["p_mean"]))
    for n, r in (res.get("month_bar") or {}).items():
        for lab, s in r.items():
            ps.append((f"月足:{n}:{lab}", s["p_mean"]))
    for n, s in (res.get("prev_line") or {}).items():
        ps.append((f"前日終値ライン:{n}", s["p"]))
    # 値嵩株のキリは「その日の安値で触れたか」で分けるので、下げた日ほど触れやすい（結果を先に見ている）。補正には入れない
    for n, r in (res.get("jump_stocks") or {}).items():
        for k, s in r.items():
            ps.append((f"先物ジャンプ:{n}:{k}", s["p_mean"]))
    for n, r in (res.get("stock_week") or {}).items():
        ps.append((f"銘柄の手仕舞い:{n}:値幅小の週", r["small_week"]["p_mean"]))
    ho = res.get("hands_off") or {}
    for k, s in ho.items():
        if k.startswith("gap_fade"):
            ps.append((f"手出し無用:窓と逆{k[8:]}", s["p_mean"]))
    for k, r in ho.items():
        if k.startswith("pattern:"):
            for lab, s in r.items():
                ps.append((f"型の持続:{k[8:]}:{lab}", s["p"]))
    return ps


def _pc(x):
    return "—" if x is None else f"{x * 100:.1f}%"


def _sg(x):
    return "—" if x is None else f"{x:+.3f}"


def _print2(res: dict) -> None:
    for k, s in (res.get("threshold") or {}).items():
        print(f"  しきい値±{k}%: 方向の日 {s['n']} 的中 {_pc(s['hit'])} 平均 {_sg(s['mean'])} 手出し無用 {_pc(s['hands_off_share'])}")
    for k, s in (res.get("europe") or {}).items():
        print(f"  欧州 {k}: n={s['n']} 的中 {_pc(s['hit'])} 平均 {_sg(s['mean'])}")
    for k, s in (res.get("asym") or {}).items():
        r9, r10 = s.get("reach_9h") or {}, s.get("reach_10h") or {}
        print(f"  非対称 {k}: n={s['n']} 的中 {_pc(s['hit'])} 平均 {_sg(s['mean'])} 0.5%到達 9時台 {_pc(r9.get('rate'))}({r9.get('n')}) 10時台まで {_pc(r10.get('rate'))}")
    for k, s in (res.get("jump") or {}).items():
        print(f"  先物ジャンプ {k}: n={s['n']} 寄り→10:00 逆に動いた {_pc(s['hit'])} 平均 {_sg(s['mean'])}")
    for k, s in (res.get("us_fut") or {}).items():
        print(f"  米先物 {k}: n={s['n']} 同じ向き {_pc(s['hit'])} 平均 {_sg(s['mean'])}")
    cp = res.get("cp1030")
    if cp:
        print("  節目で向きが変わった割合: " + "  ".join(f"{k} {_pc(v['rate'])}({v['n']})" for k, v in cp.items() if k != "p_vs_others")
              + f"  10:30 vs 他 p={cp['p_vs_others']:.3f}")
    for n, r in (res.get("stock_week") or {}).items():
        print(f"  銘柄の手仕舞い {n}: 金曜 {_pc(r['friday']['all']['hit'])}({r['friday']['all']['n']}) 月末 {_pc(r['month_end']['all']['hit'])}({r['month_end']['all']['n']})"
              f" 値幅小の週 {_pc(r['small_week']['hit'])}({r['small_week']['n']}) 日経と逆の週 {_pc(r['vs_nikkei_mixed']['hit'])}({r['vs_nikkei_mixed']['n']})")
    for n, r in (res.get("month_bar") or {}).items():
        print(f"  月足 {n}: " + "  ".join(f"{k} 上げ {_pc(s['up'])} 平均 {_sg(s['mean'])}({s['n']})" for k, s in r.items()))
    for n, s in (res.get("prev_line") or {}).items():
        print(f"  前日終値ライン {n}: 割った後10:00に戻した {_pc(s['rate'])}({s['n']})")
    dv = res.get("dividend") or {}
    if dv.get("n"):
        print(f"  配当落ち: {dv['n']}回 " + "  ".join(f"{k} 下げ {_pc(v['down'])} 平均 {_sg(v['mean'])}({v['n']})" for k, v in dv.items() if k != "n"))
    for k, s in (res.get("big_caps") or {}).items():
        print(f"  値嵩株キリ {k}: n={s['n']} 上げ {_pc(s['up'])} 平均 {_sg(s['mean'])} 安値からの戻り {_pc(s['rebound'])}")
    for k, s in (res.get("index_dev") or {}).items():
        print(f"  指数乖離 {k}: n={s['n']} 上げ {_pc(s['up'])} 平均 {_sg(s['mean'])} 前場 {_sg(s['am_mean'])} 後場 {_sg(s['pm_mean'])}（{s['n_hourly']}日）")
    for n, r in (res.get("jump_stocks") or {}).items():
        print(f"  先物ジャンプ（銘柄の窓） {n}: " + "  ".join(f"{k} n={s['n']} 逆 {_pc(s['hit'])} 平均 {_sg(s['mean'])}" for k, s in r.items()))
    ho = res.get("hands_off") or {}
    for k, g in ho.items():
        if k.startswith("gap_fade"):
            print(f"  手出し無用・窓と逆{k[8:]}: n={g['n']} 的中 {_pc(g['hit'])} 平均 {_sg(g['mean'])}")
    for k, r in ho.items():
        if k.startswith("pattern:"):
            print(f"  型の持続 {k[8:]}: " + "  ".join(f"{lab} {_pc(s['rate'])}({s['n']})" for lab, s in r.items()))
