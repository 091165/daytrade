"""
バックテスト結果のHTML/JSONレポート生成

2種類の検証結果をまとめて1つのレポートに出力:
  1. 米→日セクター連動マッピングの検証（相関・p値・ヒット率）
  2. 日経デイトレ方針判定ロジックの検証（判定別の勝率・騰落率）
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from backtest import PairValidationResult
from direction_backtest import DirectionBacktestResult


JST = ZoneInfo("Asia/Tokyo")


# ----- セクター検証セクション用ヘルパー -----

def _verdict_class(verdict: str) -> str:
    return {
        "confirmed": "verdict-confirmed",
        "weak": "verdict-weak",
        "contradicted": "verdict-contradicted",
        "insufficient_data": "verdict-neutral",
    }.get(verdict, "verdict-neutral")


def _format_hit_rate(rate: float | None, n_days: int) -> str:
    if rate is None or n_days == 0:
        return '<span class="muted">—</span>'
    pct = rate * 100
    color = "var(--long)" if pct >= 60 else ("var(--short)" if pct < 40 else "var(--text)")
    return f'<span style="color: {color};">{pct:.1f}%</span><span class="muted"> (n={n_days})</span>'


def _format_r(r: float) -> str:
    if r >= 0.4:
        color = "var(--long)"
    elif r >= 0.2:
        color = "var(--accent)"
    elif r > 0:
        color = "var(--muted)"
    else:
        color = "var(--short)"
    return f'<b style="color: {color};">{r:+.3f}</b>'


def _format_p(p: float) -> str:
    if p < 0.001:
        return '<b style="color: var(--long);">&lt; 0.001</b>'
    if p < 0.05:
        return f'<b style="color: var(--long);">{p:.3f}</b>'
    if p < 0.1:
        return f'<span style="color: var(--accent);">{p:.3f}</span>'
    return f'<span class="muted">{p:.3f}</span>'


# ----- 日経方針検証セクション用ヘルパー -----

def _format_pct(pct: float) -> str:
    if pct > 0:
        return f'<span style="color: var(--long);">+{pct:.2f}%</span>'
    if pct < 0:
        return f'<span style="color: var(--short);">{pct:.2f}%</span>'
    return f'<span class="muted">{pct:.2f}%</span>'


def _format_win_rate(rate: float, n: int) -> str:
    pct = rate * 100
    color = "var(--long)" if pct >= 55 else ("var(--short)" if pct < 45 else "var(--text)")
    return f'<span style="color: {color};">{pct:.1f}%</span> <span class="muted">({n}日)</span>'


def _verdict_badge(verdict: str, label: str) -> str:
    if verdict == "buy_bias":
        return f'<span class="dir-badge dir-buy">📈 {label}</span>'
    if verdict == "sell_bias":
        return f'<span class="dir-badge dir-sell">📉 {label}</span>'
    return f'<span class="dir-badge dir-hands-off">⏸️ {label}</span>'


# ----- 個別銘柄（フジクラ・SBG）検証セクション -----

def _fmt_r_opt(r: float | None) -> str:
    return '<span class="muted">—</span>' if r is None else _format_r(r)


def _fmt_p_opt(p: float | None) -> str:
    return '<span class="muted">—</span>' if p is None else _format_p(p)


def _fmt_bucket(b: dict, up: bool) -> str:
    if not b or b.get("n", 0) == 0:
        return '<span class="muted">該当日なし</span>'
    return (
        f'<span class="muted">n={b["n"]}</span><br>'
        f'窓 {_format_pct(b["mean_gap"])}（同方向 {b["gap_hit"]*100:.0f}%）<br>'
        f'寄り後 {_format_pct(b["mean_intraday"])}（同方向 {b["intraday_hit"]*100:.0f}%）'
    )


def _fmt_proxy(ticker: str, v: dict) -> str:
    g = "" if v.get("gap_r") is None else f"{v['gap_r']:+.2f}"
    i = "" if v.get("intraday_r") is None else f"{v['intraday_r']:+.2f}"
    return f"{ticker}(窓{g}/寄後{i})"


_LINK_CLASS = {
    "confirmed": "verdict-confirmed", "weak": "verdict-weak",
    "none": "verdict-contradicted", "reverse": "verdict-contradicted", "unknown": "verdict-neutral",
}


def render_focus_section(focus_results: list[dict] | None, focus_period: str) -> str:
    if not focus_results:
        return """
  <section class="section-box">
    <h2 class="section-title">🎯 承認銘柄・試用銘柄の個別予測の検証</h2>
    <div class="muted" style="padding: 16px;">まだ検証結果がありません（データ取得失敗、または未実行）。</div>
  </section>
"""
    rows = ""
    for r in focus_results:
        proxies = ", ".join(
            _fmt_proxy(t, v) for t, v in r.get("per_proxy", {}).items()
        ) or "—"
        rows += f"""
          <tr>
            <td><b>{r['name']}</b><div class="muted tiny">{r['code']}</div></td>
            <td class="num">{r['n']}</td>
            <td class="num">{_fmt_r_opt(r['gap_r'])}<br>{_fmt_p_opt(r['gap_p'])}</td>
            <td><span class="verdict {_LINK_CLASS[r['gap_link']]}">{r['gap_label']}</span></td>
            <td class="num">{_fmt_r_opt(r['intraday_r'])}<br>{_fmt_p_opt(r['intraday_p'])}</td>
            <td><span class="verdict {_LINK_CLASS[r['intraday_link']]}">{r['intraday_label']}</span></td>
            <td class="tiny">{_fmt_bucket(r.get('us_up'), True)}</td>
            <td class="tiny">{_fmt_bucket(r.get('us_down'), False)}</td>
            <td class="tiny muted">{proxies}</td>
          </tr>
        """
    return f"""
  <section class="section-box">
    <h2 class="section-title">🎯 承認銘柄・試用銘柄の個別予測の検証</h2>
    <div class="period-note">検証期間: {focus_period}　／　米関連株の前日騰落率（平均） vs 翌日の日本株</div>
    <table class="results">
      <thead>
        <tr>
          <th>銘柄</th>
          <th class="num">n</th>
          <th class="num">窓<br>r / p</th>
          <th>窓の判定</th>
          <th class="num">寄り後<br>r / p</th>
          <th>寄り後の判定</th>
          <th>米が+1%以上の日</th>
          <th>米が-1%以下の日</th>
          <th>ティッカー別 r</th>
        </tr>
      </thead>
      <tbody>
        {rows}
      </tbody>
    </table>
    <div class="explainer">
      <h3>📖 読み方</h3>
      <ul>
        <li><b>窓</b>: 前日終値 → 寄り付き。米国の動きは主にここに出る。デイトレでは取れない部分。</li>
        <li><b>寄り後</b>: 寄り付き → 引け。デイトレで実際に取れる部分。ここの連動が弱ければ「寄りの方向は読めても、寄り後は読めない」。</li>
        <li><b>逆に動きやすい</b>: 米が上げた翌日、寄り後は下げやすい（寄り天型）。寄りで飛びつかない根拠になる。</li>
      </ul>
    </div>
  </section>
"""


# ----- 米国株 × 日本株 連動スキャン（研究用） -----

def _q_txt(q: float | None) -> str:
    if q is None:
        return "q=—"
    return "q&lt;0.001" if q < 0.001 else f"q={q:.3f}"


def _scan_item(p: dict, kind: str, q_th: float) -> str:
    r = p[f"{kind}_r"]
    q = p.get(f"{kind}_q")
    sig = q is not None and q < q_th
    mark = "✅" if sig else ""
    return (f'<li><b>{p["us"]}</b> <span class="muted tiny">{p["us_name"]}</span> '
            f'{_format_r(r)} <span class="muted tiny">{_q_txt(q)}</span> {mark}</li>')


def render_scan_section(scan: dict | None) -> str:
    if not scan:
        return """
  <section class="section-box">
    <h2 class="section-title">🔬 米国株 × 日本株 連動スキャン（研究用）</h2>
    <div class="muted" style="padding: 16px;">まだ結果がありません（データ取得失敗、または未実行）。</div>
  </section>
"""
    sm = scan["summary"]
    if sm["intraday_hits"]:
        hits = "".join(
            f'<li><b>{h["jp_name"]}</b> × <b>{h["us"]}</b>（{h["us_name"]}）: '
            f'寄り後 {_format_r(h["intraday_r"])} {_q_txt(h["intraday_q"])} ／ 窓 {_format_r(h["gap_r"])}'
            f'{"　<span class=muted>→ 米が上げると寄り後は下げやすい（寄り天型）</span>" if h["intraday_r"] < 0 else ""}</li>'
            for h in sm["intraday_hits"]
        )
        hits_html = f'<ul class="scan-hits">{hits}</ul>'
    else:
        hits_html = ('<div class="muted">なし。どの組み合わせも、補正後は寄り後の連動が確認できなかった'
                     '（＝米国の動きは寄り付きでほぼ織り込まれる）。</div>')

    cards = ""
    for j in sm["by_jp"]:
        if not j["top_gap"]:
            cards += f'<div class="scan-card"><b>{j["name"]}</b> <span class="muted tiny">{j["code"]}</span><div class="muted tiny">データ不足</div></div>'
            continue
        gap_li = "".join(_scan_item(p, "gap", sm["q_threshold"]) for p in j["top_gap"])
        intra_li = "".join(_scan_item(p, "intraday", sm["q_threshold"]) for p in j["top_intraday"])
        cards += f"""
        <div class="scan-card">
          <div><b>{j["name"]}</b> <span class="muted tiny">{j["code"]} ／ n={j["n"]}</span></div>
          <div class="scan-sub">窓（寄り付き）で連動 トップ3</div>
          <ul>{gap_li}</ul>
          <div class="scan-sub">寄り後の相関が大きい順（参考）</div>
          <ul>{intra_li}</ul>
        </div>"""

    return f"""
  <section class="section-box">
    <h2 class="section-title">🔬 米国株 × 日本株 連動スキャン（研究用）</h2>
    <div class="period-note">
      検証期間: {scan["period"]}　／　日本株{len(scan["jp_universe"])}銘柄 × 米国株{len(scan["us_universe"])}銘柄 = {sm["n_pairs"]}組
      ／　窓で有意: {sm["n_gap_significant"]}組　／　寄り後で有意: <b>{sm["n_intraday_significant"]}組</b>
      （補正前の p&lt;0.05 は {sm["n_intraday_raw_p05"]}組）
    </div>
    <div class="scan-highlight">
      <h3>⭐ 寄り後まで連動する組み合わせ（多重検定補正後 q&lt;{sm["q_threshold"]}）</h3>
      {hits_html}
    </div>
    <div class="scan-grid">{cards}</div>
    <div class="explainer">
      <h3>📖 読み方</h3>
      <ul>
        <li><b>r</b>: 米国株の前日騰落率と、翌日の日本株の値動きの相関（1に近いほど同じ向きに動く）。</li>
        <li><b>q</b>: たくさんの組み合わせを同時に調べたことを補正したp値。<b>q&lt;{sm["q_threshold"]}（✅）だけが「偶然とは言いにくい」</b>。
          補正前のp&lt;0.05は、数百組調べると偶然でも十数組出る。</li>
        <li>✅ が付いても、1回の検証だけで信じない。<b>数週間続けて残るか</b>を見てから使う。</li>
        <li><b>窓</b>: 前日終値→寄り付き。米国の動きはほぼここに出る。デイトレでは取れない。</li>
        <li><b>寄り後</b>: 寄り付き→引け。デイトレで取れる部分。ここで ✅ が付く組み合わせが見つかれば、使える可能性がある。</li>
        <li>この結果は毎朝のダッシュボードには出さない（承認銘柄以外を触るきっかけを作らないため）。</li>
      </ul>
    </div>
  </section>
"""


# ----- 想定値幅の検証 -----

def render_range_section(range_results: list[dict] | None, period: str) -> str:
    if not range_results:
        return """
  <section class="section-box">
    <h2 class="section-title">📏 想定値幅の検証（承認銘柄・試用銘柄）</h2>
    <div class="muted" style="padding: 16px;">まだ検証結果がありません（データ取得失敗、または未実行）。</div>
  </section>
"""
    rows = ""
    for res in range_results:
        for v in res.get("by_window", []):
            if v.get("r") is None:
                rows += f'<tr><td><b>{res["name"]}</b></td><td class="num">{v["window"]}日</td><td colspan="5" class="muted">データ不足</td></tr>'
                continue
            rows += f"""
          <tr>
            <td><b>{res["name"]}</b></td>
            <td class="num">直近{v["window"]}日</td>
            <td class="num">{_format_r(v["r"])}<br>{_format_p(v["p"])}</td>
            <td class="num">{v["n"]}</td>
            <td class="num">{v["within_rate"] * 100:.0f}%</td>
            <td class="num">{v["median_ratio"]:.2f}倍</td>
            <td class="num">{v["rough_ratio"]:.2f}倍</td>
          </tr>"""
    return f"""
  <section class="section-box">
    <h2 class="section-title">📏 想定値幅の検証（承認銘柄・試用銘柄）</h2>
    <div class="period-note">検証期間: {period}　／　直近N日の平均値幅（高値−安値）で、翌日の値幅を予測</div>
    <table class="results">
      <thead>
        <tr>
          <th>銘柄</th><th class="num">予測に使う期間</th><th class="num">r / p</th><th class="num">n</th>
          <th class="num">予測以内に<br>収まった日</th><th class="num">実際÷予測<br>（中央値）</th><th class="num">実際÷予測<br>（5日に1日）</th>
        </tr>
      </thead>
      <tbody>{rows}</tbody>
    </table>
    <div class="explainer">
      <h3>📖 読み方</h3>
      <ul>
        <li><b>r</b>: 予測した値幅と実際の値幅の相関。大きいほど「最近荒れていれば今日も荒れる」が当たっている。</li>
        <li><b>実際÷予測（中央値）</b>: 1より小さければ、ふつうの日は予測より小さく収まる。</li>
        <li><b>実際÷予測（5日に1日）</b>: 荒れた日に予測の何倍まで動くか。毎朝のカードの「荒れた日」の幅に使っている。</li>
      </ul>
    </div>
  </section>
"""


def render_screen_section(screen: dict | None) -> str:
    """売買代金上位の銘柄スクリーニング（研究用）"""
    if not screen or not screen.get("results"):
        return """
  <section class="section-box">
    <h2 class="section-title">🔬 売買代金上位の銘柄スクリーニング（研究用）</h2>
    <div class="muted" style="padding: 16px;">まだ結果がありません（データ取得失敗、または未実行）。</div>
  </section>
"""
    labels = screen.get("predictor_labels", {})

    def cell(p: dict | None) -> str:
        if not p or p.get("r_conf") is None:
            return '<td class="muted">—</td>'
        cls = "verdict-confirmed" if p.get("reliable") else "verdict-neutral"
        us = f'{p["us"]} ' if p.get("us") else ""
        hit = f'当たり{p["hit_conf"] * 100:.0f}%' if p.get("hit_conf") is not None else ""
        edge = f' ／ 1回{p["edge_yen"]:+,.0f}円' if p.get("edge_yen") is not None else ""
        mark = "✅ " if p.get("reliable") else ""
        return (f'<td class="tiny"><span class="verdict {cls}">{mark}{us}r={p["r_conf"]:+.2f}</span>'
                f'<div class="muted">前半 r={p["r_disc"]:+.2f} ／ q={p["q_conf"]:.2f}<br>{hit}{edge}</div></td>')

    rows = ""
    for r in sorted(screen["results"], key=lambda r: -(r.get("range_r") or 0)):
        pr = r.get("predictors", {})
        rng = (f'{r["range_r"]:+.2f}<div class="muted tiny">予測以内 {r["range_within"] * 100:.0f}%</div>'
               if r.get("range_r") is not None else "—")
        size = (f'{r["range_pct"]:.1f}%<div class="muted tiny">100株で約{r["range_yen"]:,.0f}円</div>'
                if r.get("range_pct") and r.get("range_yen") else "—")
        tv = f'{r["turnover_oku"]:,.0f}億' if r.get("turnover_oku") else "—"
        rows += f"""
          <tr>
            <td><b>{r["name"]}</b><div class="muted tiny">{r["code"]}</div></td>
            <td class="num">{rng}</td>
            <td class="num">{size}</td>
            <td class="num tiny">{tv}</td>
            {cell(pr.get("us"))}{cell(pr.get("gap"))}{cell(pr.get("prev"))}
          </tr>"""
    n_rel = sum(1 for r in screen["results"] for p in r.get("predictors", {}).values() if p.get("reliable"))
    return f"""
  <section class="section-box">
    <h2 class="section-title">🔬 売買代金上位の銘柄スクリーニング（研究用）</h2>
    <div class="period-note">期間: {screen["period"]}（前半で向きを決め、後半だけで検証）　／　寄り後の方向で「信頼できる」: {n_rel}件（q&lt;{screen["q_threshold"]} かつ前半と同じ向き）</div>
    <table class="results">
      <thead>
        <tr>
          <th>銘柄</th><th class="num">値幅予測<br>r</th><th class="num">1日の値幅<br>（直近60日）</th><th class="num">売買代金<br>（20日平均）</th>
          <th>寄り後 ←<br>{labels.get("us", "us")}</th><th>寄り後 ←<br>{labels.get("gap", "gap")}</th><th>寄り後 ←<br>{labels.get("prev", "prev")}</th>
        </tr>
      </thead>
      <tbody>{rows}</tbody>
    </table>
    <div class="explainer">
      <h3>📖 読み方</h3>
      <ul>
        <li><b>値幅予測 r</b>: 直近10日の平均値幅で翌日の値幅を予測したときの相関。大きいほど「今日の値幅の目安」が当たる。</li>
        <li><b>寄り後 ← ◯◯</b>: 寄り付き時点でわかる手がかりで、寄り→引けの方向を当てられるか。後半の期間での相関 r と、前半の向きで寄りに入り引けで出た場合の当たり率・100株あたりの平均損益（手数料なし）。</li>
        <li><b>窓</b>の r がマイナスなら窓埋め（窓と逆に動く）、プラスなら窓の方向に続く。<b>前日の寄り後</b>も同じ。</li>
        <li>毎朝のダッシュボードには出さない。承認・試用に加える前に、本人の取引成績と合わせて判断する。</li>
      </ul>
    </div>
  </section>
"""


def render_report(
    sector_results: list[PairValidationResult],
    direction_result: DirectionBacktestResult | None,
    period: str,
    output_html: Path,
    output_json: Path,
    focus_results: list[dict] | None = None,
    focus_period: str = "",
    scan_result: dict | None = None,
    range_results: list[dict] | None = None,
    screen_result: dict | None = None,
) -> None:
    """結果をHTMLとJSONで出力"""
    now = datetime.now(JST).strftime("%Y-%m-%d %H:%M JST")

    # ===== セクター検証セクション =====
    n_total = len(sector_results)
    n_confirmed = sum(1 for r in sector_results if r.verdict == "confirmed")
    n_weak = sum(1 for r in sector_results if r.verdict == "weak")
    n_contradicted = sum(1 for r in sector_results if r.verdict == "contradicted")
    n_insufficient = sum(1 for r in sector_results if r.verdict == "insufficient_data")

    sector_rows = ""
    for r in sector_results:
        tickers_us = ", ".join(r.us_tickers_used) if r.us_tickers_used else "—"
        tickers_jp = ", ".join(r.jp_tickers_used) if r.jp_tickers_used else "—"
        sector_rows += f"""
          <tr>
            <td>
              <div class="sector-name"><b>{r.sector_name}</b></div>
              <div class="muted tiny">期待: {r.expected_correlation}</div>
            </td>
            <td class="num">{_format_r(r.pearson_r)}</td>
            <td class="num">{_format_p(r.p_value)}</td>
            <td class="num">{r.n_samples}</td>
            <td class="num">{_format_hit_rate(r.hit_rate_up, r.n_us_up_days)}</td>
            <td class="num">{_format_hit_rate(r.hit_rate_down, r.n_us_down_days)}</td>
            <td><span class="verdict {_verdict_class(r.verdict)}">{r.verdict_label}</span></td>
            <td class="tiny muted">
              米: {tickers_us}<br>
              日: {tickers_jp}
            </td>
          </tr>
        """

    # ===== 日経方針検証セクション =====
    if direction_result and direction_result.total_days > 0:
        dir_rows = ""
        for s in direction_result.stats_by_verdict:
            if s.n == 0:
                continue
            dir_rows += f"""
              <tr>
                <td>{_verdict_badge(s.verdict, s.verdict_label)}</td>
                <td class="num">{s.n}</td>
                <td class="num">{_format_pct(s.mean_intraday_pct)}</td>
                <td class="num">{_format_pct(s.median_intraday_pct)}</td>
                <td class="num"><span class="muted">±{s.std_intraday_pct:.2f}%</span></td>
                <td class="num">{_format_win_rate(s.directional_win_rate, s.n_aligned)}</td>
                <td class="num">{_format_pct(s.avg_when_aligned)}</td>
                <td class="num">{_format_pct(s.mean_daily_pct)}</td>
              </tr>
            """
        direction_section = f"""
  <section class="section-box">
    <h2 class="section-title">📈 日経デイトレ方針判定の検証</h2>
    <div class="period-note">
      検証期間: {direction_result.start_date} ～ {direction_result.end_date}
      ({direction_result.total_days}営業日)
    </div>

    <table class="results">
      <thead>
        <tr>
          <th>判定</th>
          <th class="num">該当日数</th>
          <th class="num">日中<br>平均</th>
          <th class="num">日中<br>中央値</th>
          <th class="num">日中<br>標準偏差</th>
          <th class="num">方向一致率</th>
          <th class="num">一致日平均</th>
          <th class="num">日次<br>平均</th>
        </tr>
      </thead>
      <tbody>
        {dir_rows}
      </tbody>
    </table>

    <div class="explainer">
      <h3>📖 指標の読み方</h3>
      <ul>
        <li><b>日中平均</b>: その判定が出た日の日経の寄り付き→引け の平均騰落率。
          買いポジ主体でプラス、売りポジ主体でマイナスになっていれば判定は機能している。</li>
        <li><b>日中中央値</b>: 外れ値に影響されないロバストな中心傾向。平均と乖離があれば外れ値の影響が大きい。</li>
        <li><b>日中標準偏差</b>: ボラティリティ。大きいほどその日の値幅ブレが大きい。</li>
        <li><b>方向一致率</b>: 判定方向に日中が動いた日の割合。50%は偶然レベル、55%超で有意、60%超で信頼性高い。
          「初心者手出し無用」では「±0.3%以内のレンジ」を一致と定義。</li>
        <li><b>一致日平均</b>: 方向一致した日だけの平均騰落率。大きいほど「当たった時の収益」が大きい。</li>
        <li><b>日次平均</b>: 前日終値→当日終値の平均（スイング視点）。日中より大きくなる傾向あり。</li>
      </ul>
    </div>

    <div class="explainer">
      <h3>🎯 判定の使い方</h3>
      <ul>
        <li><b>方向一致率 ≥ 60%</b>: シグナルを信頼して OCO不成デイトレで使える水準。</li>
        <li><b>方向一致率 50-60%</b>: 参考程度。他の指標（キリバン、セクター）と組み合わせて判断。</li>
        <li><b>方向一致率 < 50%</b>: シグナルが実相場で機能していない。しきい値や条件を見直す候補。</li>
      </ul>
    </div>
  </section>
"""
    else:
        direction_section = """
  <section class="section-box">
    <h2 class="section-title">📈 日経デイトレ方針判定の検証</h2>
    <div class="muted" style="padding: 16px;">データ取得に失敗しました。</div>
  </section>
"""

    focus_section = render_focus_section(focus_results, focus_period)
    scan_section = render_scan_section(scan_result)
    range_section = render_range_section(range_results, focus_period)
    screen_section = render_screen_section(screen_result)

    # ===== HTML全体 =====
    html = f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Backtest Report - Daytrade Signal Board</title>
<style>
  :root {{
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
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0;
    font-family: -apple-system, "Segoe UI", "Hiragino Sans", "Yu Gothic", sans-serif;
    background: var(--bg);
    color: var(--text);
    line-height: 1.6;
  }}
  .container {{ max-width: 1280px; margin: 0 auto; padding: 24px; }}
  header {{ display: flex; justify-content: space-between; align-items: baseline; margin-bottom: 24px; border-bottom: 1px solid var(--border); padding-bottom: 16px; flex-wrap: wrap; gap: 8px; }}
  h1 {{ margin: 0; font-size: 20px; font-weight: 600; }}
  .back-link {{ color: var(--accent); text-decoration: none; font-size: 14px; }}
  .back-link:hover {{ text-decoration: underline; }}
  .timestamp {{ color: var(--muted); font-size: 13px; }}
  .period {{ color: var(--accent); font-size: 13px; font-weight: 600; }}
  .period-note {{ color: var(--muted); font-size: 13px; margin-bottom: 12px; }}

  section.section-box {{ margin-bottom: 40px; }}
  h2.section-title {{
    font-size: 18px;
    font-weight: 600;
    color: var(--accent);
    margin: 0 0 14px 0;
    padding-bottom: 8px;
    border-bottom: 1px solid var(--border);
  }}

  .summary {{
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
    gap: 12px;
    margin-bottom: 20px;
  }}
  .summary-card {{
    background: var(--panel);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 14px;
    text-align: center;
  }}
  .summary-card .num {{ font-size: 28px; font-weight: 700; font-variant-numeric: tabular-nums; }}
  .summary-card .label {{ color: var(--muted); font-size: 12px; margin-top: 4px; }}
  .summary-confirmed .num {{ color: var(--long); }}
  .summary-weak .num {{ color: var(--accent); }}
  .summary-contradicted .num {{ color: var(--short); }}
  .summary-insufficient .num {{ color: var(--neutral); }}

  table.results {{
    width: 100%;
    border-collapse: collapse;
    background: var(--panel);
    border: 1px solid var(--border);
    border-radius: 10px;
    overflow: hidden;
    font-size: 13px;
  }}
  table.results th {{
    background: var(--panel-2);
    color: var(--muted);
    font-weight: 500;
    text-align: left;
    padding: 12px 10px;
    border-bottom: 1px solid var(--border);
    font-size: 12px;
    letter-spacing: 0.05em;
    text-transform: uppercase;
  }}
  table.results td {{
    padding: 12px 10px;
    border-bottom: 1px solid var(--border);
    vertical-align: middle;
  }}
  table.results tr:last-child td {{ border-bottom: none; }}
  .num {{ text-align: right; font-variant-numeric: tabular-nums; }}
  .muted {{ color: var(--muted); }}
  .tiny {{ font-size: 11px; }}
  .sector-name {{ font-size: 14px; }}

  .verdict {{
    display: inline-block;
    padding: 3px 10px;
    border-radius: 999px;
    font-size: 12px;
    font-weight: 600;
    white-space: nowrap;
  }}
  .verdict-confirmed {{ background: rgba(16, 185, 129, 0.15); color: var(--long); }}
  .verdict-weak {{ background: rgba(251, 191, 36, 0.15); color: var(--accent); }}
  .verdict-contradicted {{ background: rgba(239, 68, 68, 0.15); color: var(--short); }}
  .verdict-neutral {{ background: rgba(107, 114, 128, 0.15); color: var(--neutral); }}

  .dir-badge {{
    display: inline-block;
    padding: 4px 10px;
    border-radius: 6px;
    font-size: 13px;
    font-weight: 600;
    white-space: nowrap;
  }}
  .dir-buy {{ background: rgba(16, 185, 129, 0.15); color: var(--long); }}
  .dir-sell {{ background: rgba(239, 68, 68, 0.15); color: var(--short); }}
  .dir-hands-off {{ background: rgba(107, 114, 128, 0.15); color: var(--neutral); }}

  .explainer {{
    background: var(--panel);
    border: 1px solid var(--border);
    border-left: 3px solid var(--accent);
    border-radius: 0 8px 8px 0;
    padding: 14px 18px;
    margin: 16px 0;
    font-size: 13px;
  }}
  .explainer h3 {{ margin: 0 0 8px 0; font-size: 14px; color: var(--accent); }}
  .explainer ul {{ margin: 4px 0; padding-left: 20px; }}
  .explainer li {{ margin: 4px 0; }}
  .explainer code {{ background: var(--panel-2); padding: 1px 6px; border-radius: 3px; font-size: 12px; }}

  .scan-highlight {{ border: 1px solid var(--accent); border-radius: 8px; padding: 12px 16px; margin: 12px 0; background: rgba(251,191,36,0.06); }}
  .scan-highlight h3 {{ margin: 0 0 6px 0; font-size: 14px; color: var(--accent); }}
  .scan-hits {{ margin: 0; padding-left: 20px; }}
  .scan-grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 12px; margin: 12px 0; }}
  .scan-card {{ background: var(--panel-2); border-radius: 8px; padding: 12px 14px; font-size: 13px; }}
  .scan-card ul {{ margin: 4px 0 8px 0; padding-left: 18px; }}
  .scan-sub {{ margin-top: 8px; font-size: 11px; color: var(--muted); }}

  .footer-note {{
    margin-top: 32px;
    padding: 16px;
    background: var(--panel);
    border-radius: 8px;
    font-size: 12px;
    color: var(--muted);
    border: 1px solid var(--border);
  }}
</style>
</head>
<body>
<div class="container">
  <header>
    <div>
      <h1>🧪 バックテスト検証レポート</h1>
      <a href="./index.html" class="back-link">← メインダッシュボードに戻る</a>
    </div>
    <div style="text-align: right;">
      <div class="period">検証期間: 過去 {period}</div>
      <div class="timestamp">Updated: {now}</div>
    </div>
  </header>

  {direction_section}

  {focus_section}

  {range_section}

  {scan_section}
  {screen_section}

  <section class="section-box">
    <h2 class="section-title">🔁 米→日セクター連動マッピングの検証</h2>

    <div class="summary">
      <div class="summary-card summary-confirmed">
        <div class="num">{n_confirmed}</div>
        <div class="label">✅ 連動確認</div>
      </div>
      <div class="summary-card summary-weak">
        <div class="num">{n_weak}</div>
        <div class="label">△ 弱連動</div>
      </div>
      <div class="summary-card summary-contradicted">
        <div class="num">{n_contradicted}</div>
        <div class="label">✗ 連動せず</div>
      </div>
      <div class="summary-card summary-insufficient">
        <div class="num">{n_insufficient}</div>
        <div class="label">— データ不足</div>
      </div>
      <div class="summary-card">
        <div class="num">{n_total}</div>
        <div class="label">合計セクター</div>
      </div>
    </div>

    <table class="results">
      <thead>
        <tr>
          <th>セクター</th>
          <th class="num">相関 r</th>
          <th class="num">p値</th>
          <th class="num">n</th>
          <th class="num">米↑→日↑</th>
          <th class="num">米↓→日↓</th>
          <th>判定</th>
          <th>使用銘柄</th>
        </tr>
      </thead>
      <tbody>
        {sector_rows}
      </tbody>
    </table>

    <div class="explainer">
      <h3>📖 指標の読み方</h3>
      <ul>
        <li><b>相関 r</b>: 米セクター平均騰落率(T日) と 日本セクター平均騰落率(T+1日) のピアソン相関係数。
          <code>r ≥ 0.4</code> で強い連動、<code>0.2 ≤ r &lt; 0.4</code> で中程度、それ未満は弱い。</li>
        <li><b>p値</b>: 「本当は無相関なのに偶然この相関係数が出た確率」。<code>&lt; 0.05</code> で統計的に有意。</li>
        <li><b>n</b>: 有効サンプル数（日数）。<code>n &lt; 20</code> だと判定不能。</li>
        <li><b>米↑→日↑ ヒット率</b>: 米セクターが<b>+1%以上上げた日</b>、翌日の日本セクターが<b>+0%以上</b>だった確率。</li>
        <li><b>米↓→日↓ ヒット率</b>: 米セクターが<b>-1%以上下げた日</b>、翌日の日本セクターが<b>-0%以下</b>だった確率。</li>
      </ul>
    </div>
  </section>

  <div class="footer-note">
    <b>⚠️ 注意点</b><br>
    ・バックテスト期間は市場環境に大きく依存します（下げ相場・上げ相場で相関が変わる）。定期的に再検証してください。<br>
    ・相関が高くても、将来も同じとは限りません。特に個別銘柄材料（決算、不正、M&A）発生時は連動が崩れます。<br>
    ・サンプル数が少ないセクターでは、たまたま高い相関が出ていることがあります。p値も必ず確認してください。<br>
    ・方向一致率は「方向が合った率」であり、実際のトレード収益性とは別物です（スリッページ・取引コスト未考慮）。
  </div>
</div>
</body>
</html>"""

    output_html.parent.mkdir(parents=True, exist_ok=True)
    output_html.write_text(html, encoding="utf-8")

    # JSON出力
    json_data = {
        "generated_at": now,
        "period": period,
        "sector_validation": {
            "summary": {
                "total": n_total,
                "confirmed": n_confirmed,
                "weak": n_weak,
                "contradicted": n_contradicted,
                "insufficient_data": n_insufficient,
            },
            "results": [r.as_dict() for r in sector_results],
        },
        "direction_validation": direction_result.as_dict() if direction_result else None,
        "focus_validation": {"period": focus_period, "results": focus_results} if focus_results else None,
        "correlation_scan": scan_result,
        "range_validation": {"period": focus_period, "results": range_results} if range_results else None,
        "stock_screen": screen_result,
    }
    output_json.write_text(
        json.dumps(json_data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(f"📄 HTML report: {output_html}")
    print(f"📊 JSON report: {output_json}")


def run_backtest_and_report(period: str = "6mo", direction_period: str = "1y") -> None:
    """バックテストを実行してレポートを出力するエントリポイント

    Args:
        period: セクター検証の期間
        direction_period: 日経方針検証の期間（より長めがおすすめ）
    """
    from backtest import validate_all_sectors
    from direction_backtest import validate_direction_logic

    output_dir = Path(__file__).resolve().parent.parent / "docs"
    output_dir.mkdir(exist_ok=True)

    print(f"🔍 Running sector validation for period: {period}")
    sector_results = validate_all_sectors(period=period)

    print()
    print(f"🔍 Running direction logic validation for period: {direction_period}")
    direction_result = None
    try:
        direction_result = validate_direction_logic(period=direction_period)
    except Exception as e:
        print(f"⚠️ Direction validation failed: {e}")

    print()
    print(f"🔍 Running focus stock validation for period: {direction_period}")
    focus_results = None
    try:
        from focus_backtest import validate_all_focus_stocks
        focus_results = validate_all_focus_stocks(period=direction_period)
    except Exception as e:
        print(f"⚠️ Focus stock validation failed: {e}")

    print()
    print(f"🔍 Running expected range validation for period: {direction_period}")
    range_results = None
    try:
        from expected_range import run_range_validation
        range_results = run_range_validation(period=direction_period)
    except Exception as e:
        print(f"⚠️ Range validation failed: {e}")

    print()
    print(f"🔍 Running US x JP correlation scan for period: {direction_period}")
    scan_result = None
    try:
        from correlation_scan import run_correlation_scan
        scan_result = run_correlation_scan(period=direction_period)
    except Exception as e:
        print(f"⚠️ Correlation scan failed: {e}")

    print()
    print("🔍 Running stock screen (top turnover) for period: 2y")
    screen_result = None
    try:
        from stock_screen import run_stock_screen
        screen_result = run_stock_screen(period="2y")
    except Exception as e:
        print(f"⚠️ Stock screen failed: {e}")

    print()
    render_report(
        sector_results=sector_results,
        direction_result=direction_result,
        period=f"{period} (セクター) / {direction_period} (日経方針・個別)",
        output_html=output_dir / "backtest_report.html",
        output_json=output_dir / "backtest_report.json",
        focus_results=focus_results,
        focus_period=direction_period,
        scan_result=scan_result,
        range_results=range_results,
        screen_result=screen_result,
    )


if __name__ == "__main__":
    import sys

    period = sys.argv[1] if len(sys.argv) > 1 else "6mo"
    direction_period = sys.argv[2] if len(sys.argv) > 2 else "1y"
    run_backtest_and_report(period=period, direction_period=direction_period)
