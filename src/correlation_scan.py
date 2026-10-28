"""
米国株 × 日本株（売買代金上位）の連動スキャン（研究用・週次バックテストのみ）

目的:
  「どの米国株が、どの日本株と連動しているか」を総当たりで調べる。
  毎朝のダッシュボードには出さない（承認銘柄以外を触るきっかけを作らないため）。

検証の中身:
  各組み合わせについて、米国株の前日騰落率と、翌日の日本株の
    ① 窓（前日終値 → 寄り）
    ② 寄り後（寄り → 引け）  ← デイトレで取れる部分
  の相関を測る。

多重検定の補正:
  数百組を同時に調べると、偶然だけで p<0.05 になる組み合わせが必ず出る。
  そこで Benjamini-Hochberg 法で q値（偽発見率の補正後のp値）を計算し、
  q < 0.05 のものだけを「補正後も有意」とする。
  （純粋なノイズでの試験: 補正なしだと毎回平均14組が偽の当たり、q<0.10で30回中2回、q<0.05で0回）
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from focus_backtest import _corr, _norm, align_focus_returns

MIN_SAMPLES = 60
Q_THRESHOLD = 0.05

# 日本株: 売買代金上位（2026/10/28時点のランキング上位11銘柄）
SCAN_JP: list[tuple[str, str, str]] = [
    ("285A", "285A.T", "キオクシア"),
    ("6857", "6857.T", "アドバンテスト"),
    ("3905", "3905.T", "データセクション"),
    ("6976", "6976.T", "太陽誘電"),
    ("9984", "9984.T", "ソフトバンクG"),
    ("4062", "4062.T", "イビデン"),
    ("5803", "5803.T", "フジクラ"),
    ("8035", "8035.T", "東京エレクトロン"),
    ("6920", "6920.T", "レーザーテック"),
    ("1570", "1570.T", "日経レバ(1570)"),
    ("6981", "6981.T", "村田製作所"),
]

# 米国株の候補（テーマ別）
SCAN_US: list[tuple[str, str]] = [
    # 指数・ETF
    ("QQQ", "ナスダック100"), ("SPY", "S&P500"), ("SOXX", "半導体ETF"),
    # AI半導体
    ("NVDA", "NVIDIA"), ("AMD", "AMD"), ("AVGO", "Broadcom"), ("ARM", "Arm"), ("TSM", "TSMC"),
    # 半導体製造装置・検査
    ("AMAT", "Applied Materials"), ("LRCX", "Lam Research"), ("KLAC", "KLA"), ("ASML", "ASML"), ("TER", "Teradyne"),
    # メモリ・ストレージ
    ("MU", "Micron"), ("SNDK", "SanDisk"), ("WDC", "Western Digital"), ("STX", "Seagate"),
    # AIデータセンター・光通信
    ("GLW", "Corning"), ("COHR", "Coherent"), ("LITE", "Lumentum"), ("VRT", "Vertiv"), ("ANET", "Arista"),
    # 電子部品・スマホ
    ("AAPL", "Apple"), ("TEL", "TE Connectivity"), ("APH", "Amphenol"),
]


def bh_qvalues(pvalues: list[float | None]) -> list[float | None]:
    """Benjamini-Hochberg 法の q値。None はそのまま None を返す。"""
    idx = [i for i, p in enumerate(pvalues) if p is not None and not np.isnan(p)]
    q: list[float | None] = [None] * len(pvalues)
    m = len(idx)
    if m == 0:
        return q
    ps = np.array([pvalues[i] for i in idx], dtype=float)
    order = np.argsort(ps)
    ranked = ps[order] * m / np.arange(1, m + 1)
    # 後ろから累積最小をとって単調にする
    ranked = np.minimum.accumulate(ranked[::-1])[::-1]
    ranked = np.clip(ranked, 0, 1)
    for k, o in enumerate(order):
        q[idx[o]] = float(ranked[k])
    return q


def scan_pairs(
    us_returns: dict[str, pd.Series],
    jp_ohlc: dict[str, tuple[pd.Series, pd.Series]],
) -> list[dict]:
    """全組み合わせの相関を計算する（純粋関数）。

    Args:
        us_returns: 米ティッカー → 日次騰落率(%)のSeries
        jp_ohlc: 日本の銘柄コード → (Open, Close)
    """
    pairs = []
    for code, (jo, jc) in jp_ohlc.items():
        for ut, ur in us_returns.items():
            df = align_focus_returns(ur, jo, jc)
            n = len(df)
            if n < MIN_SAMPLES:
                continue
            gr, gp = _corr(df["us"], df["gap"])
            ir, ip = _corr(df["us"], df["intraday"])
            pairs.append({"jp": code, "us": ut, "n": n,
                          "gap_r": gr, "gap_p": gp, "intraday_r": ir, "intraday_p": ip})

    # 多重検定の補正（窓・寄り後それぞれで全組み合わせを母集団にする）
    for key in ("gap", "intraday"):
        qs = bh_qvalues([p[f"{key}_p"] for p in pairs])
        for p, q in zip(pairs, qs):
            p[f"{key}_q"] = q
            p[f"{key}_significant"] = q is not None and q < Q_THRESHOLD
    return pairs


def summarize(pairs: list[dict], top_n: int = 3) -> dict:
    """日本株ごとの上位と、寄り後まで連動する組み合わせをまとめる。"""
    jp_names = {c: n for c, _, n in SCAN_JP}
    us_names = dict(SCAN_US)

    by_jp = []
    for code, _, name in SCAN_JP:
        rows = [p for p in pairs if p["jp"] == code and p["gap_r"] is not None]
        if not rows:
            by_jp.append({"code": code, "name": name, "n": 0, "top_gap": [], "top_intraday": []})
            continue
        top_gap = sorted(rows, key=lambda p: p["gap_r"], reverse=True)[:top_n]
        top_intra = sorted(
            [p for p in rows if p["intraday_r"] is not None],
            key=lambda p: abs(p["intraday_r"]), reverse=True,
        )[:top_n]
        by_jp.append({
            "code": code, "name": name, "n": max(p["n"] for p in rows),
            "top_gap": [_compact(p, us_names) for p in top_gap],
            "top_intraday": [_compact(p, us_names) for p in top_intra],
        })

    intraday_hits = sorted(
        [p for p in pairs if p.get("intraday_significant")],
        key=lambda p: p["intraday_q"],
    )
    return {
        "n_pairs": len(pairs),
        "q_threshold": Q_THRESHOLD,
        "n_gap_significant": sum(1 for p in pairs if p.get("gap_significant")),
        "n_intraday_significant": len(intraday_hits),
        "n_intraday_raw_p05": sum(1 for p in pairs if p["intraday_p"] is not None and p["intraday_p"] < 0.05),
        "by_jp": by_jp,
        "intraday_hits": [
            {**_compact(p, us_names), "jp": p["jp"], "jp_name": jp_names.get(p["jp"], p["jp"])}
            for p in intraday_hits
        ],
    }


def _compact(p: dict, us_names: dict[str, str]) -> dict:
    return {
        "us": p["us"], "us_name": us_names.get(p["us"], p["us"]), "n": p["n"],
        "gap_r": p["gap_r"], "gap_q": p.get("gap_q"),
        "intraday_r": p["intraday_r"], "intraday_q": p.get("intraday_q"),
        "intraday_significant": p.get("intraday_significant", False),
    }


def run_correlation_scan(period: str = "1y") -> dict:
    import yfinance as yf

    jp_tickers = [t for _, t, _ in SCAN_JP]
    us_tickers = [t for t, _ in SCAN_US]
    print(f"Fetching {len(jp_tickers)} JP + {len(us_tickers)} US tickers for correlation scan, {period}...")
    data = yf.download(
        tickers=" ".join(jp_tickers + us_tickers), period=period, interval="1d",
        progress=False, group_by="ticker", auto_adjust=True, threads=True,
    )

    us_returns: dict[str, pd.Series] = {}
    for t in us_tickers:
        try:
            c = _norm(data[t]["Close"])
        except KeyError:
            continue
        if len(c) >= MIN_SAMPLES:
            us_returns[t] = c.pct_change().dropna() * 100

    jp_ohlc: dict[str, tuple[pd.Series, pd.Series]] = {}
    for code, t, _ in SCAN_JP:
        try:
            jo, jc = data[t]["Open"], data[t]["Close"]
        except KeyError:
            continue
        if jo.dropna().size >= MIN_SAMPLES:
            jp_ohlc[code] = (jo, jc)

    pairs = scan_pairs(us_returns, jp_ohlc)
    summary = summarize(pairs)
    print(f"  {summary['n_pairs']}組を検証 / 窓で有意(q<{Q_THRESHOLD}): {summary['n_gap_significant']}組"
          f" / 寄り後で有意: {summary['n_intraday_significant']}組"
          f"（補正前 p<0.05 は {summary['n_intraday_raw_p05']}組）")
    for h in summary["intraday_hits"]:
        print(f"    寄り後まで連動: {h['jp_name']} × {h['us']}  r={h['intraday_r']:+.3f} q={h['intraday_q']:.3f}")
    return {"period": period, "summary": summary, "pairs": pairs,
            "jp_universe": [{"code": c, "name": n} for c, _, n in SCAN_JP],
            "us_universe": [{"ticker": t, "name": n} for t, n in SCAN_US]}
