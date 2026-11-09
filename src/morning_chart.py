"""
寄り〜10:30 の分足チャート（1銘柄・直近20営業日）を PNG に描く

上段：日ごとの値動き（終値ベース）を横に並べる。各日の高値・安値と時刻、最初の10分（9:00〜9:09）の
      高値・安値（点線）。日付の下に「寄り→10:30 の騰落」「窓」「9:10 以降に最初の10分の値幅を
      上抜け・下抜けしたか（時刻）」を出す。
下段：日ごとの値動きを縦に並べる（下ほど遅い時刻・右ほど高い）。高値▲・安値▼の時刻と、
      極値から終値で1%戻すまでの時間（10:30 までに戻さなければ「未」）。

データ: Yahoo のチャートAPI（1分足は直近30日分まで）。--from-minutes で data/minutes/ の保存分を使う。
出力: docs/charts/{code}_morning.png

使い方: python src/morning_chart.py 5803 [--days 20] [--interval 1m] [--from-minutes]
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

JST = ZoneInfo("Asia/Tokyo")
START, END = time(9, 0), time(10, 29)       # 10:29 の足まで（= 10:30 まで）
DECIDE = time(9, 10)                       # 9:10 の確認
REVERT_PCT = 1.0
ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "docs" / "charts"
SLOTS = 90                                 # 9:00〜10:29 の分数

UP_RED, DOWN_BLUE, LOW_GREEN = "#d62728", "#1f5fbf", "#2ca02c"


def _minute(t: time) -> int:
    return (t.hour - 9) * 60 + t.minute


def _hm(t: time) -> str:
    return f"{t.hour:02d}:{t.minute:02d}"


def split_days(df: pd.DataFrame) -> dict:
    """分足（UTC か JST の日時 index）を、日付ごとの 9:00〜10:29 の足に分ける（純粋関数）。"""
    if df is None or df.empty:
        return {}
    x = df.dropna(subset=["Open", "High", "Low", "Close"])
    if getattr(x.index, "tz", None) is not None:
        x = x.tz_convert(JST)
    keep = [START <= ts.time() <= END for ts in x.index]
    x = x[keep]
    return {d: g for d, g in x.groupby(x.index.date)}


def summarize_day(bars: pd.DataFrame, prev_close: float | None = None) -> dict:
    """1日分（9:00〜10:29）の足から、高値・安値・最初の10分・抜けた時刻・1%戻しを出す（純粋関数）。"""
    bars = bars.sort_index()
    times = [ts.time() for ts in bars.index]
    hi_i = int(bars["High"].values.argmax())
    lo_i = int(bars["Low"].values.argmin())
    o = float(bars["Open"].iloc[0])
    c = float(bars["Close"].iloc[-1])
    s = {
        "open": o, "close": c, "first_time": times[0],
        "high": float(bars["High"].iloc[hi_i]), "high_time": times[hi_i],
        "low": float(bars["Low"].iloc[lo_i]), "low_time": times[lo_i],
        "change_pct": (c / o - 1) * 100,
        "gap_pct": (o / prev_close - 1) * 100 if prev_close else None,
    }

    first = bars[[t < DECIDE for t in times]]
    after = bars[[t >= DECIDE for t in times]]
    if first.empty:
        s.update(hi10=None, lo10=None, dir10=None, width10=None, brk="データなし", brk_time=None)
    else:
        hi10, lo10 = float(first["High"].max()), float(first["Low"].min())
        s.update(hi10=hi10, lo10=lo10, width10=hi10 - lo10,
                 dir10=float(first["Close"].iloc[-1]) - float(first["Open"].iloc[0]))
        up = next((ts.time() for ts, r in after.iterrows() if r["High"] > hi10), None)
        dn = next((ts.time() for ts, r in after.iterrows() if r["Low"] < lo10), None)
        if up and dn:
            s["brk"], s["brk_time"] = ("両抜け", min(up, dn))
            s["brk_first"] = "上" if up <= dn else "下"
        elif up:
            s["brk"], s["brk_time"] = "上抜け", up
        elif dn:
            s["brk"], s["brk_time"] = "下抜け", dn
        else:
            s["brk"], s["brk_time"] = "内側", None

    def revert(i: int, target: float, below: bool):
        for ts, r in bars.iloc[i + 1:].iterrows():
            if (r["Close"] <= target) if below else (r["Close"] >= target):
                return ts.time()
        return None

    s["high_revert"] = revert(hi_i, s["high"] * (1 - REVERT_PCT / 100), True)
    s["low_revert"] = revert(lo_i, s["low"] * (1 + REVERT_PCT / 100), False)
    return s


def _gap_minutes(a: time, b: time) -> int:
    return _minute(b) - _minute(a)


def draw(code: str, name: str, days: dict, summaries: dict, out: Path, interval: str) -> Path:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib import font_manager

    for fam in ["Noto Sans CJK JP", "IPAexGothic", "IPAGothic", "WenQuanYi Zen Hei"]:
        if any(f.name == fam for f in font_manager.fontManager.ttflist):
            plt.rcParams["font.family"] = fam
            break
    plt.rcParams["axes.unicode_minus"] = False

    dates = sorted(days)
    n = len(dates)
    W = SLOTS + 1
    fig, (ax, bx) = plt.subplots(2, 1, figsize=(max(14, n * 1.0 + 4), 17),
                                 gridspec_kw={"height_ratios": [1.05, 1]})
    ups = sum(1 for d in dates if summaries[d]["change_pct"] > 0)
    downs = sum(1 for d in dates if summaries[d]["change_pct"] < 0)
    fig.suptitle(
        f"{code} {name} — 寄り〜10:30（{interval}足・{n}営業日） {dates[0]:%Y-%m-%d}〜{dates[-1]:%Y-%m-%d}"
        f"／寄り→10:30 上昇{ups}日・下落{downs}日／描画は終値ベース",
        x=0.01, ha="left", fontsize=14, y=0.995)

    # ---------- 上段 ----------
    px, py = [], []
    for i, d in enumerate(dates):
        b, s = days[d], summaries[d]
        x0 = i * W
        ax.axvspan(x0 - 0.5, x0 + SLOTS - 0.5, color="#fde4e4" if s["change_pct"] >= 0 else "#e4ecfb", lw=0)
        ax.axvline(x0 + _minute(DECIDE) - 0.5, color="#999", lw=0.7, ls="--")
        xs = [x0 + _minute(ts.time()) for ts in b.index]
        px += xs
        py += list(b["Close"].values)
        ax.hlines(s["high"], x0, x0 + SLOTS - 1, colors=UP_RED, linestyles="--", lw=1)
        ax.hlines(s["low"], x0, x0 + SLOTS - 1, colors=LOW_GREEN, linestyles="--", lw=1)
        ax.plot(x0 + _minute(s["high_time"]), s["high"], "o", color=UP_RED, ms=3)
        ax.plot(x0 + _minute(s["low_time"]), s["low"], "o", color=LOW_GREEN, ms=3)
        ax.text(x0 + 2, s["high"], f"{s['high']:,.0f} ({_hm(s['high_time'])})", color=UP_RED,
                fontsize=7.5, va="bottom")
        ax.text(x0 + 2, s["low"], f"{s['low']:,.0f} ({_hm(s['low_time'])})", color=LOW_GREEN,
                fontsize=7.5, va="top")
        if s["hi10"] is not None:
            a = x0 + _minute(DECIDE)
            ax.hlines([s["hi10"]], a, x0 + SLOTS - 1, colors=UP_RED, linestyles=":", lw=0.9)
            ax.hlines([s["lo10"]], a, x0 + SLOTS - 1, colors=LOW_GREEN, linestyles=":", lw=0.9)
    ax.plot(px, py, color="#1f2f7a", lw=1.1)

    hi_d = max(dates, key=lambda d: summaries[d]["high"])
    lo_d = min(dates, key=lambda d: summaries[d]["low"])
    for d, key, col, lab, dy in [(hi_d, "high", UP_RED, "期間高", 1), (lo_d, "low", LOW_GREEN, "期間安", -1)]:
        s = summaries[d]
        x = dates.index(d) * W + _minute(s[f"{key}_time"])
        span = max(summaries[k]["high"] for k in dates) - min(summaries[k]["low"] for k in dates)
        ax.annotate(f"{lab} {s[key]:,.0f}（{d:%m/%d} {_hm(s[key + '_time'])}）", (x, s[key]),
                    xytext=(x + W * 1.2 if x < n * W / 2 else x - W * 3, s[key] + dy * span * 0.04),
                    color=col, fontsize=11, fontweight="bold",
                    arrowprops=dict(arrowstyle="->", color=col))

    ax.set_xlim(-1, n * W - 1)
    ax.set_xticks([i * W + SLOTS / 2 for i in range(n)])
    labels = []
    for d in dates:
        s = summaries[d]
        gap = f"窓{s['gap_pct']:+.1f}%" if s["gap_pct"] is not None else ""
        brk = s["brk"] + (f"({s['brk_first']}先)" if s.get("brk_first") else "") \
            + (f" {_hm(s['brk_time'])}" if s["brk_time"] else "")
        labels.append(f"{d:%m/%d}\n{s['change_pct']:+.1f}%\n{gap}\n{brk}")
    ax.set_xticklabels(labels, fontsize=8.5)
    for lab, d in zip(ax.get_xticklabels(), dates):
        lab.set_color(UP_RED if summaries[d]["change_pct"] >= 0 else DOWN_BLUE)
    ax.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: f"{v:,.0f}"))
    ax.grid(axis="y", color="#bbb", lw=0.5)

    # ---------- 下段 ----------
    for i, d in enumerate(dates):
        b, s = days[d], summaries[d]
        bx.axvspan(i, i + 1, color="#fde4e4" if s["change_pct"] >= 0 else "#e4ecfb", lw=0)
        bx.axvline(i, color="white", lw=2)
        rng = (s["high"] - s["low"]) or 1.0

        def X(p, i=i, s=s, rng=rng):
            return i + 0.1 + 0.8 * (p - s["low"]) / rng

        ys = [_minute(ts.time()) for ts in b.index]
        bx.plot([X(p) for p in b["Close"].values], ys, color="#8090c0", lw=0.8, alpha=0.8)
        if s["hi10"] is not None:
            bx.plot([X(s["lo10"]), X(s["hi10"])], [_minute(DECIDE)] * 2, color="#555", lw=2.2, alpha=0.6)

        for key, col, mk, below in [("high", UP_RED, "^", True), ("low", LOW_GREEN, "v", False)]:
            t0 = s[f"{key}_time"]
            y0 = _minute(t0)
            bx.plot(X(s[key]), y0, mk, color=col, ms=8)
            ha = "right" if key == "high" else "left"
            bx.text(X(s[key]) + (-0.03 if key == "high" else 0.03), y0 - 1.2,
                    f"{_hm(t0)}\n{s[key]:,.0f}", color=col, fontsize=7.5, fontweight="bold", ha=ha, va="bottom")
            t1 = s[f"{key}_revert"]
            if t1 is None:
                bx.text(X(s[key]), y0 + 2.5, "未", color="#666", fontsize=7.5, ha="center", va="top")
                continue
            seg = b[[t0 <= ts.time() <= t1 for ts in b.index]]
            bx.plot([X(s[key])] + [X(p) for p in seg["Close"].values[1:]],
                    [y0] + [_minute(ts.time()) for ts in seg.index[1:]], color=col, lw=2.2)
            y1 = _minute(t1)
            bx.plot(X(float(seg["Close"].iloc[-1])), y1, "o", mfc="white", mec=col, ms=6, mew=1.5)
            bx.text(X(float(seg["Close"].iloc[-1])), y1 + 1.2, f"{_hm(t1)} (+{_gap_minutes(t0, t1)}分)",
                    color="#333", fontsize=7, ha="center", va="top")

    bx.axhline(_minute(DECIDE), color="#999", lw=0.8, ls="--")
    for m in (30, 60):
        bx.axhline(m, color="#bbb", lw=0.6, ls=":")
    bx.set_xlim(0, n)
    bx.set_ylim(SLOTS + 1, -3)
    bx.set_yticks([0, 10, 30, 60, 89])
    bx.set_yticklabels(["9:00", "9:10", "9:30", "10:00", "10:29"])
    bx.set_xticks([i + 0.5 for i in range(n)])
    bx.set_xticklabels([f"{d:%m/%d}" for d in dates], fontsize=8.5)
    bx.set_ylabel("時刻")

    note = (
        "上段：赤/緑の破線＝その日の高値/安値（時刻）　点線＝最初の10分（9:00〜9:09）の高値/安値　縦の破線＝9:10　"
        "背景の赤/青＝寄り→10:30 で上げ/下げ\n"
        "日付の下：寄り→10:30 の騰落／窓（前日終値→寄り）／9:10以降に最初の10分の値幅を上抜け・下抜け・両抜けした最初の時刻、内側＝抜けず\n"
        f"下段：縦＝時刻（下ほど遅い）・横＝その日の安値〜高値で正規化　▲高値 ▼安値　太線＝極値から終値で{REVERT_PCT:.0f}%戻すまで、"
        "○＝戻した足（かっこ内＝極値からの分数）　未＝10:30までに戻さず　灰色の横棒＝最初の10分の値幅\n"
        f"出典: Yahoo {interval}足（寄りの 9:00 台の足が欠ける日がある）　作成 {datetime.now(JST):%Y-%m-%d %H:%M}"
    )
    fig.text(0.01, 0.005, note, fontsize=8.5, color="#444", va="bottom")
    fig.tight_layout(rect=(0, 0.06, 1, 0.98))
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=110)
    plt.close(fig)
    return out


def _load_saved(code: str) -> pd.DataFrame:
    from collect_minutes import load_minutes
    m = load_minutes()
    m = m[m["code"] == code]
    if m.empty:
        return pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"])
    idx = pd.to_datetime(m["date"].dt.strftime("%Y-%m-%d") + " " + m["time"]).dt.tz_localize(JST)
    return pd.DataFrame({"Open": m["open"].values, "High": m["high"].values, "Low": m["low"].values,
                         "Close": m["close"].values, "Volume": m["volume"].values}, index=pd.DatetimeIndex(idx))


def prev_closes(daily: pd.DataFrame) -> dict:
    """日足から「その日の前日終値」を日付ごとに引けるようにする（純粋関数）。"""
    if daily is None or daily.empty:
        return {}
    x = daily.dropna(subset=["Close"])
    if getattr(x.index, "tz", None) is not None:
        x = x.tz_convert(JST)
    ds = [ts.date() for ts in x.index]
    cs = list(x["Close"].values)
    return {ds[i]: float(cs[i - 1]) for i in range(1, len(ds))}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("code", nargs="?", default="5803")
    ap.add_argument("--days", type=int, default=20)
    ap.add_argument("--interval", default="1m", choices=["1m", "2m", "5m"])
    ap.add_argument("--from-minutes", action="store_true", help="data/minutes/ の保存分を使う（ネットに出ない）")
    a = ap.parse_args(argv)

    from stock_screen import SCREEN_JP
    names = {c: n for c, _, n in SCREEN_JP}
    name = names.get(a.code, "")

    pcs = {}
    if a.from_minutes:
        df, interval = _load_saved(a.code), "1m"
    else:
        from yahoo_chart import fetch_chart, fetch_intraday
        interval = a.interval
        cal = int(a.days * 1.5) + 8
        df = fetch_intraday(f"{a.code}.T", interval, days=min(cal, 29 if interval == "1m" else 59), chunk_days=7)
        now = datetime.now(timezone.utc)
        try:
            pcs = prev_closes(fetch_chart(f"{a.code}.T", "1d", now - timedelta(days=cal + 15), now))
        except Exception as e:
            print(f"⚠️ 日足が取れず（窓は出さない）: {e}")

    days = split_days(df)
    today = datetime.now(JST)
    if today.date() in days and today.time() < time(10, 30):
        days.pop(today.date())           # 場中の未完成の日は描かない
    keep = sorted(days)[-a.days:]
    days = {d: days[d] for d in keep}
    if not days:
        print("❌ 分足が取れなかった")
        return 1
    summaries = {d: summarize_day(days[d], pcs.get(d)) for d in keep}
    out = draw(a.code, name, days, summaries, OUT_DIR / f"{a.code}_morning.png", interval)
    print(f"✅ {out}（{len(keep)}日: {keep[0]}〜{keep[-1]}）")
    return 0


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    sys.exit(main())
