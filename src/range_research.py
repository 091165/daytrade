"""
値幅の目安を良くできるかの検証（研究用・作業ブランチで一度だけ動かす）

① 1日の値幅（2年・日足）
   目安 = 直近10日の平均値幅（今のカード）
   追加候補（寄り前にわかる）: 米関連株の前日の動きの大きさ |us|、決算発表の翌営業日
   追加候補（寄り付きでわかる）: 窓の大きさ |gap|
   前半で係数を決め、後半で 相関 r と 平均の外れ幅（MAE）を比べる

② 9時台の値幅（5分足・直近60日）
   目安 = 直近10日の1日の平均値幅 × 係数
   追加候補: |gap|、|us|
   銘柄ごとに目安で割って全銘柄をまとめ、前半30日で係数を決め、後半30日で比べる
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from expected_range import daily_range_pct
from focus_backtest import _corr, _norm
from focus_stocks import FOCUS_STOCKS
from stock_screen import EXTRA_JP
from correlation_scan import SCAN_JP

UNIVERSE = SCAN_JP + EXTRA_JP
PROXIES = {s.code: s.us_proxies for s in FOCUS_STOCKS}
DEFAULT_PROXIES = ("SOXX", "QQQ")


def us_abs_move(us_close: dict[str, pd.Series], proxies, jp_index: pd.DatetimeIndex) -> pd.Series:
    """日本の各営業日に、前日の米関連株の平均騰落率の大きさ（%）をそろえる。"""
    rets = [us_close[t].pct_change() * 100 for t in proxies if t in us_close]
    if not rets:
        return pd.Series(index=jp_index, dtype=float)
    avg = pd.concat(rets, axis=1).mean(axis=1).dropna()
    pos = np.searchsorted(avg.index.values, (jp_index - pd.Timedelta(days=1)).values, side="right") - 1
    vals = np.where(pos >= 0, avg.values[np.clip(pos, 0, None)], np.nan)
    return pd.Series(np.abs(vals), index=jp_index)


def earnings_next_day(ticker: str, index: pd.DatetimeIndex) -> pd.Series:
    """決算発表の翌営業日（引け後発表 → 翌日が反応日）なら True。取れなければ全部 False。"""
    import yfinance as yf
    flag = pd.Series(False, index=index)
    try:
        ed = yf.Ticker(ticker).get_earnings_dates(limit=16)
    except Exception:
        return flag
    if ed is None or len(ed) == 0:
        return flag
    for ts in ed.index:
        t = pd.Timestamp(ts).tz_localize(None) if pd.Timestamp(ts).tzinfo else pd.Timestamp(ts)
        later = index[index > t.normalize()] if t.hour >= 15 or t.hour == 0 else index[index >= t.normalize()]
        if len(later):
            flag[later[0]] = True
    return flag


def fit_eval(train: pd.DataFrame, test: pd.DataFrame, cols: list[str], target: str = "y") -> dict:
    X = np.column_stack([np.ones(len(train))] + [train[c] for c in cols])
    coef, *_ = np.linalg.lstsq(X, train[target].values, rcond=None)
    Xt = np.column_stack([np.ones(len(test))] + [test[c] for c in cols])
    pred = Xt @ coef
    actual = test[target].values
    r, _ = _corr(pd.Series(pred), pd.Series(actual))
    return {"r": r, "mae": float(np.mean(np.abs(np.exp(pred) - np.exp(actual)))), "coef": coef.round(3).tolist()}


def daily_study(data, us_close) -> None:
    print("=== ① 1日の値幅（後半の r ／ 平均の外れ幅%）: 目安 → +|us| → +決算 → +|gap| ===")
    pooled = []
    for code, t, name in UNIVERSE:
        try:
            ohlc = data[t][["Open", "High", "Low", "Close"]].dropna()
        except KeyError:
            continue
        rng = daily_range_pct(ohlc)
        o, c = _norm(ohlc["Open"]), _norm(ohlc["Close"])
        df = pd.DataFrame({"range": rng, "avg10": rng.rolling(10).mean().shift(1),
                           "gap": ((o / c.shift(1) - 1) * 100).abs()}).dropna()
        df = df[(df["range"] > 0) & (df["avg10"] > 0)]
        df["us"] = us_abs_move(us_close, PROXIES.get(code, DEFAULT_PROXIES), df.index)
        df["earn"] = earnings_next_day(t, df.index).astype(float)
        df = df.dropna()
        if len(df) < 200:
            continue
        # 対数で比べる（値幅は右に裾が長いので）
        df["y"], df["la"] = np.log(df["range"]), np.log(df["avg10"])
        df["lg"], df["lu"] = np.log1p(df["gap"]), np.log1p(df["us"])
        split = df.index[len(df) // 2]
        a, b = df[df.index < split], df[df.index >= split]
        res = [fit_eval(a, b, cols) for cols in (["la"], ["la", "lu"], ["la", "lu", "earn"], ["la", "lu", "earn", "lg"])]
        e = b[b["earn"] > 0]
        er = (e["range"] / e["avg10"]).mean() if len(e) else float("nan")
        print(f"  {name:10s} " + "  ".join(f"{x['r']:+.2f}/{x['mae']:.2f}" for x in res)
              + f"  決算翌日{int(df['earn'].sum())}回 値幅は目安の{er:.1f}倍")
        pooled.append((name, res, df["earn"].sum()))
    if pooled:
        m = np.array([[x["r"] for x in res] for _, res, _ in pooled])
        print("  平均 r: " + "  ".join(f"{v:+.3f}" for v in m.mean(axis=0)))


def morning_study(us_close) -> None:
    import yfinance as yf
    print("=== ② 9時台（9:00-10:00）の値幅（5分足・直近60日、全銘柄まとめ） ===")
    tickers = [t for _, t, _ in UNIVERSE]
    m5 = yf.download(" ".join(tickers), period="60d", interval="5m", progress=False,
                     group_by="ticker", auto_adjust=True, threads=True)
    d1 = yf.download(" ".join(tickers), period="6mo", interval="1d", progress=False,
                     group_by="ticker", auto_adjust=True, threads=True)
    rows = []
    for code, t, name in UNIVERSE:
        try:
            x = m5[t].dropna()
            daily = d1[t].dropna()
        except KeyError:
            continue
        if x.empty or daily.empty:
            continue
        x.index = x.index.tz_convert("Asia/Tokyo")
        rng = daily_range_pct(daily)
        avg10 = rng.rolling(10).mean().shift(1)
        close = _norm(daily["Close"])
        for day, g in x.groupby(x.index.date):
            a = g.between_time("09:00", "09:55")
            ts = pd.Timestamp(day)
            if len(a) < 8 or ts not in avg10.index or pd.isna(avg10.get(ts)):
                continue
            prev = close[close.index < ts]
            if prev.empty:
                continue
            pc = prev.iloc[-1]
            o = a["Open"].iloc[0]
            first5 = abs(a["Close"].iloc[0] - o) / pc * 100           # 9:00-9:05の動き（寄り後すぐわかる）
            rows.append({"code": code, "date": ts, "morning": (a["High"].max() - a["Low"].min()) / pc * 100,
                         "avg10": avg10[ts], "gap": abs(o / pc - 1) * 100, "first5": first5,
                         "full": rng.get(ts, np.nan)})
    df = pd.DataFrame(rows).dropna(subset=["morning", "avg10"])
    if df.empty:
        print("  データなし")
        return
    df["us"] = np.nan
    for code in df["code"].unique():
        m = df["code"] == code
        df.loc[m, "us"] = us_abs_move(us_close, PROXIES.get(code, DEFAULT_PROXIES), pd.DatetimeIndex(df.loc[m, "date"])).values
    df = df.dropna(subset=["us"])
    share = (df["morning"] / df["full"]).median()
    print(f"  {df['code'].nunique()}銘柄 × {df['date'].nunique()}日 = {len(df)}件 ／ 9時台の値幅は1日の値幅の約{share * 100:.0f}%")
    df["y"], df["la"] = np.log(df["morning"]), np.log(df["avg10"])
    df["lg"], df["lu"], df["l5"] = np.log1p(df["gap"]), np.log1p(df["us"]), np.log1p(df["first5"])
    dates = np.sort(df["date"].unique())
    split = dates[len(dates) // 2]
    a, b = df[df["date"] < split], df[df["date"] >= split]
    for label, cols in (("目安だけ", ["la"]), ("+|us|", ["la", "lu"]), ("+|gap|（寄り付きで判明）", ["la", "lu", "lg"]),
                        ("+9:00-9:05の動き", ["la", "lu", "lg", "l5"])):
        res = fit_eval(a, b, cols)
        print(f"  {label:24s} r={res['r']:+.3f} 平均の外れ幅 {res['mae']:.2f}%  係数{res['coef']}")
    # 窓の大きさ別の、9時台の値幅 ÷ 目安
    b = b.assign(ratio=b["morning"] / b["avg10"], gq=pd.qcut(b["gap"] / b["avg10"], 4, labels=["小", "中", "大", "特大"]))
    print("  窓の大きさ（目安に対する比）別の 9時台の値幅÷1日の目安: " +
          "  ".join(f"{k} {v:.2f}" for k, v in b.groupby("gq", observed=True)["ratio"].median().items()))


def run() -> None:
    import yfinance as yf
    jp = [t for _, t, _ in UNIVERSE]
    us = sorted({t for p in PROXIES.values() for t in p} | set(DEFAULT_PROXIES))
    data = yf.download(" ".join(jp + us), period="2y", interval="1d", progress=False,
                       group_by="ticker", auto_adjust=True, threads=True)
    us_close = {}
    for t in us:
        try:
            us_close[t] = _norm(data[t]["Close"])
        except KeyError:
            pass
    daily_study(data, us_close)
    morning_study(us_close)


if __name__ == "__main__":
    run()
