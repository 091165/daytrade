"""
ボリンジャーバンド・移動平均線で、値幅や寄り後の方向を予測できるかの検証（研究用）

値幅（高値−安値）:
  - avg10: 直近10日の平均値幅（今の「値幅の目安」）
  - bbw:   ボリンジャーバンドの幅（20日・±2σ、終値に対する%）
  - combo: 両方を使った回帰（前半で係数を決め、後半で当たりを測る）
  - スクイーズ: バンド幅が直近120日の下位20%の日。その後、目安より大きく動くか

寄り後（寄り→引け）の方向。どれも前日の引け時点でわかる:
  - ma_trend: 5日線と25日線の差（＋＝ゴールデンクロスの状態、−＝デッドクロスの状態）
  - ma_cross: 直近3日以内のクロス（＋1＝ゴールデン、−1＝デッド、0＝なし）
  - above_ma: 終値と25日線の乖離
  - pct_b:    ボリンジャーバンドの中での位置（＋1＝上のバンド、−1＝下のバンド）

検証のしかたは stock_screen と同じ（前半で向きを決め、後半で測り、BH補正）。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from correlation_scan import Q_THRESHOLD, bh_qvalues
from expected_range import daily_range_pct
from focus_backtest import _corr, _norm
from stock_screen import MIN_HALF, SCREEN_JP, _confirm, daily_moves

TECH_LABELS = {
    "ma_trend": "5日線−25日線（GC/DCの状態）",
    "ma_cross": "直近3日のGC/DC",
    "above_ma": "25日線からの乖離",
    "pct_b": "ボリバンの中の位置",
}


def technicals(ohlc: pd.DataFrame) -> pd.DataFrame:
    """前日の引けまででわかる指標を、当日の行にそろえる（%）。"""
    c = _norm(ohlc["Close"])
    ma5, ma20, ma25 = c.rolling(5).mean(), c.rolling(20).mean(), c.rolling(25).mean()
    sd20 = c.rolling(20).std()
    diff = ma5 - ma25
    state = np.sign(diff)
    crossed = state.diff().fillna(0) / 2          # +1: ゴールデンクロス、-1: デッドクロス
    recent = crossed.rolling(3).sum().clip(-1, 1)
    df = pd.DataFrame({
        "ma_trend": diff / ma25 * 100,
        "ma_cross": recent,
        "above_ma": (c - ma25) / ma25 * 100,
        "pct_b": (c - ma20) / (2 * sd20),
        "bbw": 4 * sd20 / ma20 * 100,
    })
    return df.shift(1)                              # 当日の行には前日までの値だけ


def range_models(ohlc: pd.DataFrame) -> dict:
    """値幅の予測を avg10・bbw・両方 で比べる（後半の相関）。"""
    rng = daily_range_pct(ohlc)
    tech = technicals(ohlc)
    df = pd.DataFrame({
        "actual": rng,
        "avg10": rng.rolling(10).mean().shift(1),
        "bbw": tech["bbw"].reindex(rng.index),
    }).dropna()
    if len(df) < MIN_HALF * 2:
        return {}
    df["squeeze"] = df["bbw"] <= df["bbw"].rolling(120, min_periods=60).quantile(0.2)
    split = df.index[len(df) // 2]
    a, b = df[df.index < split], df[df.index >= split]
    X = np.column_stack([np.ones(len(a)), a["avg10"], a["bbw"]])
    coef, *_ = np.linalg.lstsq(X, a["actual"].values, rcond=None)
    combo = coef[0] + coef[1] * b["avg10"] + coef[2] * b["bbw"]
    ratio = b["actual"] / b["avg10"]
    sq = b["squeeze"]
    return {
        "r_avg10": _corr(b["avg10"], b["actual"])[0],
        "r_bbw": _corr(b["bbw"], b["actual"])[0],
        "r_combo": _corr(combo, b["actual"])[0],
        "n_squeeze": int(sq.sum()),
        "ratio_squeeze": float(ratio[sq].mean()) if sq.any() else None,     # 実際÷目安（スクイーズの日）
        "ratio_other": float(ratio[~sq].mean()) if (~sq).any() else None,
    }


def tech_stock(ohlc: pd.DataFrame) -> dict:
    moves = daily_moves(ohlc).join(technicals(ohlc), how="left")
    closes = _norm(ohlc["Close"])
    price = float(closes.iloc[-1]) if len(closes) else None
    out = {"range": range_models(ohlc), "predictors": {}}
    for key in TECH_LABELS:
        d = moves[[key, "intraday"]].dropna()
        if len(d) < MIN_HALF * 2:
            continue
        split = d.index[len(d) // 2]
        a, b = d[d.index < split], d[d.index >= split]
        out["predictors"][key] = _confirm(a[key], a["intraday"], b[key], b["intraday"], price)
    return out


def run_tech_screen(period: str = "2y") -> dict:
    import yfinance as yf

    tickers = [t for _, t, _ in SCREEN_JP]
    data = yf.download(tickers=" ".join(tickers), period=period, interval="1d",
                       progress=False, group_by="ticker", auto_adjust=True, threads=True)
    results = {}
    for code, t, name in SCREEN_JP:
        try:
            ohlc = data[t][["Open", "High", "Low", "Close"]]
        except KeyError:
            continue
        if ohlc.dropna().shape[0] < MIN_HALF * 2:
            continue
        results[code] = {"name": name, **tech_stock(ohlc)}

    keys = [(c, k) for c, r in results.items() for k in r["predictors"]]
    qs = bh_qvalues([results[c]["predictors"][k]["p_conf"] for c, k in keys])
    for (c, k), q in zip(keys, qs):
        p = results[c]["predictors"][k]
        p["q_conf"] = q
        p["reliable"] = bool(q is not None and q < Q_THRESHOLD and p["r_disc"] is not None
                             and p["r_conf"] is not None and np.sign(p["r_disc"]) == np.sign(p["r_conf"]))

    print("=== 値幅: 後半の相関（avg10 / ボリバン幅 / 両方） と スクイーズ後の 実際÷目安 ===")
    for c, r in sorted(results.items(), key=lambda x: -(x[1]["range"].get("r_avg10") or 0)):
        g = r["range"]
        if not g:
            continue
        print(f"  {r['name']:10s} avg10 {g['r_avg10']:+.2f}  bbw {g['r_bbw']:+.2f}  両方 {g['r_combo']:+.2f}"
              f"  スクイーズ{g['n_squeeze']:3d}日 {g['ratio_squeeze'] or float('nan'):.2f}倍 / 他 {g['ratio_other']:.2f}倍")
    print("=== 寄り後の方向: 後半の r / 当たり率 / q ===")
    n_raw = 0
    for c, r in results.items():
        cells = []
        for k, p in r["predictors"].items():
            if p["r_conf"] is None:
                continue
            n_raw += p["p_conf"] is not None and p["p_conf"] < 0.05
            hit = f"{p['hit_conf'] * 100:.0f}%" if p["hit_conf"] is not None else "-"
            mark = "✅" if p["reliable"] else ""
            cells.append(f"{k} {p['r_conf']:+.2f}(前半{p['r_disc']:+.2f}) {hit} q{p['q_conf']:.2f}{mark}")
        print(f"  {r['name']:10s} " + " | ".join(cells))
    n_rel = sum(1 for c, k in keys if results[c]["predictors"][k]["reliable"])
    print(f"  検証 {len(keys)}通り / 信頼できる: {n_rel} / 補正前 p<0.05: {n_raw}")
    return results


if __name__ == "__main__":
    run_tech_screen()
