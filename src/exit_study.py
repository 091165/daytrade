"""
決済方法の比較：10時終了（今のルール）／時間方式／1：3 の値幅方式（研究用・週次レポートのみ）

メンサロ記事（過去ログ7・2025/9/30・10/19）では、デイトレで「損切り1：利確3」のように値幅で決める方式は
引けまでに利確に届かないことが多く、代わりに「時間で切る」方式をすすめている。
毎朝の画面とマイルール（trading_rules.py）は変えず、承認・試用銘柄の5分足で3通りを機械的に比べる。

  (a) 10時終了：9:05 に判定方向で入り、損切り幅に触れたら決済、触れなければ 10:00 に決済
  (b) 時間方式：10:00 までは (a) と同じ。10:00 に含み益なら、10:00 の値段から損切り幅ぶん不利な位置に
      逆指値を置き直し、10:00〜15:25 に触れたら決済、触れなければ引けで決済。含み益でなければ 10:00 に決済
  (c) 1：3 方式：損切り幅と、その3倍の利確の OCO。どちらにも触れなければ引けで決済

約束ごと:
  - 入る値段は 9:05 に始まる5分足の始値（checkpoint_study と同じ。Yahoo は 9:00〜9:05 の足が欠ける日が多い）
  - 逆指値・指値に触れたかは5分足の高値・安値で見る。同じ足で損切りと利確の両方に触れたら損切りが先とみなす
  - 足の始値がすでに逆指値の向こうにあれば始値で約定（不利側）。利確の指値は始値が向こうにあれば始値で約定
  - 判定が手出し無用の日は見送り。参考として、手出し無用の日に 寄り→9:05 の向きで入った場合も別に出す
  - 損益は100株・手数料なし
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

from trading_rules import DAILY_LOSS_LIMIT_YEN, PER_TRADE_STOP_MAX_YEN, REWARD_RISK_RATIO

SHARES = 100
STOP_YEN = PER_TRADE_STOP_MAX_YEN            # 損切り -5,000円（100株）
TARGET_YEN = STOP_YEN * REWARD_RISK_RATIO     # 利確 +15,000円（1：3）
ENTRY_TIME = "09:05"
END_TIME = "10:00"
LAST_BAR = "15:25"                           # この時刻より前に始まる足まで逆指値・指値を見る
CLOSE_TIME = "15:30"
N_BOOT = 2000
METHODS = ("a", "b", "c")
METHOD_LABELS = {"a": "(a) 10時終了（今のルール）", "b": "(b) 時間方式（10時に含み益なら引けまで）",
                 "c": "(c) 1：3（-5,000円／+15,000円の OCO）"}
SIDES = {"buy_bias": 1, "sell_bias": -1}
JOURNAL_RAW = "https://raw.githubusercontent.com/091165/daytrade-journal/main/"


# ========== 1日分のシミュレーション（純粋関数） ==========

def _hm(ts) -> str:
    return pd.Timestamp(ts).strftime("%H:%M")


def _stop_fill(bar, side: int, level: float) -> float | None:
    """逆指値に触れたら約定値段（始値が向こうなら始値）。触れなければ None"""
    if side > 0 and bar.Low <= level:
        return float(min(bar.Open, level))
    if side < 0 and bar.High >= level:
        return float(max(bar.Open, level))
    return None


def _limit_fill(bar, side: int, level: float) -> float | None:
    """利確の指値に触れたら約定値段（始値が向こうなら始値）。触れなければ None"""
    if side > 0 and bar.High >= level:
        return float(max(bar.Open, level))
    if side < 0 and bar.Low <= level:
        return float(min(bar.Open, level))
    return None


def _result(entry: float, side: int, price: float, time: str, reason: str, shares: int) -> dict:
    return {"exit_price": float(price), "exit_time": time, "reason": reason,
            "pnl": float(side * (price - entry) * shares)}


def simulate_day(bars: pd.DataFrame, side: int, close: float | None = None,
                 stop_yen: float = STOP_YEN, target_yen: float = TARGET_YEN, shares: int = SHARES) -> dict | None:
    """1銘柄1日分の (a)(b)(c)。bars はその日の5分足（index は JST の日時、Open/High/Low/Close）。

    side: +1 買い / -1 売り。close: 引けの値段（なければ最後の足の終値）。
    戻り値: {"entry", "side", "a": {...}, "b": {...}, "c": {...}}。9:05 の足がなければ None
    """
    if side == 0 or bars is None or bars.empty:
        return None
    b = bars.sort_index()
    hm = b.index.strftime("%H:%M")
    if ENTRY_TIME not in hm:
        return None
    b = b[hm >= ENTRY_TIME]
    hm = b.index.strftime("%H:%M")
    entry = float(b["Open"].iloc[0])
    stop_w, target_w = stop_yen / shares, target_yen / shares
    stop = entry - side * stop_w
    target = entry + side * target_w
    last = float(close) if close is not None and not pd.isna(close) else float(b["Close"].iloc[-1])
    morning = b[hm < END_TIME]
    after = b[(hm >= END_TIME) & (hm < LAST_BAR)]

    # (a) と (b) の 10:00 まで
    early = None
    for bar in morning.itertuples():
        px = _stop_fill(bar, side, stop)
        if px is not None:
            early = _result(entry, side, px, _hm(bar.Index), "損切り", shares)
            break
    if early is None:
        at10 = b[hm >= END_TIME]
        p10 = float(at10["Open"].iloc[0]) if not at10.empty else float(morning["Close"].iloc[-1])
        a = _result(entry, side, p10, END_TIME, "10時", shares)
    else:
        a = early

    # (b) 10:00 に含み益なら、逆指値を置き直して引けまで
    if early is not None:
        bb = dict(early)
    elif side * (p10 - entry) <= 0:
        bb = _result(entry, side, p10, END_TIME, "10時（含み益なし）", shares)
    else:
        stop2 = p10 - side * stop_w
        bb = None
        for bar in after.itertuples():
            px = _stop_fill(bar, side, stop2)
            if px is not None:
                bb = _result(entry, side, px, _hm(bar.Index), "逆指値（10時以降）", shares)
                break
        if bb is None:
            bb = _result(entry, side, last, CLOSE_TIME, "引け", shares)

    # (c) OCO（同じ足で両方触れたら損切りが先）
    c = None
    for bar in b[hm < LAST_BAR].itertuples():
        px = _stop_fill(bar, side, stop)
        if px is not None:
            c = _result(entry, side, px, _hm(bar.Index), "損切り", shares)
            break
        px = _limit_fill(bar, side, target)
        if px is not None:
            c = _result(entry, side, px, _hm(bar.Index), "利確", shares)
            break
    if c is None:
        c = _result(entry, side, last, CLOSE_TIME, "引け", shares)

    return {"entry": entry, "side": side, "a": a, "b": bb, "c": c}


# ========== 集計（純粋関数） ==========

def wilson_ci(k: int, n: int, z: float = 1.96) -> list[float] | None:
    if n == 0:
        return None
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return [max(0.0, c - h), min(1.0, c + h)]


def _boot_mean_ci(values: pd.Series, days: pd.Series, n_boot: int = N_BOOT, seed: int = 0) -> list[float] | None:
    """平均の95%区間。同じ日の銘柄どうしは一緒に動くので、日を単位に選び直す"""
    df = pd.DataFrame({"v": values.values, "d": days.values}).dropna()
    if df["d"].nunique() < 5:
        return None
    by_day = df.groupby("d")["v"].agg(["sum", "count"])
    s, c = by_day["sum"].values, by_day["count"].values
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(s), size=(n_boot, len(s)))
    means = s[idx].sum(axis=1) / c[idx].sum(axis=1)
    return [float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))]


def max_consecutive_losses(pnl: list[float]) -> int:
    best = run = 0
    for x in pnl:
        run = run + 1 if x < 0 else 0
        best = max(best, run)
    return best


def max_drawdown(pnl: list[float]) -> float:
    """累積損益の山から谷までの最大の下げ（円・0以下）"""
    if not pnl:
        return 0.0
    cum = np.cumsum([0.0] + list(pnl))
    return float((cum - np.maximum.accumulate(cum)).min())


def summarize(trades: pd.DataFrame, method: str, seed: int = 0) -> dict:
    """trades: date・code・{method}_pnl の行（時刻順に並べて使う）"""
    if trades is None or trades.empty:
        return {"n": 0}
    t = trades.sort_values(["date", "code"])
    pnl = t[f"{method}_pnl"].astype(float)
    n, wins = len(pnl), int((pnl > 0).sum())
    day = t.groupby("date")[f"{method}_pnl"].sum()
    return {
        "n": n, "n_days": int(t["date"].nunique()),
        "total": float(pnl.sum()), "mean": float(pnl.mean()), "median": float(pnl.median()),
        "mean_ci": _boot_mean_ci(pnl, t["date"], seed=seed),
        "win_rate": wins / n, "win_ci": wilson_ci(wins, n),
        "max_consec_losses": max_consecutive_losses(list(pnl)),
        "max_drawdown": max_drawdown(list(pnl)),
        "loss_limit_days": int((day <= -DAILY_LOSS_LIMIT_YEN).sum()),
    }


def compare_methods(trades: pd.DataFrame, seed: int = 0) -> dict:
    """3通りの成績と、(b)(c) − (a) の差（1回あたり・日を単位にした95%区間）"""
    out = {m: summarize(trades, m, seed) for m in METHODS}
    if trades is not None and not trades.empty:
        for m in ("b", "c"):
            d = trades[f"{m}_pnl"] - trades["a_pnl"]
            out[f"{m}_minus_a"] = {"mean": float(d.mean()), "ci": _boot_mean_ci(d, trades["date"], seed=seed)}
    return out


def study(trades: pd.DataFrame, reference: pd.DataFrame | None = None) -> dict:
    """全体・判定別・確度別・銘柄別と、参考（手出し無用の日に 寄り→9:05 の向きで入った場合）"""
    res = {"all": compare_methods(trades)}
    if trades is None or trades.empty:
        res.update(by_verdict={}, by_confidence={}, by_code={})
    else:
        res["by_verdict"] = {v: compare_methods(g) for v, g in trades.groupby("verdict")}
        res["by_confidence"] = {c: compare_methods(g) for c, g in trades.groupby("confidence")}
        res["by_code"] = {c: compare_methods(g) for c, g in trades.groupby("code")}
        res["exit_reasons"] = {m: trades[f"{m}_reason"].value_counts().to_dict() for m in METHODS}
    if reference is not None and not reference.empty:
        res["hands_off_reference"] = compare_methods(reference)
    return res


def build_trades(m5: dict[str, pd.DataFrame], daily: dict[str, pd.DataFrame],
                 verdicts: dict[str, dict], names: dict[str, str] | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    """銘柄ごとの5分足と日足、日付→判定 から、シミュレーションの行を作る（純粋関数）。

    m5 / daily: コード → JST の足。verdicts: "YYYY-MM-DD" → {"verdict", "confidence"}
    戻り値: (判定どおりの行, 手出し無用の日の参考の行)
    """
    rows, ref = [], []
    for code, bars in m5.items():
        if bars is None or bars.empty:
            continue
        d = daily.get(code)
        for day, g in bars.groupby(bars.index.normalize()):
            key = day.strftime("%Y-%m-%d")
            v = verdicts.get(key)
            if not v:
                continue
            close = d["Close"].get(day) if d is not None and day in d.index else None
            side = SIDES.get(v["verdict"], 0)
            target = rows
            if side == 0:
                # 参考：寄り→9:05 の向き（寄りは日足の始値）
                g905 = g[g.index.strftime("%H:%M") == ENTRY_TIME]
                if d is None or day not in d.index or g905.empty:
                    continue
                move = float(g905["Open"].iloc[0]) - float(d.at[day, "Open"])
                side = int(np.sign(move))
                target = ref
            sim = simulate_day(g, side, close)
            if sim is None:
                continue
            row = {"date": key, "code": code, "name": (names or {}).get(code, code),
                   "verdict": v["verdict"], "confidence": v.get("confidence") or "—",
                   "side": side, "entry": sim["entry"]}
            for m in METHODS:
                row[f"{m}_pnl"] = sim[m]["pnl"]
                row[f"{m}_exit"] = sim[m]["exit_price"]
                row[f"{m}_time"] = sim[m]["exit_time"]
                row[f"{m}_reason"] = sim[m]["reason"]
            target.append(row)
    return pd.DataFrame(rows), pd.DataFrame(ref)


# ========== 実際の約定との差（daytrade-journal） ==========

def compare_actual(trades: pd.DataFrame, real: list[dict]) -> dict:
    """実際の約定（その日その銘柄で 10:00 までに建てた最初の取引）と (a) のシミュレーションを並べる（純粋関数）。

    real: {"date", "code", "direction"("buy"/"sell"), "qty", "openTime", "openPrice", "closeTime", "closePrice", "pnl"}
    比べるのは、シミュレーションが入った日（判定どおりの行）と同じ日・同じ銘柄だけ。
    """
    if trades is None or trades.empty or not real:
        return {"n": 0, "rows": []}
    first: dict[tuple[str, str], dict] = {}
    for t in sorted(real, key=lambda x: (x["date"], x.get("openTime") or "99:99")):
        if not t.get("openTime") or t["openTime"] > END_TIME or not t.get("qty"):
            continue
        first.setdefault((t["date"], str(t["code"]).strip()), t)
    rows = []
    for r in trades.sort_values(["date", "code"]).itertuples():
        t = first.get((r.date, r.code))
        if t is None:
            continue
        side = 1 if t["direction"] == "buy" else -1
        per100 = float(t["pnl"]) * SHARES / float(t["qty"])
        rows.append({
            "date": r.date, "code": r.code, "name": r.name,
            "actual_side": side, "actual_open": t.get("openTime"), "actual_entry": t.get("openPrice"),
            "actual_close": t.get("closeTime"), "actual_pnl100": per100,
            "sim_side": int(r.side), "sim_entry": float(r.entry), "sim_pnl": float(r.a_pnl), "sim_time": r.a_time,
            "same_side": side == int(r.side),
            # 入った値段の差（判定方向に不利なほどプラス＝実際のほうが高く買った・安く売った）
            "entry_slip": (float(t["openPrice"]) - float(r.entry)) * int(r.side) if t.get("openPrice") else None,
            "pnl_diff": per100 - float(r.a_pnl),
        })
    if not rows:
        return {"n": 0, "rows": []}
    df = pd.DataFrame(rows)
    slip = df["entry_slip"].dropna()
    return {"n": len(df), "same_side": int(df["same_side"].sum()),
            "mean_actual": float(df["actual_pnl100"].mean()), "mean_sim": float(df["sim_pnl"].mean()),
            "mean_diff": float(df["pnl_diff"].mean()),
            "mean_entry_slip": float(slip.mean()) if len(slip) else None, "rows": rows}


def parse_real_trades(date: str, notes: dict | None) -> list[dict]:
    """振り返りメモ（data/{date}.json）の realTrades を平らにする"""
    out = []
    for code, ts in ((notes or {}).get("realTrades") or {}).items():
        for t in ts:
            out.append({"date": date, "code": str(code).strip(), "direction": t.get("direction"),
                        "qty": t.get("qty"), "openTime": t.get("openTime"), "openPrice": t.get("openPrice"),
                        "closeTime": t.get("closeTime"), "closePrice": t.get("closePrice"), "pnl": t.get("pnl", 0)})
    return out


def fetch_journal_trades(since: str, base: str = JOURNAL_RAW, timeout: int = 15) -> list[dict]:
    """daytrade-journal の振り返りメモから、since 以降の実質トレードを取る（取れなければ空）"""
    import urllib.request

    def get(path: str):
        with urllib.request.urlopen(base + path, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))

    out = []
    try:
        index = get("data/index.json")
    except Exception as e:
        print(f"  ⚠️ daytrade-journal を読み込めませんでした: {e}")
        return out
    for e in index:
        if e.get("date", "") < since or not e.get("notes"):
            continue
        try:
            out += parse_real_trades(e["date"], get(e["notes"]))
        except Exception:
            continue
    return out


# ========== データの取得と実行 ==========

def load_verdicts(docs_dir: Path) -> dict[str, dict]:
    """その朝の判定。寄り前の記録（docs/archive）を優先し、なければ docs/history.json"""
    out: dict[str, dict] = {}
    hist = docs_dir / "history.json"
    if hist.exists():
        for h in json.loads(hist.read_text(encoding="utf-8")):
            out[h["date"]] = {"verdict": h.get("verdict"), "confidence": h.get("confidence")}
    for p in sorted((docs_dir / "archive").glob("????-??-??.json")):
        try:
            a = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        d = a.get("direction") or {}
        if d.get("verdict"):
            out[p.stem] = {"verdict": d["verdict"], "confidence": d.get("confidence"),
                           "reconstructed": bool(a.get("reconstructed"))}
    return out


def _jst(x: pd.DataFrame) -> pd.DataFrame:
    x = x.dropna(subset=["Open", "High", "Low", "Close"])
    if getattr(x.index, "tz", None) is not None:
        x = x.tz_convert("Asia/Tokyo").tz_localize(None)
    return x


def run_exit_study() -> dict:
    import yfinance as yf
    from focus_stocks import FOCUS_STOCKS

    docs = Path(__file__).resolve().parent.parent / "docs"
    verdicts = load_verdicts(docs)
    tickers = {s.ticker: s for s in FOCUS_STOCKS}
    m5raw = yf.download(" ".join(tickers), period="60d", interval="5m", progress=False,
                        group_by="ticker", auto_adjust=True, threads=True)
    d1raw = yf.download(" ".join(tickers), period="6mo", interval="1d", progress=False,
                        group_by="ticker", auto_adjust=True, threads=True)
    m5, d1, names = {}, {}, {}
    for t, s in tickers.items():
        try:
            m5[s.code] = _jst(m5raw[t][["Open", "High", "Low", "Close"]])
            d = _jst(d1raw[t][["Open", "High", "Low", "Close"]])
            d.index = pd.to_datetime(d.index).normalize()
            d1[s.code] = d
            names[s.code] = s.name
        except KeyError:
            continue
    trades, ref = build_trades(m5, d1, verdicts, names)
    res = study(trades, ref)
    days = sorted({k for x in m5.values() for k in x.index.strftime("%Y-%m-%d")}) if m5 else []
    res["period"] = f"{days[0]}〜{days[-1]}" if days else ""
    res["n_market_days"] = len(days)
    res["n_hands_off_days"] = sum(1 for d in days if (verdicts.get(d) or {}).get("verdict") == "hands_off")
    res["names"] = names
    res["stop_yen"], res["target_yen"] = STOP_YEN, TARGET_YEN
    res["trades"] = trades.to_dict("records") if not trades.empty else []
    res["actual"] = compare_actual(trades, fetch_journal_trades(days[0] if days else "9999"))
    _print(res)
    return res


def _print(res: dict) -> None:
    a = res["all"]
    print(f"  決済方法の比較（{res.get('period', '')}・判定どおりに入った {a['a'].get('n', 0)}回）")
    for m in METHODS:
        s = a[m]
        if s.get("n"):
            print(f"    {METHOD_LABELS[m]}: 平均 {s['mean']:+,.0f}円 中央値 {s['median']:+,.0f}円 "
                  f"勝率 {s['win_rate']:.0%} 最大連敗 {s['max_consec_losses']} 最大DD {s['max_drawdown']:,.0f}円 "
                  f"上限到達 {s['loss_limit_days']}日")
    act = res.get("actual") or {}
    if act.get("n"):
        print(f"    実績との差（{act['n']}件）: 実績 {act['mean_actual']:+,.0f}円 / (a) {act['mean_sim']:+,.0f}円")


if __name__ == "__main__":
    run_exit_study()
