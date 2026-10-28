"""
承認銘柄（フジクラ・SBG）の個別予測の検証

米国関連株の前日騰落率が、翌日の日本株の
  ① 窓（前日終値 → 寄り付き）
  ② 寄り後（寄り付き → 引け）  ← デイトレで実際に取れる部分
にどれだけ効いているかを分けて検証する。

日付のアライメント:
  日本のT日には、T-1日以前で最新の米国営業日の騰落率を使う（朝の時点で見える情報）。
  ただし、その米国営業日が日本の前営業日より前なら（米国休場など）、
  その情報は前日の日本市場ですでに織り込まれているため除外する。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from scipy import stats

from focus_stocks import FOCUS_STOCKS, FocusStock, classify_link

BIG_MOVE_PCT = 1.0   # 米関連株の平均が ±1%以上の日を「大きく動いた日」とする
MIN_SAMPLES = 20

LINK_LABELS = {
    "confirmed": "✅ 連動を確認",
    "weak": "△ 弱い連動",
    "none": "✗ 連動なし",
    "reverse": "⚠️ 逆に動きやすい",
    "unknown": "データ不足",
}


def _norm(s: pd.Series) -> pd.Series:
    s = s.dropna().copy()
    idx = pd.to_datetime(s.index)
    if getattr(idx, "tz", None) is not None:
        idx = idx.tz_localize(None)
    s.index = idx.normalize()
    return s.sort_index()


def align_focus_returns(us_ret: pd.Series, jp_open: pd.Series, jp_close: pd.Series) -> pd.DataFrame:
    """日本の各営業日Tについて、米の前日騰落率・窓・寄り後をそろえる。

    Returns:
        DataFrame index=T, columns=[us, gap, intraday]（すべて%）
    """
    us = _norm(us_ret)
    jo = _norm(jp_open)
    jc = _norm(jp_close)
    dates = sorted(set(jo.index) & set(jc.index))
    rows = []
    for i in range(1, len(dates)):
        t, t_prev = dates[i], dates[i - 1]
        prior = us.loc[: t - pd.Timedelta(days=1)]
        if prior.empty:
            continue
        us_date = prior.index[-1]
        if us_date < t_prev:      # 米国休場などで新しい情報がない日
            continue
        prev_close, o, c = jc[t_prev], jo[t], jc[t]
        if not (prev_close > 0 and o > 0):
            continue
        rows.append({
            "date": t,
            "us": float(prior.iloc[-1]),
            "gap": (o / prev_close - 1) * 100,
            "intraday": (c / o - 1) * 100,
        })
    return pd.DataFrame(rows).set_index("date") if rows else pd.DataFrame(columns=["us", "gap", "intraday"])


def _corr(x: pd.Series, y: pd.Series) -> tuple[float | None, float | None]:
    if len(x) < 3 or x.std() == 0 or y.std() == 0:
        return None, None
    r, p = stats.pearsonr(x.values, y.values)
    return float(r), float(p)


def _bucket(df: pd.DataFrame, mask: pd.Series, up: bool) -> dict:
    sub = df[mask]
    n = len(sub)
    if n == 0:
        return {"n": 0}
    sign = 1 if up else -1
    return {
        "n": n,
        "mean_gap": float(sub["gap"].mean()),
        "mean_intraday": float(sub["intraday"].mean()),
        "gap_hit": float(((sub["gap"] * sign) > 0).mean()),         # 窓が米と同方向に開いた率
        "intraday_hit": float(((sub["intraday"] * sign) > 0).mean()),  # 寄り後も同方向に動いた率
    }


def compute_focus_stats(df: pd.DataFrame) -> dict:
    """そろえたデータから相関と条件付き成績を計算する（純粋関数）。"""
    n = len(df)
    if n < MIN_SAMPLES:
        return {"n": n, "gap_r": None, "gap_p": None, "intraday_r": None, "intraday_p": None,
                "gap_link": "unknown", "intraday_link": "unknown",
                "us_up": {"n": 0}, "us_down": {"n": 0}}
    gap_r, gap_p = _corr(df["us"], df["gap"])
    in_r, in_p = _corr(df["us"], df["intraday"])
    return {
        "n": n,
        "gap_r": gap_r, "gap_p": gap_p,
        "intraday_r": in_r, "intraday_p": in_p,
        "gap_link": classify_link(gap_r, gap_p),
        "intraday_link": classify_link(in_r, in_p),
        "us_up": _bucket(df, df["us"] >= BIG_MOVE_PCT, up=True),
        "us_down": _bucket(df, df["us"] <= -BIG_MOVE_PCT, up=False),
    }


def _fetch_ohlc(tickers: list[str], period: str) -> pd.DataFrame:
    import yfinance as yf
    return yf.download(
        tickers=" ".join(tickers), period=period, interval="1d",
        progress=False, group_by="ticker", auto_adjust=True, threads=True,
    )


def validate_focus_stock(stock: FocusStock, data: pd.DataFrame) -> dict:
    """1銘柄を検証する。dataは yfinance の group_by='ticker' 形式。"""
    proxy_closes = {}
    for t in stock.us_proxies:
        try:
            proxy_closes[t] = data[t]["Close"]
        except KeyError:
            continue
    try:
        jp_open = data[stock.ticker]["Open"]
        jp_close = data[stock.ticker]["Close"]
    except KeyError:
        jp_open = jp_close = pd.Series(dtype=float)

    base = {"code": stock.code, "name": stock.name, "us_label": stock.us_label,
            "us_tickers_used": sorted(proxy_closes)}

    if not proxy_closes or jp_open.empty:
        stats_ = compute_focus_stats(pd.DataFrame(columns=["us", "gap", "intraday"]))
        return {**base, **stats_, "per_proxy": {},
                "gap_label": LINK_LABELS["unknown"], "intraday_label": LINK_LABELS["unknown"]}

    rets = pd.DataFrame({t: _norm(c).pct_change() * 100 for t, c in proxy_closes.items()})
    us_avg = rets.mean(axis=1, skipna=True)

    df = align_focus_returns(us_avg, jp_open, jp_close)
    result = compute_focus_stats(df)

    # 参考: 各ティッカー単独での窓・寄り後の相関（どれが一番効いているか）
    per_proxy = {}
    for t in rets.columns:
        d = align_focus_returns(rets[t], jp_open, jp_close)
        if len(d) >= MIN_SAMPLES:
            gr, _ = _corr(d["us"], d["gap"])
            ir, _ = _corr(d["us"], d["intraday"])
            per_proxy[t] = {"gap_r": gr, "intraday_r": ir, "n": len(d)}

    return {
        **base, **result, "per_proxy": per_proxy,
        "gap_label": LINK_LABELS[result["gap_link"]],
        "intraday_label": LINK_LABELS[result["intraday_link"]],
    }


def validate_all_focus_stocks(period: str = "1y") -> list[dict]:
    tickers: set[str] = set()
    for s in FOCUS_STOCKS:
        tickers.add(s.ticker)
        tickers.update(s.us_proxies)
    print(f"Fetching {len(tickers)} tickers (OHLC) for focus stocks, {period}...")
    data = _fetch_ohlc(sorted(tickers), period)

    results = []
    for s in FOCUS_STOCKS:
        r = validate_focus_stock(s, data)
        results.append(r)
        fmt = lambda v: "  -  " if v is None else f"{v:+.3f}"
        print(f"  {s.name:8s} n={r['n']:3d} 窓 r={fmt(r['gap_r'])} {r['gap_label']} / "
              f"寄り後 r={fmt(r['intraday_r'])} {r['intraday_label']}")
    return results
