"""
キリのいい値段（大台）の検証（研究用・週次レポート。fib_study と同じ枠組み）

touch（過去ログ6 L6-2 ／ 過去ログ7 L7-3）:
  日経平均（N225）・日経レバ（1570）・ダブルインバース（1357）の1分足で、1,000円単位の大台に
  その日初めて触れたときと、2回目以降に触れたときの「止まった（反発）」割合を比べる。
  比較用に、大台から 500円 ずらした水準でも同じことをする。
  さらに、その日の判定（買い・売りポジ主体／手出し無用）ごとに、下から支える側（サポート）と
  上から押さえる側（レジスタンス）の止まった割合を分けて出す。

dip（過去ログ6 L6-3）:
  承認・試用銘柄の1分足で、1,000円節目（株価1万円以上は 1,000円と 10,000円）を上から下に割ったあと、
  「節目 − 0.5%」より下に行かずに節目の上に戻った割合を、比較用の水準（節目 ± 1.5% の値段）と比べる。
  有意（95%区間が0をまたがない）なら、カードに「急落時の反発候補」を出す。

止まった・抜けた: 水準に触れたあと、水準から STEP_PCT だけ戻したか・進んだか（fib_study の _resolve）。
2回目以降: いちど水準から STEP_PCT 以上離れてから、また触れたもの。
差の95%区間: 同じ日の同じ銘柄の出来事どうしは一緒に動くので、(銘柄, 日) の単位で選び直すブートストラップ。
1分足は data/minutes（9:00〜10:29）。保存した分しかないので、件数がたまるまでは「件数不足」と出る。
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

from fib_study import _resolve

INDEX_CODES = {"N225": "日経平均", "1570": "日経レバ", "1357": "Wインバース"}
LEVEL_STEP = 1000
CONTROL_OFFSET = 500
STEP_PCT = 0.0015            # 止まった・抜けた の判定幅（水準の 0.15%）
DIP_PCT = 0.005              # 節目 − 0.5% まで
CONTROL_DIP = (-0.015, 0.015)
MIN_EVENTS = 30
N_BOOT = 2000


# ========== 触れた回数ごとの結果（純粋関数） ==========

def touch_events(bars: pd.DataFrame, level: float, step: float) -> list[dict]:
    """1日分の足（時刻順、Open/High/Low/Close）で、水準に触れるたびの結果。

    戻り値: [{"touch": 1, "approach": "support"|"resistance", "result": "bounce"|"break"|None}, ...]
    support = 上から下りてきて触れた（下で支えられるか）、resistance = 下から上がってきて触れた
    """
    if bars.empty:
        return []
    hi, lo, cl = bars["High"].values, bars["Low"].values, bars["Close"].values
    op = bars["Open"].values if "Open" in bars else cl
    out, armed, k = [], True, 0
    for i in range(len(hi)):
        ref = cl[i - 1] if i > 0 else op[0]
        if armed and ref != level and lo[i] <= level <= hi[i]:
            from_above = ref > level
            k += 1
            res = _resolve(bars.iloc[i:], level, step, down=from_above)
            out.append({"touch": k, "approach": "support" if from_above else "resistance", "result": res})
            armed = False
        if not armed and abs(cl[i] - level) >= step:
            armed = True
    return out


def day_touch_events(bars: pd.DataFrame, step_pct: float = STEP_PCT) -> list[dict]:
    """1銘柄1日分。その日の値幅にある大台（1,000円単位）と、比較用（+500円）のすべて"""
    if bars.empty:
        return []
    lo, hi = float(bars["Low"].min()), float(bars["High"].max())
    out = []
    k = math.ceil(lo / LEVEL_STEP) * LEVEL_STEP
    while k <= hi:
        for lv, big in ((k, True), (k - CONTROL_OFFSET, False), (k + CONTROL_OFFSET, False)):
            if lo <= lv <= hi and lv > 0:
                for e in touch_events(bars, lv, lv * step_pct):
                    out.append({**e, "level": float(lv), "big": big})
        k += LEVEL_STEP
    # +500 と次の大台の -500 は同じ値段なので重複を除く
    seen, uniq = set(), []
    for e in out:
        key = (e["level"], e["touch"], e["approach"])
        if key not in seen:
            seen.add(key)
            uniq.append(e)
    return uniq


# ========== 節目を少し割ったところ（純粋関数） ==========

def dip_result(bars: pd.DataFrame, level: float, dip_pct: float = DIP_PCT) -> str | None:
    """上から節目を割ったあと、節目 − dip_pct より下に行かずに節目の上に戻ったら "held"、
    先に下に行ったら "failed"。割らなかった・どちらにもならなかったら None"""
    hi, lo, cl = bars["High"].values, bars["Low"].values, bars["Close"].values
    op = bars["Open"].values if "Open" in bars else cl
    floor = level * (1 - dip_pct)
    start = None
    for i in range(len(lo)):
        ref = cl[i - 1] if i > 0 else op[0]
        if ref > level and lo[i] < level:
            start = i
            break
    if start is None:
        return None
    if lo[start] < floor:
        return "failed"
    for j in range(start + 1, len(lo)):
        if lo[j] < floor:
            return "failed"
        if cl[j] > level:
            return "held"
    return None


def day_dip_events(bars: pd.DataFrame) -> list[dict]:
    """1銘柄1日分：その日の値幅にある節目（と比較用の値段）の結果"""
    if bars.empty:
        return []
    lo, hi = float(bars["Low"].min()), float(bars["High"].max())
    out = []
    big = float(bars["Close"].iloc[0]) >= 10000          # 株価1万円以上は 10,000円の節目を「大台」とする
    k = math.ceil(lo / LEVEL_STEP) * LEVEL_STEP
    while k <= hi:
        major = big and k % 10000 == 0
        r = dip_result(bars, k)
        if r:
            out.append({"level": float(k), "round": True, "major": major, "result": r})
        for off in CONTROL_DIP:
            lv = k * (1 + off)
            r = dip_result(bars, lv)
            if r:
                out.append({"level": lv, "round": False, "major": major, "result": r})
        k += LEVEL_STEP
    return out


# ========== 集計（純粋関数） ==========

def boot_diff(ev: pd.DataFrame, ok: str, a: pd.Series, b: pd.Series, seed: int = 0) -> dict:
    """ev の ok 列（0/1）の割合の差 A − B と、(code, date) 単位のブートストラップの95%区間"""
    ea, eb = ev[a], ev[b]
    out = {"a": float(ea[ok].mean()) if len(ea) else None, "n_a": int(len(ea)),
           "b": float(eb[ok].mean()) if len(eb) else None, "n_b": int(len(eb)),
           "diff": None, "ci": None, "significant": False}
    if len(ea) < MIN_EVENTS or len(eb) < MIN_EVENTS:
        return out
    out["diff"] = out["a"] - out["b"]
    x = ev.assign(_a=a.astype(int), _b=b.astype(int), _ok=ev[ok].astype(float))
    g = x.groupby(["code", "date"]).apply(
        lambda d: pd.Series({"sa": (d["_ok"] * d["_a"]).sum(), "na": d["_a"].sum(),
                             "sb": (d["_ok"] * d["_b"]).sum(), "nb": d["_b"].sum()}), include_groups=False)
    m = g.values
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(m), size=(N_BOOT, len(m)))
    s = m[idx].sum(axis=1)
    with np.errstate(invalid="ignore", divide="ignore"):
        d = s[:, 0] / s[:, 1] - s[:, 2] / s[:, 3]
    d = d[np.isfinite(d)]
    if len(d) == 0:
        return out
    lo, hi = np.percentile(d, [2.5, 97.5])
    out["ci"] = [float(lo), float(hi)]
    out["significant"] = bool(lo > 0 or hi < 0)
    return out


def _rate(x: pd.DataFrame, ok: str) -> dict:
    return {"n": int(len(x)), "rate": float(x[ok].mean()) if len(x) else None}


def summarize_touch(ev: pd.DataFrame) -> dict:
    """ev: code・date・verdict・touch・approach・big・result"""
    ev = ev.dropna(subset=["result"]) if not ev.empty else ev
    if ev.empty:
        return {"n": 0}
    ev = ev.assign(bounce=(ev["result"] == "bounce").astype(float))
    first, later = ev["touch"] == 1, ev["touch"] >= 2
    out = {
        "n": int(len(ev)),
        "groups": {
            "big_first": _rate(ev[ev["big"] & first], "bounce"),
            "big_later": _rate(ev[ev["big"] & later], "bounce"),
            "ctl_first": _rate(ev[~ev["big"] & first], "bounce"),
            "ctl_later": _rate(ev[~ev["big"] & later], "bounce"),
        },
        "first_vs_control": boot_diff(ev, "bounce", ev["big"] & first, ~ev["big"] & first),
        "first_vs_later": boot_diff(ev, "bounce", ev["big"] & first, ev["big"] & later),
        "by_verdict": {},
        "by_code": {c: _rate(g[g["big"] & (g["touch"] == 1)], "bounce") for c, g in ev.groupby("code")},
    }
    for v, g in ev[ev["big"]].groupby("verdict"):
        out["by_verdict"][v] = {ap: {"first": _rate(gg[gg["touch"] == 1], "bounce"), "all": _rate(gg, "bounce")}
                                for ap, gg in g.groupby("approach")}
    return out


def summarize_dip(ev: pd.DataFrame) -> dict:
    if ev.empty:
        return {"n": 0}
    ev = ev.assign(held=(ev["result"] == "held").astype(float))
    out = {"n": int(len(ev)), "round": _rate(ev[ev["round"]], "held"), "control": _rate(ev[~ev["round"]], "held")}
    out.update(boot_diff(ev, "held", ev["round"], ~ev["round"]))
    out["by_code"] = {c: _rate(g[g["round"]], "held") for c, g in ev.groupby("code")}
    return out


def dip_card_note(validation: dict | None, prev_close: float | None) -> dict | None:
    """検証で有意なときだけ、カードに出す1行（検証前・有意でなければ出さない）"""
    if not validation or not validation.get("significant") or (validation.get("diff") or 0) <= 0 or not prev_close:
        return None
    lv = math.floor(prev_close / 1000) * 1000
    if lv <= 0:
        return None
    return {"text": f"急落時の反発候補：節目 {lv:,.0f}円 と その少し下（{lv * (1 - DIP_PCT):,.0f}円前後）"
                    f"（検証：節目を割って戻った {validation['a'] * 100:.0f}% ／ ほかの値段 {validation['b'] * 100:.0f}%）",
            "level": "info", "kind": "dip"}


# ========== 株価の大台の手前（過去ログ6 L6-4・手法10） ==========

NEAR_PCT = 0.03              # 大台の ±3% 以内
HORIZON = 5                  # 翌日から5営業日
CONTROL_MULT = (0.93, 1.07)  # 比較用：大台を 7% ずらした（キリのよくない）値段


def big_numbers(price: float) -> list[float]:
    """株価のまわりの大台：桁が変わる値段（1,000円・10,000円・100,000円）と 5,000円の倍数"""
    out = {10.0 ** k for k in range(3, 7)}
    m = math.floor(price / 5000)
    out |= {5000.0 * j for j in range(max(1, m - 1), m + 3)}
    return sorted(out)


def near_big_number(prev_close: float | None, pct: float = NEAR_PCT) -> dict | None:
    """前日終値が大台の ±pct 以内なら、いちばん近い大台と離れ具合"""
    if not prev_close or prev_close <= 0:
        return None
    best = min(big_numbers(prev_close), key=lambda x: abs(prev_close / x - 1))
    gap = prev_close / best - 1
    if abs(gap) > pct:
        return None
    return {"level": best, "gap_pct": gap * 100, "below": prev_close < best}


def big_number_note(prev_close: float | None, validation: dict | None = None) -> dict | None:
    """カードの1行：「大台 10,000円の手前：いったん止まりやすい（突破すれば強い）」"""
    n = near_big_number(prev_close)
    if not n:
        return None
    where = "手前" if n["below"] else "を少し上回ったところ"
    ev = "（未検証）"
    v = validation or {}
    if v.get("round", {}).get("n"):
        r, c = v["round"], v["control"]
        ev = (f'（検証：大台の手前から{HORIZON}日以内に抜けた {r["rate"] * 100:.0f}%・{r["n"]}回 ／ '
              f'ほかの値段 {(c.get("rate") or 0) * 100:.0f}%{"・差あり" if v.get("significant") else "・差ははっきりしない"}）')
    return {"text": f'大台 {n["level"]:,.0f}円の{where}（前日終値 {n["gap_pct"]:+.1f}%）：いったん止まりやすい（突破すれば強い）{ev}',
            "level": "info", "kind": "big_number"}


def approach_episodes(close: pd.Series, level: float, pct: float = NEAR_PCT, horizon: int = HORIZON) -> list[dict]:
    """下から level の pct 以内に入った日（入り直すたびに1回）ごとに、その後 horizon 日で
    終値が level を超えたら "break"、超えずに入った日の終値より下で終わったら "pushed"、それ以外は "flat" """
    c = close.dropna().values
    out, inside = [], False
    for i in range(len(c) - horizon):
        zone = level * (1 - pct) <= c[i] < level
        if zone and not inside:
            fut = c[i + 1:i + 1 + horizon]
            res = "break" if (fut > level).any() else ("pushed" if fut[-1] < c[i] else "flat")
            out.append({"i": i, "result": res})
        inside = zone
    return out


def big_number_study(closes: dict[str, pd.Series]) -> dict:
    """銘柄ごとの日足の終値から、大台の手前に来たあとの「抜けた」割合を、比較用の値段と比べる（純粋関数）"""
    rows = []
    for code, c in closes.items():
        c = c.dropna()
        if len(c) < HORIZON + 2:
            continue
        lo, hi = float(c.min()), float(c.max()) * 1.05
        levels = [x for x in big_numbers(float(c.median())) + big_numbers(lo) + big_numbers(hi) if lo <= x <= hi]
        for lv in sorted(set(levels)):
            for e in approach_episodes(c, lv):
                rows.append({"code": code, "date": e["i"], "round": True, "result": e["result"]})
            for m in CONTROL_MULT:
                for e in approach_episodes(c, lv * m):
                    rows.append({"code": code, "date": e["i"], "round": False, "result": e["result"]})
    ev = pd.DataFrame(rows)
    if ev.empty:
        return {"n": 0}
    ev = ev.assign(brk=(ev["result"] == "break").astype(float), pushed=(ev["result"] == "pushed").astype(float))
    out = {"n": int(len(ev)), "round": _rate(ev[ev["round"]], "brk"), "control": _rate(ev[~ev["round"]], "brk"),
           "pushed_round": _rate(ev[ev["round"]], "pushed"), "pushed_control": _rate(ev[~ev["round"]], "pushed")}
    out.update(boot_diff(ev, "brk", ev["round"], ~ev["round"]))
    out["by_code"] = {c: _rate(g[g["round"]], "brk") for c, g in ev.groupby("code")}
    return out


def run_big_number_study() -> dict:
    import yfinance as yf
    from focus_stocks import FOCUS_STOCKS
    from idea_study2 import BIG_CAPS

    tickers = {s.ticker: s.code for s in FOCUS_STOCKS}
    tickers.update({t: t.replace(".T", "") for t in BIG_CAPS})
    d = yf.download(" ".join(tickers), period="2y", interval="1d", progress=False, group_by="ticker",
                    auto_adjust=False, threads=True)       # 大台は実際の値段で見るので、配当調整しない
    closes = {}
    for t, code in tickers.items():
        try:
            closes[code] = d[t]["Close"].dropna()
        except KeyError:
            continue
    focus = {s.code for s in FOCUS_STOCKS}
    res = {"all": big_number_study(closes),
           "focus": big_number_study({c: x for c, x in closes.items() if c in focus}),
           "period": "2年・日足", "horizon": HORIZON, "near_pct": NEAR_PCT}
    a = res["all"]
    if a.get("n"):
        print(f"  大台の手前→{HORIZON}日以内に抜けた {a['round']['rate'] * 100:.0f}%（{a['round']['n']}） / "
              f"比較用 {(a['control']['rate'] or 0) * 100:.0f}%（{a['control']['n']}）")
    return res


# ========== 実行 ==========

def _bars_by_code_day(saved: pd.DataFrame, codes) -> dict[tuple[str, pd.Timestamp], pd.DataFrame]:
    out = {}
    x = saved[saved["code"].isin(list(codes))]
    for (code, day), g in x.groupby(["code", "date"]):
        g = g.sort_values("time")
        out[(code, day)] = pd.DataFrame({"Open": g["open"].values, "High": g["high"].values,
                                         "Low": g["low"].values, "Close": g["close"].values})
    return out


def run_round_study() -> dict:
    from pathlib import Path

    from collect_minutes import load_minutes
    from exit_study import load_verdicts
    from focus_stocks import FOCUS_STOCKS

    saved = load_minutes()
    verdicts = load_verdicts(Path(__file__).resolve().parent.parent / "docs")
    n_days = int(saved["date"].nunique()) if not saved.empty else 0

    rows = []
    for (code, day), bars in _bars_by_code_day(saved, INDEX_CODES).items():
        v = (verdicts.get(day.strftime("%Y-%m-%d")) or {}).get("verdict") or "不明"
        for e in day_touch_events(bars):
            rows.append({**e, "code": code, "date": day, "verdict": v})
    touch = summarize_touch(pd.DataFrame(rows))

    names = {s.code: s.name for s in FOCUS_STOCKS}
    drows = []
    for (code, day), bars in _bars_by_code_day(saved, names).items():
        for e in day_dip_events(bars):
            drows.append({**e, "code": code, "date": day})
    dip = summarize_dip(pd.DataFrame(drows))

    try:
        big = run_big_number_study()
    except Exception as e:
        print(f"  ⚠️ 大台の手前の検証に失敗: {e}")
        big = None
    res = {"big_number": big, "source": f"保存した1分足（{n_days}日・9:00〜10:29）", "n_days": n_days,
           "step_pct": STEP_PCT, "dip_pct": DIP_PCT, "names": {**INDEX_CODES, **names},
           "touch": touch, "dip": dip}
    t = touch.get("groups") or {}
    if t:
        f = lambda g: f'{(g["rate"] or 0) * 100:.0f}%（{g["n"]}）'
        print(f"  大台 初回 {f(t['big_first'])} / 2回目以降 {f(t['big_later'])} / 比較用 初回 {f(t['ctl_first'])}")
    if dip.get("n"):
        print(f"  節目割れから戻った {dip['round']['rate'] * 100:.0f}%（{dip['round']['n']}） / 比較用 "
              f"{(dip['control']['rate'] or 0) * 100:.0f}%（{dip['control']['n']}）")
    print(f"  データ: {res['source']}")
    return res


def levels_evidence(validation: dict | None, verdict: str | None) -> str:
    """毎朝の節目欄の下に出す検証の一文（結果がなければ空）"""
    t = (validation or {}).get("touch") or {}
    g = t.get("groups") or {}
    bf, cf = g.get("big_first") or {}, g.get("ctl_first") or {}
    if not bf.get("n"):
        return ""
    d = t.get("first_vs_control") or {}
    verdict_txt = ("差あり" if d.get("significant") else "差ははっきりしない") if d.get("diff") is not None else "件数不足"
    out = (f'検証（{validation.get("source", "")}）：大台に初めて触れて止まった {bf["rate"] * 100:.0f}%（{bf["n"]}回）'
           f' ／ 比較用の値段 {(cf.get("rate") or 0) * 100:.0f}%（{cf.get("n", 0)}回）・{verdict_txt}')
    ap = {"buy_bias": ("support", "サポート"), "sell_bias": ("resistance", "レジスタンス")}.get(verdict or "")
    x = ((t.get("by_verdict") or {}).get(verdict or "") or {})
    if ap and (x.get(ap[0]) or {}).get("first", {}).get("n"):
        f = x[ap[0]]["first"]
        out += f'。同じ判定の日の{ap[1]}（初回）{f["rate"] * 100:.0f}%（{f["n"]}回）'
    return out
