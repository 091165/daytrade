"""
銘柄ごとのページ（docs/stock/<コード>.html）

トレード中に見るページなので、上から「今日の作戦（図）→ 上値・下値の目安 → 値幅 → 寄り前予測 → 注意」の順に、
その銘柄の情報を1か所にまとめる。図は SVG をこの場で組み立てる（外部ライブラリなし）。

  図1：直近の日足に、上値・下値の目安の線と、今日の9時台にふつう動く幅（前日終値の上下）を重ねる
  図2：作戦の物差し。入った値段を0として、損切り・利確の位置と、9時台にふつう動く幅を並べる
"""

from __future__ import annotations

from html import escape
from pathlib import Path

from dashboard import (
    _COMMON_CSS,
    _RULES_FOCUS_CSS,
    _focus_chip,
    _focus_validation_foot,
    render_range_block,
    render_warnings_banner,
)

CHART_DAYS = 10
W, H = 430, 400                 # 図1の大きさ（viewBox）。スマホ幅で縮んでも文字が読める大きさに
PLOT_L, PLOT_R = 6, 236         # ローソク足の範囲（x）
BAND_L, BAND_R = 242, 270       # 「今日」の帯
LABEL_X = 276                   # 水準の名前
PLOT_T, PLOT_B = 18, 380


def _fmt(x: float) -> str:
    return f"{x:,.0f}"


def _spread_labels(ys: list[float], gap: float = 13.0) -> list[float]:
    """近すぎるラベルを上下にずらす（順番は保つ）"""
    if not ys:
        return []
    order = sorted(range(len(ys)), key=lambda i: ys[i])
    out = list(ys)
    for a, b in zip(order, order[1:]):
        if out[b] - out[a] < gap:
            out[b] = out[a] + gap
    return out


def chart_svg(records: list[dict] | None, plan: dict | None, rng: dict | None) -> str:
    """図1：日足＋上値・下値の目安＋今日の9時台の幅（前日終値の上下）"""
    if not records or not plan:
        return '<div class="muted">日足のデータがないため、図を出せません</div>'
    as_of = (rng or {}).get("as_of")
    bars = [r for r in records if not as_of or r["date"] <= as_of][-CHART_DAYS:]
    if len(bars) < 2:
        return '<div class="muted">日足のデータが足りません</div>'
    pc = plan.get("prev_close") or bars[-1]["close"]
    b = plan.get("bracket") or {}
    half = (b.get("morning_width") or 0) / 2
    lv = (plan.get("levels") or {})
    levels = [dict(x, side="up") for x in lv.get("up", [])] + [dict(x, side="down") for x in lv.get("down", [])]
    # 縦の範囲は「前日終値の上下・目安の線・今日の帯」が見える幅に合わせる（はみ出したローソク足は切る）
    reach = max([abs(x["price"] - pc) for x in levels] + [half, (rng or {}).get("pred_yen") or 0, pc * 0.01])
    lo, hi = pc - reach * 1.12, pc + reach * 1.12
    # 直近5日の足は切らずに全部見えるようにする
    lo = min([lo] + [r["low"] for r in bars[-5:]])
    hi = max([hi] + [r["high"] for r in bars[-5:]])

    def y(p: float) -> float:
        return PLOT_T + (hi - p) / (hi - lo) * (PLOT_B - PLOT_T)

    n = len(bars)
    step = (PLOT_R - PLOT_L) / n
    body = max(2.0, step * 0.6)
    parts = [f'<clipPath id="plot"><rect x="0" y="{PLOT_T}" width="{BAND_R}" height="{PLOT_B - PLOT_T}"/></clipPath>']
    # 目盛り（4本）
    for k in range(5):
        p = lo + (hi - lo) * k / 4
        parts.append(f'<line x1="{PLOT_L}" x2="{BAND_R}" y1="{y(p):.1f}" y2="{y(p):.1f}" class="grid"/>')
    # ローソク足（範囲の外は切る）
    parts.append('<g clip-path="url(#plot)">')
    for i, r in enumerate(bars):
        cx = PLOT_L + step * (i + 0.5)
        up = r["close"] >= r["open"]
        cls = "c-up" if up else "c-down"
        top, bot = y(max(r["open"], r["close"])), y(min(r["open"], r["close"]))
        parts.append(f'<line x1="{cx:.1f}" x2="{cx:.1f}" y1="{y(r["high"]):.1f}" y2="{y(r["low"]):.1f}" class="{cls}-w"/>')
        parts.append(f'<rect x="{cx - body / 2:.1f}" y="{top:.1f}" width="{body:.1f}" height="{max(1.0, bot - top):.1f}" class="{cls}"/>')
    parts.append("</g>")
    # 日付（最初と最後）
    parts.append(f'<text x="{PLOT_L}" y="{H - 4}" class="t-muted">{bars[0]["date"][5:].replace("-", "/")}</text>')
    parts.append(f'<text x="{PLOT_R}" y="{H - 4}" class="t-muted" text-anchor="end">{bars[-1]["date"][5:].replace("-", "/")}</text>')
    # 今日の帯（9時台にふつう動く幅を、前日終値の上下に）
    if half:
        parts.append(f'<rect x="{BAND_L}" y="{y(pc + half):.1f}" width="{BAND_R - BAND_L}" height="{y(pc - half) - y(pc + half):.1f}" class="band"/>')
        parts.append(f'<text x="{(BAND_L + BAND_R) / 2}" y="{PLOT_T - 6}" class="t-muted" text-anchor="middle">今日</text>')
    # 前日終値
    parts.append(f'<line x1="{PLOT_L}" x2="{BAND_R}" y1="{y(pc):.1f}" y2="{y(pc):.1f}" class="pc"/>')
    # 水準の線とラベル
    items = sorted(levels, key=lambda x: -x["price"])
    label_y = _spread_labels([y(x["price"]) + 4 for x in items] + [y(pc) + 4])
    for x, ly in zip(items, label_y):
        cls = "lv-up" if x["side"] == "up" else "lv-down"
        parts.append(f'<line x1="{PLOT_L}" x2="{BAND_R}" y1="{y(x["price"]):.1f}" y2="{y(x["price"]):.1f}" class="{cls}"/>')
        parts.append(f'<text x="{LABEL_X}" y="{ly:.1f}" class="{cls}-t">{_fmt(x["price"])} {escape(x["kind"])}</text>')
    parts.append(f'<text x="{LABEL_X}" y="{label_y[-1]:.1f}" class="pc-t">{_fmt(pc)} 前日終値</text>')
    return (f'<svg viewBox="0 0 {W} {H}" class="chart" role="img" aria-label="日足と上値・下値の目安">'
            + "".join(parts) + "</svg>")


def ruler_svg(plan: dict | None, side_hint: str) -> str:
    """図2：作戦の物差し。入った値段を0として、損切り・利確と、9時台にふつう動く幅"""
    b = (plan or {}).get("bracket")
    if not b:
        return ""
    s, t, w = b["stop_width"], b["target_width"], b["morning_width"]
    Wd = 360
    L, R = 16, Wd - 16
    sides = {"buy": [("buy", "買いで入ったとき")], "sell": [("sell", "売りで入ったとき")]}.get(
        side_hint, [("buy", "買いで入ったとき"), ("sell", "売りで入ったとき")])
    rows = []
    yy = 22
    for kind, label in sides:
        sg = 1 if kind == "buy" else -1
        # 損切り側と利確側を左右に。0（入った値段）の位置は 損切り : 利確 の比で決める
        total = s + t
        x0 = L + (R - L) * (s / total) if sg > 0 else L + (R - L) * (t / total)
        xs, xt = (L, R) if sg > 0 else (R, L)
        rows.append(f'<text x="{L}" y="{yy}" class="t-label">{label}</text>')
        bar_y = yy + 30
        rows.append(f'<line x1="{x0:.1f}" x2="{xs:.1f}" y1="{bar_y}" y2="{bar_y}" class="r-stop"/>')
        rows.append(f'<line x1="{x0:.1f}" x2="{xt:.1f}" y1="{bar_y}" y2="{bar_y}" class="r-take"/>')
        rows.append(f'<circle cx="{x0:.1f}" cy="{bar_y}" r="6" class="r-entry"/>')
        rows.append(f'<text x="{x0:.1f}" y="{bar_y - 12}" class="t-entry" text-anchor="middle">▼入った値段</text>')
        rows.append(f'<text x="{xs:.1f}" y="{bar_y + 24}" class="r-stop-t" text-anchor="{"start" if sg > 0 else "end"}">'
                    f'損切り {"-" if sg > 0 else "+"}{s:.0f}円</text>')
        rows.append(f'<text x="{xt:.1f}" y="{bar_y + 24}" class="r-take-t" text-anchor="{"end" if sg > 0 else "start"}">'
                    f'利確 {"+" if sg > 0 else "-"}{t:.0f}円</text>')
        yy = bar_y + 48
    # 9時台にふつう動く幅（同じ縮尺の帯）
    scale = (R - L) / (s + t)
    bw = min(R - L, w * scale)
    rows.append(f'<rect x="{L}" y="{yy - 4}" width="{bw:.1f}" height="12" class="band"/>')
    rows.append(f'<text x="{L}" y="{yy + 26}" class="t-muted">黄色＝9時台にふつう動く幅 約{w:.0f}円（同じ縮尺）</text>')
    h = yy + 36
    return f'<svg viewBox="0 0 {Wd} {h}" class="chart ruler" role="img" aria-label="損切り・利確の物差し">' + "".join(rows) + "</svg>"


def _level_rows(plan: dict) -> str:
    lv = plan.get("levels") or {}
    rows = ""
    for side, label in (("up", "上値"), ("down", "下値")):
        for x in lv.get(side, []):
            rows += (f'<tr><td class="{"lv-up-t" if side == "up" else "lv-down-t"}">{label}</td><td>{escape(x["kind"])}</td>'
                     f'<td class="num">{_fmt(x["price"])}円</td><td class="num">{x["dist"]:+,.0f}円</td>'
                     f'<td class="num">{x["dist"] * 100:+,.0f}円</td></tr>')
    return rows or '<tr><td colspan="5" class="muted">目安の範囲に水準なし</td></tr>'


_PAGE_CSS = """
.back { color: var(--accent); text-decoration: none; font-size: 13px; }
.box { background: var(--panel); border: 1px solid var(--border); border-radius: 10px; padding: 14px; margin-bottom: 14px; }
.box h2 { font-size: 15px; color: var(--accent); margin: 0 0 8px; }
.chart { width: 100%; height: auto; display: block; }
.chart text { font-size: 13px; fill: var(--text); }
.chart .t-muted { fill: var(--muted); font-size: 11px; }
.ruler text { font-size: 14px; }
.ruler .t-label { fill: var(--text); font-weight: 600; }
.ruler .t-entry { fill: var(--muted); font-size: 12px; }
.ruler .t-muted { font-size: 12px; }
.chart .grid { stroke: var(--border); stroke-width: 1; }
.chart .c-up { fill: var(--long); } .chart .c-up-w { stroke: var(--long); stroke-width: 1; }
.chart .c-down { fill: var(--short); } .chart .c-down-w { stroke: var(--short); stroke-width: 1; }
.chart .band { fill: rgba(251, 191, 36, 0.18); stroke: rgba(251, 191, 36, 0.6); stroke-width: 1; }
.chart .pc { stroke: var(--text); stroke-width: 1.5; }
.chart .pc-t { fill: var(--text); font-weight: 600; }
.chart .lv-up { stroke: #f87171; stroke-width: 1; stroke-dasharray: 4 3; }
.chart .lv-up-t { fill: #f87171; }
.chart .lv-down { stroke: #34d399; stroke-width: 1; stroke-dasharray: 4 3; }
.chart .lv-down-t { fill: #34d399; }
.chart .r-stop { stroke: var(--short); stroke-width: 10; }
.chart .r-take { stroke: var(--long); stroke-width: 10; }
.chart .r-entry { fill: var(--text); }
.chart .r-stop-t { fill: var(--short); } .chart .r-take-t { fill: var(--long); }
table.lv { width: 100%; border-collapse: collapse; font-size: 12px; }
table.lv td, table.lv th { padding: 4px 4px; border-bottom: 1px solid var(--border); text-align: left; white-space: nowrap; }
table.lv .num { text-align: right; }
.lv-up-t { color: #f87171; } .lv-down-t { color: #34d399; }
.muted { color: var(--muted); }
.big { font-size: 18px; font-weight: 700; }
.big .stop { color: var(--short); } .big .take { color: var(--long); }
.note { font-size: 13px; margin-top: 6px; }
.note.muted { color: var(--muted); }
.dir { font-weight: 700; }
.dir.bullish { color: var(--long); } .dir.bearish { color: var(--short); } .dir.neutral { color: var(--muted); }
"""


def render_stock_page(f: dict, records: list[dict] | None, direction: dict, warnings: list[dict],
                      trading_rules: dict | None, generated_at: str) -> str:
    plan = f.get("plan") or {}
    b = plan.get("bracket") or {}
    verdict = direction.get("verdict")
    side_hint = {"buy_bias": "buy", "sell_bias": "sell"}.get(verdict, "both")
    d = f["direction"]
    dir_txt = {"bullish": "▲ 買い寄り", "bearish": "▼ 売り寄り"}.get(d, "― 方向感なし")
    trial = '<span class="focus-trial">試用</span>' if f.get("trial") else ""
    big = ""
    if b:
        big = (f'<div class="big"><span class="stop">損切り -{b["stop_yen"]:,.0f}円</span>（{b["stop_width"]:.0f}円幅）　'
               f'<span class="take">利確 +{b["target_yen"]:,.0f}円</span>（{b["target_width"]:.0f}円幅）</div>'
               + "".join(f'<div class="note">⚠️ {escape(n)}</div>' for n in b["notes"]))
    side_txt = {"buy": "今日の方針は買い。買いで入るときの図", "sell": "今日の方針は売り。売りで入るときの図"}.get(
        side_hint, "今日は方向なしの日。入るなら両方の形")
    chips = "".join(_focus_chip(t, v) for t, v in f.get("us_changes", {}).items()) or '<span class="focus-chip">データなし</span>'
    notes = "".join(f'<div class="note {n.get("level", "")}">・{escape(n["text"])}</div>' for n in f.get("notes") or [])
    rules = ""
    if trading_rules:
        items = trading_rules.get("day_rules", []) + trading_rules.get("always_rules", [])
        rules = "".join(f"<li>{escape(r)}</li>" for r in items)
    warn = render_warnings_banner(warnings) if warnings else ""
    conflict = f'<div class="note">⚠️ {escape(f["conflict_text"])}</div>' if f.get("conflict_with_market") else ""
    return f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{escape(f["name"])}（{f["code"]}）｜Daytrade Signal Board</title>
<style>
{_COMMON_CSS}
{_RULES_FOCUS_CSS}
{_PAGE_CSS}
.warnings-wrap {{ display: flex; flex-direction: column; gap: 10px; }}
.warning-item {{ background: var(--panel); border-left: 4px solid var(--accent); border-radius: 6px; padding: 10px 14px; }}
.warning-message {{ font-size: 13px; margin: 4px 0; }}
.warning-action {{ font-size: 12px; color: var(--muted); }}
.warning-evidence {{ font-size: 11px; color: var(--muted); margin-top: 6px; }}
</style>
</head>
<body>
<div class="container" style="max-width: 760px;">
  <header>
    <h1>{escape(f["name"])} <span class="focus-code">{f["code"]}</span>{trial}</h1>
    <div class="header-right"><a class="back" href="../index.html">← 今日の判定に戻る</a><div class="timestamp">{generated_at}</div></div>
  </header>

  <div class="box">
    <h2>🎯 今日の作戦（100株・入る前に決める）</h2>
    <div class="note">今日の方針：<b>{escape(direction.get("verdict_label", ""))}</b>　この銘柄の寄り前予測：<span class="dir {d}">{dir_txt}</span></div>
    {conflict}
    {big}
    <div class="note muted">{side_txt}。入った値段（寄り値など）を0として見る。</div>
    {ruler_svg(plan, side_hint)}
  </div>

  <div class="box">
    <h2>📈 上値・下値の目安（直近{CHART_DAYS}日の日足）</h2>
    {chart_svg(records, plan, f.get("range"))}
    <div class="note muted">黄色の帯＝今日の9時台にふつう動く幅を、前日終値の上下に置いたもの。赤い点線＝上値の目安、緑の点線＝下値の目安。</div>
    <table class="lv"><thead><tr><th></th><th>水準</th><th class="num">値段</th><th class="num">終値から</th><th class="num">100株で</th></tr></thead>
      <tbody>{_level_rows(plan)}</tbody></table>
    <div class="note muted">{escape(plan.get("level_evidence", ""))}</div>
  </div>

  <div class="box">
    <h2>📏 値幅</h2>
    {render_range_block(f.get("range"))}
  </div>

  <div class="box">
    <h2>🇺🇸 寄り前予測（米関連株）</h2>
    <div class="note">米関連株の平均 {f["us_avg_change_pct"]:+.2f}%（{escape(f["us_label"])}）</div>
    <div class="focus-chips">{chips}</div>
    <div class="note"><b>寄り（窓）</b>　{escape(f["gap_text"])}</div>
    <div class="note"><b>寄り後（〜引け）</b>　{escape(f["intraday_text"])}</div>
    <div class="note muted">{_focus_validation_foot(f.get("validation"))}</div>
  </div>

  <div class="box">
    <h2>📝 この銘柄の注意</h2>
    {notes or '<div class="muted">特になし</div>'}
  </div>

  {('<div class="box"><h2>⚠️ 今日の警告（全体）</h2>' + warn + '</div>') if warn else ""}

  <div class="box">
    <h2>📌 今日のマイルール</h2>
    <ul style="margin: 0; padding-left: 20px; font-size: 13px;">{rules}</ul>
  </div>
</div>
</body>
</html>
"""


def write_stock_pages(out_dir: Path, focus_signals: list[dict], ohlc_records: dict[str, list[dict]],
                      direction: dict, warnings: list[dict], trading_rules: dict | None, generated_at: str) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for f in focus_signals:
        p = out_dir / f"{f['code']}.html"
        p.write_text(render_stock_page(f, ohlc_records.get(f["code"]), direction, warnings, trading_rules, generated_at),
                     encoding="utf-8")
        paths.append(p)
    return paths
