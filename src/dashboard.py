"""
警告バナー + 日経方針 + 為替コモディティ + セクター連動 + 履歴 のHTMLダッシュボード

v6変更点:
  - キリバン水準セクションを削除
  - 直近5営業日の判定履歴を表示
  - セクターカードに validation 結果（実測r値）を反映
  - validation_verdict='contradicted' のセクターは折りたたみ表示
  - エラーページ生成関数を追加
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path


def format_jpy(n: float) -> str:
    return f"¥{n:,.0f}"


# ----- 共通CSS -----

_COMMON_CSS = """
:root {
  --bg: #0b1220;
  --panel: #151f33;
  --panel-2: #1d2842;
  --text: #e6edf3;
  --muted: #8ca0b3;
  --accent: #fbbf24;
  --long: #10b981;
  --short: #ef4444;
  --neutral: #6b7280;
  --border: #2a3654;
}
* { box-sizing: border-box; }
body {
  margin: 0;
  font-family: -apple-system, "Segoe UI", "Hiragino Sans", "Yu Gothic", sans-serif;
  background: var(--bg);
  color: var(--text);
  line-height: 1.6;
}
.container { max-width: 1280px; margin: 0 auto; padding: 24px; }
header { display: flex; justify-content: space-between; align-items: baseline; margin-bottom: 24px; border-bottom: 1px solid var(--border); padding-bottom: 16px; flex-wrap: wrap; gap: 8px; }
h1 { margin: 0; font-size: 20px; font-weight: 600; }
.header-right { text-align: right; }
.timestamp { color: var(--muted); font-size: 13px; }
.report-link { color: var(--accent); text-decoration: none; font-size: 13px; }
.report-link:hover { text-decoration: underline; }

section { margin-bottom: 28px; }
section > h2.section-title {
  font-size: 16px; font-weight: 600; color: var(--accent);
  margin: 0 0 14px 0; padding-bottom: 6px; border-bottom: 1px solid var(--border);
  letter-spacing: 0.02em;
}
.footer-note {
  margin-top: 32px; padding: 16px; background: var(--panel);
  border-radius: 8px; font-size: 12px; color: var(--muted); border: 1px solid var(--border);
}
"""


# ----- 警告バナー -----

def render_warnings_banner(warnings: list[dict]) -> str:
    if not warnings:
        return ""

    items = ""
    for w in warnings:
        sev = w["severity"]
        if sev == "high":
            border_color = "var(--short)"
            bg_color = "rgba(239, 68, 68, 0.08)"
            badge_color = "var(--short)"
        elif sev == "medium":
            border_color = "var(--accent)"
            bg_color = "rgba(251, 191, 36, 0.08)"
            badge_color = "var(--accent)"
        else:
            border_color = "var(--muted)"
            bg_color = "rgba(107, 114, 128, 0.08)"
            badge_color = "var(--muted)"

        items += f"""
        <div class="warning-item" style="border-left-color: {border_color}; background: {bg_color};">
          <div style="font-size: 15px; margin-bottom: 4px; color: {badge_color}; font-weight: 600;">{w['label']}</div>
          <div class="warning-message">{w['message']}</div>
          <div class="warning-action">→ {w['action']}</div>
        </div>
        """
    return f"""
    <section>
      <div class="warnings-wrap">
        {items}
      </div>
    </section>
    """


# ----- マイルール連動バナー -----

_RULES_FOCUS_CSS = """
.rules-banner { border-radius: 12px; padding: 18px 20px; border: 2px solid var(--accent); background: rgba(251, 191, 36, 0.07); }
.rules-banner.tone-danger { border-color: var(--short); background: rgba(239, 68, 68, 0.08); }
.rules-kicker { font-size: 12px; color: var(--muted); letter-spacing: 0.08em; margin-bottom: 6px; }
.rules-headline { font-size: 22px; font-weight: 700; line-height: 1.35; margin-bottom: 6px; }
.tone-danger .rules-headline { color: #fca5a5; }
.tone-caution .rules-headline { color: var(--accent); }
.rules-subline { font-size: 13px; color: var(--muted); margin-bottom: 12px; }
.rules-cols { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }
.rules-col h3 { margin: 0 0 6px 0; font-size: 12px; color: var(--muted); font-weight: 600; letter-spacing: 0.05em; }
.rules-col ul { margin: 0; padding-left: 18px; }
.rules-col li { margin: 4px 0; font-size: 14px; }
.rules-col.day li { font-weight: 600; }
@media (max-width: 720px) {
  .container { padding: 16px; }
  .rules-cols { grid-template-columns: 1fr; }
  .rules-headline { font-size: 19px; }
}

.focus-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }
@media (max-width: 720px) { .focus-grid { grid-template-columns: 1fr; } }
.focus-card { background: var(--panel); border: 1px solid var(--border); border-radius: 10px; padding: 16px; }
.focus-card.conflict { border-color: var(--short); }
.focus-head { display: flex; justify-content: space-between; align-items: baseline; gap: 8px; margin-bottom: 8px; }
.focus-name { font-size: 17px; font-weight: 700; }
.focus-code { color: var(--muted); font-size: 12px; margin-left: 4px; font-weight: 400; }
.focus-dir { font-size: 15px; font-weight: 700; white-space: nowrap; }
.focus-dir.bullish { color: var(--long); }
.focus-dir.bearish { color: var(--short); }
.focus-dir.neutral { color: var(--muted); }
.focus-us { font-size: 12px; color: var(--muted); margin-bottom: 10px; }
.focus-chips { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 10px; }
.focus-chip { background: var(--panel-2); border-radius: 4px; padding: 2px 8px; font-size: 12px; font-variant-numeric: tabular-nums; }
.focus-line { font-size: 14px; margin: 4px 0; }
.focus-line .lbl { display: inline-block; min-width: 4.5em; color: var(--muted); font-size: 12px; }
.focus-conflict { margin-top: 10px; padding: 8px 10px; border-radius: 6px; background: rgba(239, 68, 68, 0.12); color: #fca5a5; font-size: 13px; font-weight: 600; }
.focus-foot { margin-top: 10px; font-size: 11px; color: var(--muted); }
.focus-range { margin-top: 12px; padding: 10px 12px; border-radius: 8px; background: var(--panel-2); }
.focus-range-title { font-size: 12px; color: var(--muted); }
.focus-range-main { font-size: 20px; font-weight: 700; font-variant-numeric: tabular-nums; }
.focus-range-main .lot { font-size: 13px; font-weight: 500; color: var(--muted); margin-left: 4px; }
.focus-range-sub { font-size: 13px; margin-top: 2px; }
.focus-range-sub.high { color: #fca5a5; font-weight: 600; }
.focus-range-sub.low { color: var(--muted); }

/* スマホで履歴の表がページ全体をはみ出さないように、表だけ横スクロールさせる */
@media (max-width: 720px) {
  .history-table { display: block; overflow-x: auto; white-space: nowrap; -webkit-overflow-scrolling: touch; }
}
"""


def render_trading_rules_banner(rules: dict | None) -> str:
    """その日の判定に応じたマイルールをページ最上部に大きく表示する。"""
    if not rules:
        return ""
    day_items = "".join(f"<li>{r}</li>" for r in rules.get("day_rules", []))
    always_items = "".join(f"<li>{r}</li>" for r in rules.get("always_rules", []))
    tone = rules.get("tone", "caution")
    return f"""
    <section>
      <div class="rules-banner tone-{tone}">
        <div class="rules-kicker">今日のマイルール</div>
        <div class="rules-headline">{rules.get("headline", "")}</div>
        <div class="rules-subline">{rules.get("subline", "")}</div>
        <div class="rules-cols">
          <div class="rules-col day">
            <h3>今日のタイプのルール</h3>
            <ul>{day_items}</ul>
          </div>
          <div class="rules-col">
            <h3>毎日のルール</h3>
            <ul>{always_items}</ul>
          </div>
        </div>
      </div>
    </section>
    """


# ----- 承認銘柄（フジクラ・SBG）の個別予測 -----

def _fmt_r(x: float | None) -> str:
    return "—" if x is None else f"{x:+.2f}"


def _focus_validation_foot(v: dict | None) -> str:
    if not v:
        return "検証: まだ（毎週日曜のバックテスト後に、窓・寄り後それぞれの連動の強さを表示）"
    return (
        f"検証（{v.get('n', 0)}日）: 窓 r={_fmt_r(v.get('gap_r'))} {v.get('gap_label', '')}"
        f" ／ 寄り後 r={_fmt_r(v.get('intraday_r'))} {v.get('intraday_label', '')}"
    )


def _focus_chip(ticker: str, pct: float) -> str:
    color = "var(--long)" if pct >= 0 else "var(--short)"
    return f'<span class="focus-chip">{ticker} <span style="color: {color};">{pct:+.1f}%</span></span>'


def _frac_text(frac: float | None) -> str:
    if frac is None:
        return "—"
    if frac >= 1:
        return f"値幅の約{frac:.1f}倍"
    return f"値幅の約{max(1, round(frac * 10))}割"


def render_range_block(rg: dict | None) -> str:
    """想定値幅のブロック（フジクラ・SBGカードの中）"""
    if not rg:
        return ""
    if not rg.get("available"):
        return f'<div class="focus-range"><div class="focus-range-title">今日の値幅の目安</div><div class="focus-range-sub low">{rg.get("reason", "データなし")}</div></div>'
    rough = (
        f'<div class="focus-range-sub">荒れた日（5日に1日）は約{rg["rough_yen"]:,.0f}円まで'
        f'（100株で約{rg["rough_yen"] * rg["shares"]:,.0f}円）</div>'
        if rg.get("rough_yen") else ""
    )
    level = (
        f'<div class="focus-range-sub {rg["level"]}">{rg["level_text"]}</div>'
        if rg.get("level_text") else ""
    )
    v = rg.get("validation")
    if v and v.get("r") is not None:
        foot = (f'検証（{v["n"]}日）: 直近{v["window"]}日の平均で翌日の値幅を予測 r={v["r"]:+.2f}'
                f'／予測以内に収まった日 {v["within_rate"] * 100:.0f}%')
    else:
        foot = "検証: まだ（毎週日曜のバックテスト後に、当たり具合を表示）"
    return f"""
          <div class="focus-range">
            <div class="focus-range-title">今日の値幅の目安（高値−安値・直近{rg["window"]}日平均）</div>
            <div class="focus-range-main">約{rg["pred_yen"]:,.0f}円<span class="lot">100株で約{rg["lot_yen"]:,.0f}円</span></div>
            {rough}
            <div class="focus-range-sub">損切り-{rg["stop_yen"]:,}円（{rg["stop_width"]:.0f}円幅）は{_frac_text(rg.get("stop_frac"))}</div>
            {level}
            <div class="focus-foot">{foot}</div>
          </div>"""


def render_focus_section(focus_signals: list[dict] | None) -> str:
    if not focus_signals:
        return ""
    cards = ""
    for f in focus_signals:
        d = f["direction"]
        strong = "（強）" if f["strength"] == "strong" else ""
        if d == "bullish":
            dir_html = "▲ 買い寄り" + strong
        elif d == "bearish":
            dir_html = "▼ 売り寄り" + strong
        else:
            dir_html = "― 方向感なし"
        chips = "".join(_focus_chip(t, v) for t, v in f.get("us_changes", {}).items())
        if not chips:
            chips = '<span class="focus-chip">データなし</span>'
        is_conflict = bool(f.get("conflict_with_market"))
        conflict = f'<div class="focus-conflict">⚠️ {f["conflict_text"]}</div>' if is_conflict else ""
        card_class = "focus-card conflict" if is_conflict else "focus-card"
        cards += f"""
        <div class="{card_class}">
          <div class="focus-head">
            <div class="focus-name">{f["name"]}<span class="focus-code">{f["code"]}</span></div>
            <div class="focus-dir {d}">{dir_html}</div>
          </div>
          <div class="focus-us">米関連株の平均 {f["us_avg_change_pct"]:+.2f}%（{f["us_label"]}）</div>
          <div class="focus-chips">{chips}</div>
          <div class="focus-line"><span class="lbl">寄り（窓）</span>{f["gap_text"]}</div>
          <div class="focus-line"><span class="lbl">寄り後</span>{f["intraday_text"]}</div>
          {conflict}
          {render_range_block(f.get("range"))}
          <div class="focus-foot">{_focus_validation_foot(f.get("validation"))}</div>
        </div>
        """
    return f"""
    <section>
      <h2 class="section-title">🎯 承認銘柄の寄り前予測（フジクラ・SBG）</h2>
      <div class="focus-grid">
        {cards}
      </div>
    </section>
    """


# ----- 注目銘柄（手出し無用日のセクター連動） -----

def render_stock_picks_section(picks_result: dict) -> str:
    """指数判定が hands_off の日のみ、強連動セクターから注目銘柄を表示。"""
    if not picks_result.get("available"):
        return ""

    picks = picks_result.get("picks", [])

    # 該当なしの場合: 「休む日」メッセージのみ
    if not picks:
        return f"""
        <section>
          <h2 class="section-title">💡 個別銘柄の方向感（指数手出し無用日）</h2>
          <div class="picks-empty">
            <div style="font-size: 14px; color: var(--muted);">
              本日は指数もセクターも明確な方向感なし。
              <b style="color: var(--text);">無理にトレードせず、休む or スイング玉のリスク管理日に。</b>
            </div>
          </div>
        </section>
        """

    cards_html = ""
    for p in picks:
        if p["direction"] == "bullish":
            color_class = "pick-bullish"
            arrow = "▲"
            badge_class = "pick-badge-bullish"
        else:
            color_class = "pick-bearish"
            arrow = "▼"
            badge_class = "pick-badge-bearish"

        # 銘柄リスト（コード付き）
        stock_items = ""
        for name, ticker in zip(p["stocks"], p["tickers"]):
            # 8035.T → 8035 として表示
            code = ticker.replace(".T", "")
            stock_items += f'<li><b>{name}</b> <span class="muted tiny">({code})</span></li>'

        cards_html += f"""
        <div class="pick-card {color_class}">
          <div class="pick-header">
            <div class="pick-title">
              {arrow} <b>{p['sector_name']}</b>
            </div>
            <span class="pick-badge {badge_class}">{p['direction_label']}</span>
          </div>
          <ul class="pick-stocks">{stock_items}</ul>
          <div class="pick-rationale">{p['rationale']}</div>
        </div>
        """

    return f"""
    <section>
      <h2 class="section-title">💡 個別銘柄の方向感（指数手出し無用日）</h2>
      <div class="picks-grid">
        {cards_html}
      </div>
      <div class="picks-disclaimer">
        ⚠️ これは推奨ではなく、過去の連動データに基づく <b>該当銘柄の参考表示</b> です。
        個別銘柄の決算・材料・需給は別途確認してください。指数全体は方向感がない日のため、
        ポジションサイズは控えめにし、損切りラインを明確に。
      </div>
    </section>
    """


# ----- 日経方針 -----

def render_direction_verdict(direction_signal: dict) -> str:
    verdict = direction_signal["verdict"]
    label = direction_signal["verdict_label"]
    confidence = direction_signal["confidence"]

    if verdict == "buy_bias":
        bg = "rgba(16, 185, 129, 0.15)"
        color = "var(--long)"
        icon = "📈"
    elif verdict == "sell_bias":
        bg = "rgba(239, 68, 68, 0.15)"
        color = "var(--short)"
        icon = "📉"
    else:
        bg = "rgba(107, 114, 128, 0.15)"
        color = "var(--neutral)"
        icon = "⏸️"

    conf_text = {"high": "確度:高", "medium": "確度:中", "low": "確度:低"}.get(confidence, "")

    return f"""
    <div style="background: {bg}; color: {color}; padding: 16px 20px; border-radius: 12px; display: inline-flex; align-items: center; gap: 12px;">
      <div style="font-size: 32px;">{icon}</div>
      <div>
        <div style="font-size: 24px; font-weight: 700;">{label}</div>
        <div style="font-size: 12px; opacity: 0.8; margin-top: 2px;">{conf_text}</div>
      </div>
    </div>
    """


def render_us_indices_row(indices: list[dict]) -> str:
    items = ""
    for idx in indices:
        pct = idx["change_pct"]
        if idx["direction"] == "bullish":
            color = "var(--long)"
        elif idx["direction"] == "bearish":
            color = "var(--short)"
        else:
            color = "var(--muted)"
        items += f"""
        <div class="us-idx-item">
          <div class="us-idx-name">{idx['name']}</div>
          <div class="us-idx-pct" style="color: {color};">{pct:+.2f}%</div>
        </div>
        """
    return items


# ----- 為替・コモディティ -----

def render_market_context(ctx: dict) -> str:
    items_html = ""
    for item in ctx["items"]:
        direction = item["direction"]
        if direction == "up":
            color = "var(--long)"
            arrow = "▲"
        elif direction == "down":
            color = "var(--short)"
            arrow = "▼"
        else:
            color = "var(--muted)"
            arrow = "━"

        if item["name"] == "ドル円":
            price_str = f"{item['current']:.2f}"
        elif item["name"] == "ゴールド":
            price_str = f"${item['current']:,.1f}"
        else:
            price_str = f"${item['current']:,.2f}"

        items_html += f"""
        <div class="fx-item">
          <div class="fx-header">
            <span class="fx-name">{item['name']}</span>
            <span class="fx-symbol">{item['symbol']}</span>
          </div>
          <div class="fx-price-row">
            <span class="fx-price">{price_str}</span>
            <span class="fx-change" style="color: {color};">{arrow} {item['change_pct']:+.2f}%</span>
          </div>
          <div class="fx-interpretation">{item['interpretation']}</div>
        </div>
        """

    combined_html = ""
    if ctx.get("combined_note"):
        combined_html = f"""
        <div class="fx-combined">
          {ctx['combined_note']}
        </div>
        """

    return f"""
    <section>
      <h2 class="section-title">💱 参考: 為替・コモディティ</h2>
      <div class="fx-grid">
        {items_html}
      </div>
      {combined_html}
    </section>
    """


# ----- セクターカード -----

def _validation_badge(sig: dict) -> str:
    """バックテストの検証結果バッジ"""
    v = sig.get("validation_verdict")
    r = sig.get("validation_pearson_r")
    if not v:
        # バックテスト未実行の場合は何も出さない
        return ""

    if v == "confirmed":
        return f'<span class="vbadge vbadge-confirmed" title="実測r={r:+.2f}">✓ 検証済</span>'
    if v == "weak":
        return f'<span class="vbadge vbadge-weak" title="実測r={r:+.2f}">△ 弱連動</span>'
    if v == "contradicted":
        return f'<span class="vbadge vbadge-contradicted" title="実測r={r:+.2f}">✗ 連動低</span>'
    return ""


def render_sector_card(sig: dict) -> str:
    direction = sig["direction"]
    strength = sig["signal_strength"]
    avg = sig["us_avg_change_pct"]

    if direction == "bullish":
        bar_class = "bar-bullish"
        dir_class = "dir-bullish"
        dir_text = "買い" if strength == "strong" else "やや買い"
        arrow = "▲"
    elif direction == "bearish":
        bar_class = "bar-bearish"
        dir_class = "dir-bearish"
        dir_text = "売り" if strength == "strong" else "やや売り"
        arrow = "▼"
    else:
        bar_class = "bar-neutral"
        dir_class = "dir-neutral"
        dir_text = "様子見"
        arrow = "━"

    bar_width = min(abs(avg) / 3.0 * 100, 100)
    bar_offset = 50 if direction == "bullish" else (50 - bar_width if direction == "bearish" else 50)
    bar_display_width = bar_width if direction != "neutral" else 0

    jp_stocks_text = "、".join(sig["jp_stocks"][:4])
    validation_html = _validation_badge(sig)

    return f"""
    <div class="sector-card">
      <div class="sector-header">
        <div class="sector-title">
          {arrow} <b>{sig['sector_name']}</b> {validation_html}
        </div>
        <div class="sector-avg {dir_class}">
          {avg:+.2f}%
        </div>
      </div>
      <div class="sector-bar-container">
        <div class="sector-bar-center"></div>
        <div class="sector-bar {bar_class}" style="width: {bar_display_width}%; left: {bar_offset}%;"></div>
      </div>
      <div class="sector-detail">
        <div class="sector-us-label">米: {sig['us_label']}</div>
        <div class="sector-jp-stocks">日: {jp_stocks_text}</div>
        <div class="sector-action">→ <b class="{dir_class}">{dir_text}目線</b></div>
      </div>
      <div class="sector-note">{sig['note']}</div>
    </div>
    """


def render_sectors_section(sector_signals: list[dict]) -> str:
    """セクターを表示。validation_verdict='contradicted' のものは折りたたむ。"""
    primary = []
    contradicted = []
    for s in sector_signals:
        if s.get("validation_verdict") == "contradicted":
            contradicted.append(s)
        else:
            primary.append(s)

    primary_cards = "".join(render_sector_card(s) for s in primary)

    contradicted_html = ""
    if contradicted:
        c_cards = "".join(render_sector_card(s) for s in contradicted)
        contradicted_html = f"""
        <details class="contradicted-details">
          <summary>連動性の低いセクター ({len(contradicted)}件) を表示</summary>
          <div class="sector-grid" style="margin-top: 12px;">
            {c_cards}
          </div>
          <div style="font-size: 12px; color: var(--muted); margin-top: 10px;">
            これらは過去のバックテストで連動が確認できなかったセクター。実運用上、判断材料としての信頼度は低い。
          </div>
        </details>
        """

    return f"""
    <section>
      <h2 class="section-title">🔁 米→日セクター連動予測</h2>
      <div class="sector-grid">
        {primary_cards}
      </div>
      {contradicted_html}
      <div class="rationale" style="margin-top: 16px;">
        ✓検証済 = バックテストで連動が確認されたセクター。
        判定: ±1.5%以上=強シグナル / ±0.5〜1.5%=弱シグナル / それ未満=様子見。
      </div>
    </section>
    """


# ----- 履歴表示 -----

def render_history_section(recent_history: list[dict]) -> str:
    if len(recent_history) <= 1:
        # 初回起動などで履歴が今日分のみ → 表示しない
        return ""

    def _verdict_badge(v: str, label: str) -> str:
        if v == "buy_bias":
            return f'<span class="hist-badge hist-buy">📈 {label}</span>'
        if v == "sell_bias":
            return f'<span class="hist-badge hist-sell">📉 {label}</span>'
        return f'<span class="hist-badge hist-handsoff">⏸️ {label}</span>'

    rows = ""
    for h in recent_history:
        date_str = h.get("date", "")
        # 曜日を計算
        try:
            d = datetime.strptime(date_str, "%Y-%m-%d")
            weekday_jp = "月火水木金土日"[d.weekday()]
            display_date = f"{d.month}/{d.day}({weekday_jp})"
        except ValueError:
            display_date = date_str

        verdict = h.get("verdict", "hands_off")
        label = h.get("verdict_label", "")
        confidence = h.get("confidence", "low")
        nasdaq = h.get("nasdaq_pct", 0)
        dow = h.get("dow_pct", 0)
        sox = h.get("sox_pct", 0)
        diff = h.get("futures_diff", 0)

        conf_label = {"high": "高", "medium": "中", "low": "低"}.get(confidence, "")

        rows += f"""
        <tr>
          <td>{display_date}</td>
          <td>{_verdict_badge(verdict, label)}</td>
          <td class="num"><span style="color: var(--muted);">{conf_label}</span></td>
          <td class="num">{nasdaq:+.2f}%</td>
          <td class="num">{dow:+.2f}%</td>
          <td class="num">{sox:+.2f}%</td>
          <td class="num">{diff:+,.0f}</td>
        </tr>
        """

    return f"""
    <section>
      <h2 class="section-title">📅 直近の判定履歴</h2>
      <table class="history-table">
        <thead>
          <tr>
            <th>日付</th>
            <th>判定</th>
            <th class="num">確度</th>
            <th class="num">NQ</th>
            <th class="num">DOW</th>
            <th class="num">SOX</th>
            <th class="num">現対(円)</th>
          </tr>
        </thead>
        <tbody>{rows}</tbody>
      </table>
    </section>
    """


# ----- メイン: ダッシュボード描画 -----

def render_dashboard(
    warnings: list[dict],
    market_context: dict,
    direction_signal: dict,
    market: dict,
    sector_signals: list[dict],
    recent_history: list[dict],
    stock_picks: dict,
    output_path: Path,
    trading_rules: dict | None = None,
    focus_signals: list[dict] | None = None,
) -> None:
    """HTMLダッシュボードを生成してファイルに書き出す。"""
    direction_verdict_html = render_direction_verdict(direction_signal)
    us_indices_html = render_us_indices_row(direction_signal["us_indices"])
    reasons_html = "".join(f"<li>{r}</li>" for r in direction_signal["reasons"])

    warnings_html = render_warnings_banner(warnings)
    market_context_html = render_market_context(market_context)
    sectors_html = render_sectors_section(sector_signals)
    history_html = render_history_section(recent_history)
    stock_picks_html = render_stock_picks_section(stock_picks)
    rules_html = render_trading_rules_banner(trading_rules)
    focus_html = render_focus_section(focus_signals)

    futures_diff = direction_signal["futures_diff"]
    futures_diff_color = (
        "var(--long)" if futures_diff >= 200
        else ("var(--short)" if futures_diff <= -200 else "var(--muted)")
    )

    html = f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Daytrade Signal Board</title>
<style>
{_COMMON_CSS}
{_RULES_FOCUS_CSS}

/* 警告バナー */
.warnings-wrap {{ display: flex; flex-direction: column; gap: 10px; }}
.warning-item {{
  background: var(--panel);
  border-left: 4px solid var(--accent);
  border-radius: 0 10px 10px 0;
  padding: 12px 16px;
}}
.warning-message {{ font-size: 13px; color: var(--text); margin: 4px 0; }}
.warning-action {{ font-size: 12px; color: var(--muted); margin-top: 6px; }}

/* 日経方針 */
.direction-card {{ background: var(--panel); border: 1px solid var(--border); border-radius: 12px; padding: 24px; }}
.direction-meta {{ margin-top: 16px; display: flex; gap: 20px; flex-wrap: wrap; }}
.direction-meta-item {{ flex: 1; min-width: 200px; }}
.direction-meta-label {{ font-size: 11px; color: var(--muted); text-transform: uppercase; letter-spacing: 0.1em; }}
.us-indices {{ display: flex; gap: 16px; margin-top: 6px; }}
.us-idx-item {{ flex: 1; text-align: center; padding: 8px; background: var(--panel-2); border-radius: 6px; }}
.us-idx-name {{ font-size: 11px; color: var(--muted); }}
.us-idx-pct {{ font-size: 16px; font-weight: 700; font-variant-numeric: tabular-nums; margin-top: 2px; }}
.futures-value {{ font-size: 22px; font-weight: 700; font-variant-numeric: tabular-nums; margin-top: 6px; }}
.sub {{ color: var(--muted); font-size: 14px; }}
.reasons-list {{ margin-top: 16px; padding: 14px 16px; background: var(--panel-2); border-left: 3px solid var(--accent); border-radius: 0 8px 8px 0; }}
.reasons-list ul {{ margin: 0; padding-left: 18px; font-size: 13px; }}
.reasons-list li {{ margin: 4px 0; }}
.time-nodes-note {{
  margin-top: 12px; padding: 10px 14px; background: var(--panel-2);
  border-radius: 8px; font-size: 12px; color: var(--muted); border: 1px dashed var(--border);
}}
.time-nodes-note b {{ color: var(--accent); }}

/* 為替・コモディティ */
.fx-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 12px; }}
.fx-item {{ background: var(--panel); border: 1px solid var(--border); border-radius: 10px; padding: 14px 16px; }}
.fx-header {{ display: flex; justify-content: space-between; align-items: baseline; }}
.fx-name {{ font-weight: 600; font-size: 14px; }}
.fx-symbol {{ font-size: 11px; color: var(--muted); }}
.fx-price-row {{ display: flex; justify-content: space-between; align-items: baseline; margin: 6px 0; }}
.fx-price {{ font-size: 20px; font-weight: 700; font-variant-numeric: tabular-nums; }}
.fx-change {{ font-size: 14px; font-weight: 600; font-variant-numeric: tabular-nums; }}
.fx-interpretation {{ font-size: 12px; color: var(--muted); margin-top: 6px; border-top: 1px dashed var(--border); padding-top: 6px; }}
.fx-combined {{
  margin-top: 12px; padding: 10px 14px;
  background: rgba(251, 191, 36, 0.1);
  border-left: 3px solid var(--accent); border-radius: 0 8px 8px 0;
  font-size: 13px;
}}

/* セクター */
.sector-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); gap: 14px; }}
.sector-card {{ background: var(--panel); border: 1px solid var(--border); border-radius: 10px; padding: 14px 16px; }}
.sector-header {{ display: flex; justify-content: space-between; align-items: baseline; margin-bottom: 8px; }}
.sector-title {{ font-size: 15px; }}
.sector-avg {{ font-size: 18px; font-weight: 700; font-variant-numeric: tabular-nums; }}
.dir-bullish {{ color: var(--long); }}
.dir-bearish {{ color: var(--short); }}
.dir-neutral {{ color: var(--neutral); }}
.sector-bar-container {{ position: relative; height: 6px; background: var(--panel-2); border-radius: 3px; margin: 8px 0 12px 0; overflow: hidden; }}
.sector-bar-center {{ position: absolute; left: 50%; top: 0; bottom: 0; width: 1px; background: var(--border); }}
.sector-bar {{ position: absolute; top: 0; bottom: 0; border-radius: 3px; }}
.bar-bullish {{ background: var(--long); }}
.bar-bearish {{ background: var(--short); }}
.bar-neutral {{ background: var(--neutral); }}
.sector-detail {{ font-size: 12px; color: var(--muted); line-height: 1.7; }}
.sector-detail .sector-jp-stocks {{ color: var(--text); }}
.sector-detail .sector-action {{ margin-top: 4px; font-size: 13px; color: var(--text); }}
.sector-note {{ margin-top: 8px; padding-top: 8px; border-top: 1px dashed var(--border); font-size: 11px; color: var(--muted); line-height: 1.5; }}

.vbadge {{ display: inline-block; padding: 1px 7px; border-radius: 4px; font-size: 10px; font-weight: 600; margin-left: 4px; cursor: help; }}
.vbadge-confirmed {{ background: rgba(16, 185, 129, 0.18); color: var(--long); }}
.vbadge-weak {{ background: rgba(251, 191, 36, 0.18); color: var(--accent); }}
.vbadge-contradicted {{ background: rgba(239, 68, 68, 0.18); color: var(--short); }}

.contradicted-details {{
  margin-top: 14px;
  background: var(--panel);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 10px 14px;
}}
.contradicted-details summary {{
  cursor: pointer; color: var(--muted); font-size: 13px;
  list-style: none;
}}
.contradicted-details summary::before {{ content: "▸ "; }}
.contradicted-details[open] summary::before {{ content: "▾ "; }}

.rationale {{
  background: var(--panel-2);
  border-left: 3px solid var(--accent);
  padding: 12px 16px;
  border-radius: 0 8px 8px 0;
  margin-top: 12px;
  font-size: 13px;
}}

/* 注目銘柄（手出し無用日） */
.picks-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 14px; }}
.pick-card {{
  background: var(--panel);
  border: 1px solid var(--border);
  border-left: 4px solid var(--accent);
  border-radius: 0 10px 10px 0;
  padding: 14px 16px;
}}
.pick-card.pick-bullish {{ border-left-color: var(--long); }}
.pick-card.pick-bearish {{ border-left-color: var(--short); }}
.pick-header {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px; }}
.pick-title {{ font-size: 15px; }}
.pick-badge {{ display: inline-block; padding: 3px 10px; border-radius: 999px; font-size: 12px; font-weight: 600; }}
.pick-badge-bullish {{ background: rgba(16, 185, 129, 0.15); color: var(--long); }}
.pick-badge-bearish {{ background: rgba(239, 68, 68, 0.15); color: var(--short); }}
.pick-stocks {{ list-style: none; padding: 0; margin: 0 0 10px 0; }}
.pick-stocks li {{ padding: 4px 0; font-size: 14px; border-bottom: 1px dashed var(--border); }}
.pick-stocks li:last-child {{ border-bottom: none; }}
.pick-stocks .muted {{ color: var(--muted); }}
.pick-stocks .tiny {{ font-size: 11px; }}
.pick-rationale {{ font-size: 12px; color: var(--muted); border-top: 1px solid var(--border); padding-top: 8px; margin-top: 6px; }}
.picks-empty {{
  background: var(--panel);
  border: 1px dashed var(--border);
  border-radius: 10px;
  padding: 20px;
  text-align: center;
}}
.picks-disclaimer {{
  margin-top: 12px;
  padding: 10px 14px;
  background: rgba(251, 191, 36, 0.05);
  border-left: 3px solid var(--accent);
  border-radius: 0 8px 8px 0;
  font-size: 12px;
  color: var(--muted);
}}
.picks-disclaimer b {{ color: var(--text); }}

/* 履歴 */
.history-table {{
  width: 100%;
  border-collapse: collapse;
  background: var(--panel);
  border: 1px solid var(--border);
  border-radius: 10px;
  overflow: hidden;
  font-size: 13px;
}}
.history-table th {{
  background: var(--panel-2); color: var(--muted);
  font-weight: 500; text-align: left; padding: 10px;
  font-size: 11px; text-transform: uppercase; letter-spacing: 0.05em;
}}
.history-table td {{ padding: 10px; border-top: 1px solid var(--border); }}
.history-table tr:first-child td {{ border-top: none; }}
.history-table .num {{ text-align: right; font-variant-numeric: tabular-nums; }}
.hist-badge {{ display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 12px; font-weight: 600; }}
.hist-buy {{ background: rgba(16, 185, 129, 0.15); color: var(--long); }}
.hist-sell {{ background: rgba(239, 68, 68, 0.15); color: var(--short); }}
.hist-handsoff {{ background: rgba(107, 114, 128, 0.15); color: var(--neutral); }}
</style>
</head>
<body>
<div class="container">
  <header>
    <h1>📊 Daytrade Signal Board</h1>
    <div class="header-right">
      <div class="timestamp">Updated: {datetime.fromisoformat(market['timestamp']).strftime('%Y-%m-%d %H:%M JST')}</div>
      <a href="./backtest_report.html" class="report-link">🧪 バックテスト検証レポート →</a>
    </div>
  </header>

  {rules_html}

  {warnings_html}

  <!-- 本日の日経デイトレ方針 -->
  <section>
    <div class="direction-card">
      <h2 style="margin: 0 0 14px 0; font-size: 14px; font-weight: 500; color: var(--muted); text-transform: uppercase; letter-spacing: 0.1em;">
        本日の日経デイトレ方針
      </h2>
      {direction_verdict_html}

      <div class="direction-meta">
        <div class="direction-meta-item">
          <div class="direction-meta-label">前日米国市場</div>
          <div class="us-indices">
            {us_indices_html}
          </div>
        </div>
        <div class="direction-meta-item">
          <div class="direction-meta-label">日経先物 現対</div>
          <div class="futures-value" style="color: {futures_diff_color};">{futures_diff:+,.0f}円</div>
          <div class="sub" style="font-size: 12px;">
            先物: {format_jpy(market['nikkei_futures']['price'])} / 現物終値: {format_jpy(market['nikkei']['prev_close'])}
          </div>
        </div>
      </div>

      <div class="reasons-list">
        <ul>{reasons_html}</ul>
      </div>

      <div class="time-nodes-note">
        💡 <b>時間節目</b>: 9:40・10:00（前場トレ転）／12:30（後場寄り）／14:00・14:30（後場トレ転）
        で方向転換・手仕舞いが発生しやすい。ただし相場に大変動あるほど時間節目も貫通する点に留意。
      </div>
    </div>
  </section>

  {focus_html}

  {market_context_html}

  {stock_picks_html}

  {sectors_html}

  {history_html}

  <div class="footer-note">
    <b>⚠️ ディスクレーマー</b>:
    これは個人研究用のシグナルボードです。投資判断は自己責任で。
    全ての判定はPDFサロン記事から抽出した経験則に基づく仮説であり、
    統計的有意性は自分でバックテストレポートで検証してください。
  </div>
</div>
</body>
</html>"""

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html, encoding="utf-8")
    print(f"Dashboard written to {output_path}")


def render_error_page(output_path: Path, error_message: str) -> None:
    """データ取得失敗時のエラーページを生成する。

    実運用では「シグナルが出ない＝何かおかしい」を明示的に示すことで誤判断を防ぐ。
    """
    now = datetime.now().strftime('%Y-%m-%d %H:%M')
    html = f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>⚠️ Daytrade Signal Board - エラー</title>
<style>
{_COMMON_CSS}
.error-card {{
  background: var(--panel);
  border: 1px solid var(--short);
  border-radius: 12px;
  padding: 32px;
  text-align: center;
}}
.error-icon {{ font-size: 48px; margin-bottom: 12px; }}
.error-title {{ color: var(--short); font-size: 22px; font-weight: 700; margin-bottom: 16px; }}
.error-msg {{
  background: var(--panel-2); padding: 12px 16px; border-radius: 8px;
  font-family: monospace; font-size: 13px; color: var(--muted);
  text-align: left; margin: 16px 0;
}}
.error-action {{ color: var(--text); font-size: 14px; margin-top: 16px; }}
</style>
</head>
<body>
<div class="container">
  <header>
    <h1>📊 Daytrade Signal Board</h1>
    <div class="timestamp">Updated: {now}</div>
  </header>

  <div class="error-card">
    <div class="error-icon">⚠️</div>
    <div class="error-title">本日のシグナルは生成できませんでした</div>
    <div class="error-msg">{error_message}</div>
    <div class="error-action">
      データ取得に失敗したため、本日の判定は出していません。<br>
      ブラウザで直接 yfinance や使用中の証券会社のサイトで市場状況を確認してください。
    </div>
  </div>
</div>
</body>
</html>"""

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html, encoding="utf-8")
    print(f"⚠️ Error page written to {output_path}")
