"""
取引前に決める「今日の作戦」：100株での損切り・利確の幅と、上値・下値の目安（レジサポ）（純粋関数）

損切り・利確:
  9時台の値幅の目安（1日の目安 × 銘柄ごとの比）を3つに分けた幅を損切り、その3倍を利確にする。
  損切りはルールの上限（-5,000円 = 100株で50円）を超えない。上限に当たる銘柄は
  「9時台の値幅に比べて損切りが近い（振り回されやすい）」と出す。
  決算明けは値幅が何倍にもなるので、その日は見送り候補にする。

上値・下値の目安:
  前日までに確定した日足だけで、前日の終値・高値・安値、直近5日・20日の高値・安値、25日線、
  キリのいい値段を出し、前日終値から 1日の目安 以内にあるものを近い順に並べる。
"""

from __future__ import annotations

import math

import pandas as pd

from trading_rules import PER_TRADE_STOP_MAX_YEN, REWARD_RISK_RATIO

SHARES = 100
STOP_PARTS = 3              # 9時台の目安の 1/3 を損切り幅に
MAX_LEVELS = 3              # 上下それぞれ近い順にいくつ出すか


def bracket(morning_width: float | None, earnings_today: bool = False, earnings_ratio: float | None = None,
            shares: int = SHARES, max_stop_yen: int = PER_TRADE_STOP_MAX_YEN, ratio: int = REWARD_RISK_RATIO) -> dict | None:
    """9時台の目安（1株あたりの円幅）から、損切り・利確の幅（円）を出す"""
    if not morning_width or morning_width <= 0:
        return None
    width = morning_width * (earnings_ratio or 1.0) if earnings_today else morning_width
    max_stop = max_stop_yen / shares
    raw = width / STOP_PARTS
    stop = min(raw, max_stop)
    stop = max(1.0, math.floor(stop))
    target = stop * ratio
    out = {
        "stop_width": stop, "target_width": target,
        "stop_yen": stop * shares, "target_yen": target * shares,
        "morning_width": width, "capped": raw > max_stop,
        "reachable": target <= width,
        "skip": bool(earnings_today),
    }
    notes = []
    if out["skip"]:
        notes.append("決算明けで値幅が普段の何倍にもなりやすい。今日はこの銘柄は見送り候補")
    elif out["capped"]:
        notes.append(f"9時台の値幅に比べて損切り（上限 -{max_stop_yen:,}円）が近い。普段の上下で引っかかりやすい")
    if not out["reachable"] and not out["skip"]:
        notes.append("利確の幅は9時台の目安より大きい＝10時までに届かない日が多い")
    out["notes"] = notes
    return out


def round_steps(price: float) -> tuple[float, float]:
    """キリのいい値段の刻み（細かい刻み, 大きい刻み）"""
    if price < 1000:
        return 10, 50
    if price < 5000:
        return 100, 500
    if price < 10000:
        return 100, 1000
    if price < 50000:
        return 500, 5000
    return 1000, 10000


def price_levels(ohlc: pd.DataFrame, day_width: float, merge: bool = True) -> list[dict]:
    """確定した日足（今日を含めない）から、上値・下値の目安の候補を作る"""
    d = ohlc.dropna()
    if len(d) < 2 or not day_width:
        return []
    last = d.iloc[-1]
    pc = float(last["Close"])
    cands: list[tuple[str, float]] = [
        ("前日高値", float(last["High"])), ("前日安値", float(last["Low"])),
    ]
    if len(d) >= 5:
        cands += [("5日高値", float(d["High"].iloc[-5:].max())), ("5日安値", float(d["Low"].iloc[-5:].min()))]
    if len(d) >= 20:
        cands += [("20日高値", float(d["High"].iloc[-20:].max())), ("20日安値", float(d["Low"].iloc[-20:].min()))]
    if len(d) >= 25:
        cands.append(("25日線", float(d["Close"].iloc[-25:].mean())))
    minor, major = round_steps(pc)
    lo, hi = pc - day_width, pc + day_width
    k = math.floor(lo / minor) * minor
    while k <= hi:
        if k > 0:
            cands.append(("キリ（大）" if k % major == 0 else "キリ", float(k)))
        k += minor
    out = []
    for kind, px in cands:
        if lo <= px <= hi and abs(px - pc) >= 1e-9:
            out.append({"kind": kind, "price": px, "dist": px - pc, "side": "up" if px > pc else "down"})
    out.sort(key=lambda x: x["price"])
    if not merge:
        return out
    # 同じ値段（±1円）は1つにまとめる（名前はつなげる）
    merged: list[dict] = []
    for x in out:
        if merged and abs(merged[-1]["price"] - x["price"]) <= max(1.0, pc * 0.0005):
            if x["kind"] not in merged[-1]["kind"]:
                merged[-1]["kind"] += "・" + x["kind"]
            continue
        merged.append(dict(x))
    return merged


def nearest_levels(levels: list[dict], n: int = MAX_LEVELS) -> dict:
    """上と下それぞれ、前日終値に近い順に n 個。キリ（小）だけの水準は、ほかに無いときだけ使う"""
    def pick(side: str) -> list[dict]:
        xs = sorted([x for x in levels if x["side"] == side], key=lambda x: abs(x["dist"]))
        main = [x for x in xs if x["kind"] != "キリ"]
        rest = [x for x in xs if x["kind"] == "キリ"]
        return sorted((main + rest)[:n], key=lambda x: abs(x["dist"]))
    return {"up": pick("up"), "down": pick("down")}


def build_trade_plan(ohlc: pd.DataFrame | None, rng: dict | None, morning: dict | None,
                     level_validation: dict | None = None) -> dict | None:
    """カード用：今日の作戦（100株）と上値・下値の目安"""
    if ohlc is None or ohlc.empty or not rng or not rng.get("available"):
        return None
    mo = morning or {}
    width = mo.get("morning_yen") if mo.get("available") else None
    plan = {"bracket": bracket(width, bool(mo.get("earnings_today")), mo.get("earnings_ratio"))}
    as_of = pd.Timestamp(rng["as_of"]) if rng.get("as_of") else ohlc.index[-1]
    done = ohlc[ohlc.index <= as_of]
    lv = nearest_levels(price_levels(done, float(rng["pred_yen"])))
    plan["levels"] = lv
    plan["prev_close"] = float(done["Close"].iloc[-1]) if not done.empty else None
    plan["level_evidence"] = level_evidence(level_validation)
    return plan


def level_evidence(v: dict | None) -> str:
    """週次レポートの検証結果（水準ごとの止まりやすさ）を1文に"""
    if not v or not v.get("kinds"):
        return "未検証（毎週日曜のバックテスト後に、止まりやすさを表示）"
    base = v.get("control_bounce")
    good = [k for k in v["kinds"] if k.get("significant") and k.get("diff", 0) > 0]
    if good:
        txt = "・".join(f'{k["kind"]} {k["bounce"] * 100:.0f}%' for k in good)
        return f"検証（{v['source']}）：止まりやすい水準 {txt}（比較用 {base * 100:.0f}%）"
    return f"検証（{v['source']}）：どの水準も、比較用の値段（止まった割合 {base * 100:.0f}%）と差なし。目安として使う"
