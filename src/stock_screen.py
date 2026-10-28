"""
売買代金上位の銘柄スクリーニング（研究用・週次バックテストのみ）

目的:
  承認銘柄・試用銘柄の候補を探すため、売買代金上位の銘柄について
    ① 寄り後（寄り → 引け）の方向を、寄り前にわかる情報で当てられるか
    ② 今日の値幅を、直近の値幅から見積もれるか
  を同じ物差しで比べる。毎朝のダッシュボードには出さない。

寄り後の方向の手がかり（どれも寄り付き時点でわかる）:
  - us:   米国株の前日騰落率（銘柄ごとに、前半の期間で一番連動した米国株を選ぶ）
  - gap:  その日の窓（前日終値 → 寄り）。窓埋め（逆）か、窓の方向に続く（順）か
  - prev: 前日の寄り後の動き。続く（順）か、戻す（逆）か

過剰な当てはめを避ける検証:
  期間を前半・後半に分け、前半で「どの手がかりを、どちら向きに使うか」を決め、
  後半だけで当たったかを測る（前半で選んだ米国株は後半では選び直さない）。
  後半のp値は全銘柄×全手がかりで Benjamini-Hochberg 法の補正をかけ、
  q < 0.05 かつ前半と同じ向き のものだけを「信頼できる」とする。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from correlation_scan import SCAN_JP, SCAN_US, Q_THRESHOLD, bh_qvalues
from expected_range import RANGE_WINDOW, daily_range_pct, validate_range_prediction
from focus_backtest import _corr, _norm, align_focus_returns

MIN_HALF = 60            # 前半・後半それぞれに必要な日数

# 売買代金上位（2026/10/28 のランキング11〜20位）。SCAN_JP と合わせて調べる
EXTRA_JP: list[tuple[str, str, str]] = [
    ("6146", "6146.T", "ディスコ"),
    ("8306", "8306.T", "三菱UFJ"),
    ("6758", "6758.T", "ソニーG"),
    ("4063", "4063.T", "信越化学"),
    ("6098", "6098.T", "リクルートHD"),
    ("8766", "8766.T", "東京海上"),
    ("6723", "6723.T", "ルネサス"),
    ("6861", "6861.T", "キーエンス"),
    ("5801", "5801.T", "古河電工"),
]
SCREEN_JP: list[tuple[str, str, str]] = SCAN_JP + EXTRA_JP
# 金融株（三菱UFJ・東京海上）向けに米国の金融株ETFを足す
SCREEN_US: list[tuple[str, str]] = SCAN_US + [("XLF", "米金融株ETF")]

PREDICTOR_LABELS = {
    "us": "米国株の前日の動き",
    "gap": "その日の窓",
    "prev": "前日の寄り後",
}


def daily_moves(ohlc: pd.DataFrame) -> pd.DataFrame:
    """日足から 窓・寄り後・前日の寄り後 を%でそろえる（index=日付）。"""
    o = _norm(ohlc["Open"])
    c = _norm(ohlc["Close"])
    df = pd.DataFrame({"open": o, "close": c}).dropna()
    df = df[(df["open"] > 0) & (df["close"] > 0)]
    df["gap"] = (df["open"] / df["close"].shift(1) - 1) * 100
    df["intraday"] = (df["close"] / df["open"] - 1) * 100
    df["prev"] = df["intraday"].shift(1)
    return df[["gap", "intraday", "prev"]].dropna()


def _confirm(x_disc: pd.Series, y_disc: pd.Series, x_conf: pd.Series, y_conf: pd.Series, price: float | None) -> dict:
    """前半で向きを決め、後半で当たり具合を測る。"""
    r_disc, _ = _corr(x_disc, y_disc)
    r_conf, p_conf = _corr(x_conf, y_conf)
    out = {"n_disc": len(x_disc), "n_conf": len(x_conf), "r_disc": r_disc, "r_conf": r_conf, "p_conf": p_conf,
           "hit_conf": None, "edge_pct": None, "edge_yen": None}
    if r_disc is None or r_conf is None:
        return out
    side = np.sign(r_disc) * np.sign(x_conf.values)      # 前半の向きで出した、後半の各日の売買方向
    trade = side != 0
    if trade.any():
        got = side[trade] * y_conf.values[trade]          # その方向に寄りで入り、引けで出た場合の%
        out["hit_conf"] = float((got > 0).mean())
        out["edge_pct"] = float(got.mean())
        out["edge_yen"] = float(got.mean() / 100 * price * 100) if price else None   # 100株あたり
    return out


def screen_stock(ohlc: pd.DataFrame, us_returns: dict[str, pd.Series]) -> dict:
    """1銘柄分のスクリーニング（純粋関数）。q値は screen_all でまとめて付ける。"""
    moves = daily_moves(ohlc)
    n = len(moves)
    closes = _norm(ohlc["Close"])
    price = float(closes.iloc[-1]) if len(closes) else None
    vol = _norm(ohlc["Volume"]) if "Volume" in ohlc else None
    turnover = float((closes * vol).dropna().tail(20).mean() / 1e8) if vol is not None and len(vol) else None

    rng = daily_range_pct(ohlc)
    range_pct = float(rng.tail(60).mean()) if len(rng) else None
    range_val = validate_range_prediction(ohlc, RANGE_WINDOW)

    out = {"n": n, "price": price, "turnover_oku": turnover,
           "range_pct": range_pct,
           "range_yen": range_pct / 100 * price * 100 if range_pct and price else None,
           "range_r": range_val.get("r"), "range_within": range_val.get("within_rate"),
           "predictors": {}}
    if n < MIN_HALF * 2:
        return out

    split = moves.index[n // 2]
    disc, conf = moves[moves.index < split], moves[moves.index >= split]
    out["split"] = split.strftime("%Y-%m-%d")
    for key in ("gap", "prev"):
        out["predictors"][key] = _confirm(disc[key], disc["intraday"], conf[key], conf["intraday"], price)

    # 米国株: 前半で一番連動したものを選び、後半はそれだけで測る
    best, best_r = None, 0.0
    aligned = {}
    for t, ur in us_returns.items():
        df = align_focus_returns(ur, ohlc["Open"], ohlc["Close"])
        d = df[df.index < split]
        if len(d) < MIN_HALF:
            continue
        r, _ = _corr(d["us"], d["intraday"])
        aligned[t] = df
        if r is not None and abs(r) > abs(best_r):
            best, best_r = t, r
    if best:
        df = aligned[best]
        d, c = df[df.index < split], df[df.index >= split]
        if len(c) >= MIN_HALF:
            out["predictors"]["us"] = {**_confirm(d["us"], d["intraday"], c["us"], c["intraday"], price), "us": best}
    return out


def screen_all(results: dict[str, dict]) -> dict[str, dict]:
    """全銘柄×全手がかりの後半のp値に補正をかけ、信頼できるかを付ける。"""
    keys = [(code, k) for code, r in results.items() for k in r.get("predictors", {})]
    qs = bh_qvalues([results[c]["predictors"][k]["p_conf"] for c, k in keys])
    for (code, k), q in zip(keys, qs):
        p = results[code]["predictors"][k]
        p["q_conf"] = q
        same_side = p["r_disc"] is not None and p["r_conf"] is not None and np.sign(p["r_disc"]) == np.sign(p["r_conf"])
        p["reliable"] = bool(q is not None and q < Q_THRESHOLD and same_side)
    return results


def run_stock_screen(period: str = "2y") -> dict:
    import yfinance as yf

    jp_tickers = [t for _, t, _ in SCREEN_JP]
    us_tickers = [t for t, _ in SCREEN_US]
    print(f"Fetching {len(jp_tickers)} JP + {len(us_tickers)} US tickers for stock screen, {period}...")
    data = yf.download(tickers=" ".join(jp_tickers + us_tickers), period=period, interval="1d",
                       progress=False, group_by="ticker", auto_adjust=True, threads=True)

    us_returns: dict[str, pd.Series] = {}
    for t in us_tickers:
        try:
            c = _norm(data[t]["Close"])
        except KeyError:
            continue
        if len(c) >= MIN_HALF:
            us_returns[t] = c.pct_change().dropna() * 100

    results: dict[str, dict] = {}
    names = {}
    for code, t, name in SCREEN_JP:
        names[code] = name
        try:
            ohlc = data[t][["Open", "High", "Low", "Close", "Volume"]]
        except KeyError:
            continue
        if ohlc.dropna().shape[0] < MIN_HALF * 2:
            continue
        results[code] = screen_stock(ohlc, us_returns)
    screen_all(results)

    rows = [{"code": c, "name": names[c], **r} for c, r in results.items()]
    for r in sorted(rows, key=lambda r: -(r["range_r"] or 0)):
        rel = [f"{PREDICTOR_LABELS[k]}({p.get('us', '')}) r={p['r_conf']:+.2f} q={p['q_conf']:.3f}"
               for k, p in r["predictors"].items() if p.get("reliable")]
        print(f"  {r['name']:10s} 値幅予測 r={r['range_r'] or float('nan'):+.2f} 値幅{r['range_pct'] or 0:.1f}%"
              f" 寄り後: {'・'.join(rel) or '信頼できる手がかりなし'}")
    return {"period": period, "q_threshold": Q_THRESHOLD, "results": rows,
            "predictor_labels": PREDICTOR_LABELS}
