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
MIN_RANGE_DAYS = 30          # checkpoint_study.MIN_DAYS と同じ


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
    hi_rows = ""
    pc = lambda x: "—" if x is None else f"{x * 100:.0f}%"
    for r in focus_results:
        h = r.get("at_high")
        if not h or not h.get("high", {}).get("n"):
            continue
        cell = lambda b: (f'{pc(b["up_rate"])}（{b["n"]}日）'
                          + (f'<div class="muted tiny">{b["up_ci"][0] * 100:.0f}〜{b["up_ci"][1] * 100:.0f}%・平均 {b["mean"]:+.2f}%</div>' if b.get("up_ci") else ""))
        p = f'{h["p_rate"]:.3f}' if h.get("p_rate") is not None else "件数不足"
        hi_rows += (f'<tr><td>{r["name"]}</td><td class="num">{cell(h["high"])}</td><td class="num">{cell(h["other"])}</td>'
                    f'<td class="num">{p}{" ✅" if h.get("significant") else ""}</td></tr>')
    hi_html = f"""
    <div class="explainer">
      <h3>📈 52週高値を更新中の日の寄り後（{next((r["at_high"]["period"] for r in focus_results if r.get("at_high")), "")}）</h3>
      <table class="results"><thead><tr><th>銘柄</th><th class="num">高値更新中の日：寄り後に上げた割合</th>
        <th class="num">そうでない日</th><th class="num">差の p</th></tr></thead><tbody>{hi_rows}</tbody></table>
      <div class="muted tiny">前日までの直近5日で、それより前の52週（250営業日）の高値を超えた日を「更新中」とする（カードの表示と同じ決め方）。
        記事では「最高値を更新中の銘柄は買いが入りやすい」。✅ は割合の差が p&lt;0.05。</div>
    </div>""" if hi_rows else ""
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
    {hi_html}
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


def render_morning_section(morning: dict | None) -> str:
    """9時台の値幅・決算明けの検証"""
    if not morning or not morning.get("per_code"):
        return """
  <section class="section-box">
    <h2 class="section-title">🌅 9時台の値幅・決算明けの検証</h2>
    <div class="muted" style="padding: 16px;">まだ検証結果がありません（データ取得失敗、または未実行）。</div>
  </section>
"""
    rows = ""
    for code, v in morning["per_code"].items():
        e = v.get("earnings") or {}
        ratio = f'{v["ratio"]:.2f}倍' if v.get("ratio") else "—"
        rough = f'{v["rough_ratio"]:.2f}倍' if v.get("rough_ratio") else "—"
        earn = f'{e["ratio"]:.2f}倍（{e["n"]}回）' if e.get("ratio") else "—"
        rows += f"""
          <tr><td><b>{v["name"]}</b><div class="muted tiny">{code}</div></td>
            <td class="num">{v["n"]}</td><td class="num">{ratio}</td><td class="num">{rough}</td><td class="num">{earn}</td></tr>"""
    first5 = "".join(
        f'<li>最初の10分の値幅が1日の目安の{r["from"] * 100:.0f}%' + (f'〜{r["to"] * 100:.0f}%' if r["to"] else "以上")
        + f'：9時台は目安の<b>{r["ratio"]:.2f}倍</b>（{r["n"]}件）</li>'
        for r in morning.get("first5_table", [])
    )
    return f"""
  <section class="section-box">
    <h2 class="section-title">🌅 9時台の値幅・決算明けの検証</h2>
    <div class="period-note">9時台: 5分足・直近60日　／　決算明け: 日足・{morning["period"]}</div>
    <table class="results">
      <thead><tr><th>銘柄</th><th class="num">日数</th><th class="num">9時台÷1日の目安<br>（中央値）</th><th class="num">同<br>（5日に1日）</th><th class="num">決算明けの値幅<br>÷1日の目安</th></tr></thead>
      <tbody>{rows}</tbody>
    </table>
    <div class="explainer">
      <h3>📖 寄り直後10分の動きの大きさ別（売買代金上位20銘柄まとめ）</h3>
      <ul>{first5}</ul>
      <ul>
        <li>毎朝のカードの「9時台の目安」は、1日の目安 × 上の「9時台÷1日の目安」で出している。</li>
        <li>決算明けは値幅が大きくなりやすい銘柄がある。カードに注意を出す。</li>
      </ul>
    </div>
  </section>
"""


def render_checkpoint_section(cp: dict | None) -> str:
    """節目ごとの値動きの方向を当てられるか"""
    if not cp or not cp.get("pooled"):
        return """
  <section class="section-box">
    <h2 class="section-title">⏱️ 節目ごとの方向（寄り・9:05・9:15・9:30・10:00）</h2>
    <div class="muted" style="padding: 16px;">まだ結果がありません（データ取得失敗、または未実行）。</div>
  </section>
"""
    labels = cp["predictors"]
    by = {(o["seg"], o["key"]): o for o in cp["pooled"]}

    def cell(o: dict | None) -> str:
        if not o:
            return '<td class="muted">—</td>'
        cls = "verdict-confirmed" if o.get("significant") else "verdict-neutral"
        mark = "✅ " if o.get("significant") else ""
        return (f'<td class="tiny"><span class="verdict {cls}">{mark}r={o["r"]:+.2f}</span>'
                f'<div class="muted">同じ向き {o["same_dir"] * 100:.0f}%<br>q={o["q"]:.2f}</div></td>')

    rows = "".join(
        f'<tr><td><b>{seg}</b></td>' + "".join(cell(by.get((seg, k))) for k in labels) + "</tr>"
        for seg in cp["segments"]
    )
    hits = "".join(
        f'<li>✅ {s["name"]}：{h["seg"]} ← {labels[h["key"]]}（r={h["r"]:+.2f}・当たり{h["hit"] * 100:.0f}%）</li>'
        for s in cp["per_stock"] for h in s["hits"]
    ) or "<li>銘柄ごとでは、補正後も有意なものはなし</li>"
    rd = cp.get("range_day") or {}
    pc = lambda x: "—" if x is None else f"{x * 100:.0f}%"
    sg = lambda x: "—" if x is None else f"{x:.2f}%"
    rd_html = (f"""
      <h3>🔁 レンジ型の日（米3指数がすべて ±0.5% 未満・現対 ±100円以内）：{rd["n_range"]}日 ／ ほかの日 {rd["n_other"]}日</h3>
      <ul>
        <li>寄り→9:15 の最安値から 10:00 までの戻り（銘柄平均）：{sg(rd["rebound_range"])} ／ ほかの日 {sg(rd["rebound_other"])}（p={rd["p_rebound"] if rd["p_rebound"] is None else f'{rd["p_rebound"]:.3f}'}）</li>
        <li>寄り→10:00 が上げた日の割合：{pc(rd["up_range"])} ／ ほかの日 {pc(rd["up_other"])}（50%との差 p={rd["p_up"] if rd["p_up"] is None else f'{rd["p_up"]:.3f}'}）{" ✅" if rd.get("significant") else ""}</li>
        <li>✅（{MIN_RANGE_DAYS}日以上・上げた割合が50%より多く p&lt;0.05）になるまで、毎朝の「レンジ型の日」の表示は「未検証」のまま。</li>
      </ul>""" if rd.get("n_range") else "")
    return f"""
  <section class="section-box">
    <h2 class="section-title">⏱️ 節目ごとの方向（寄り・9:05・9:15・9:30・10:00）</h2>
    <div class="period-note">データ: {cp["source"]}　／　全銘柄まとめ（日ごとに銘柄平均をとって日数で検定・BH補正）</div>
    <table class="results">
      <thead><tr><th>区間</th>{"".join(f"<th>← {v}</th>" for v in labels.values())}</tr></thead>
      <tbody>{rows}</tbody>
    </table>
    <div class="explainer">
      <h3>銘柄ごと（{cp["n_tests"]}通りを BH 補正）</h3>
      <ul>{hits}</ul>
      {rd_html}
      <h3>📖 読み方</h3>
      <ul>
        <li><b>r</b>: 手がかりと区間の動きの相関（銘柄ごとに標準化して平均）。＋なら同じ向き、−なら逆向きに動きやすい。</li>
        <li><b>同じ向き</b>: 手がかりと区間の動きの向きが一致した割合。50%ならコイン投げと同じ。</li>
        <li><b>寄り→区間の始まりまでの動き</b> が＋なら「動いた方向に乗る」、−なら「寄り直後の動きは戻しやすい」。</li>
        <li>寄り値は日足の始値、各節目の値段はその時刻に始まる5分足の始値（Yahoo は 9:00〜9:05 の足が欠ける日が多いため）。</li>
      </ul>
    </div>
  </section>
"""


def render_fib_section(fib: dict | None) -> str:
    """フィボナッチの水準で反発しやすいか"""
    pats = (fib or {}).get("patterns") or {}
    if not pats:
        return """
  <section class="section-box">
    <h2 class="section-title">📐 フィボナッチの水準で反発しやすいか</h2>
    <div class="muted" style="padding: 16px;">まだ結果がありません（データ取得失敗、または未実行）。</div>
  </section>
"""
    titles = {"gap": f"窓の幅に引いた線（窓 ±{fib['min_gap_pct']:.0f}%以上の日・前場）",
              "swing": "寄り後30分（9:00〜9:30）の値幅に引いた線（9:30以降・前場）"}
    blocks = ""
    for key, title in titles.items():
        s = pats.get(key) or {}
        if s.get("diff") is None:
            blocks += f'<h3>{title}</h3><div class="muted">件数不足（{s.get("n", 0)}回）</div>'
            continue
        cls = "verdict-confirmed" if s["significant"] else "verdict-neutral"
        verdict = "✅ フィボの水準の方が反発しやすい" if s["significant"] and s["diff"] > 0 else (
            "フィボの水準の方が反発しにくい" if s["significant"] else "差は偶然の範囲")
        cells = "".join(
            f'<td class="num">{"<b>" if lv["fib"] else ""}{(lv["bounce"] or 0) * 100:.0f}%{"</b>" if lv["fib"] else ""}'
            f'<div class="muted tiny">{lv["n"]}回</div></td>'
            for lv in s["levels"]
        )
        heads = "".join(f'<th class="num">{"<b>" if lv["fib"] else ""}{lv["level"] * 100:.1f}%{"</b>" if lv["fib"] else ""}</th>'
                        for lv in s["levels"])
        blocks += f"""
      <h3>{title}</h3>
      <div style="margin: 6px 0 10px;"><span class="verdict {cls}">{verdict}</span>
        <span class="muted tiny">フィボ {s["fib_bounce"] * 100:.1f}%（{s["n_fib"]}回） vs 比較用 {s["control_bounce"] * 100:.1f}%（{s["n_control"]}回）・
        差 {s["diff"] * 100:+.1f}pt（95%区間 {s["ci"][0] * 100:+.1f}〜{s["ci"][1] * 100:+.1f}pt）</span></div>
      <table class="results"><thead><tr><th>水準</th>{heads}</tr></thead><tbody><tr><td>反発率</td>{cells}</tr></tbody></table>"""
    return f"""
  <section class="section-box">
    <h2 class="section-title">📐 フィボナッチの水準で反発しやすいか</h2>
    <div class="period-note">データ: {fib["source"]}　／　太字がフィボの水準、それ以外は比較用</div>
    <div class="explainer" style="margin-top: 0;">{blocks}</div>
    <div class="explainer">
      <h3>📖 読み方</h3>
      <ul>
        <li>線に初めて触れたあと、線から幅の{fib["bounce_frac"] * 100:.0f}%だけ元の方向に戻したら「反発」、先に進んだら「抜けた」。反発率 = 反発 ÷（反発＋抜けた）。</li>
        <li>どんな水準でもある程度は反発するので、フィボの水準の反発率を比較用の水準（30%・45%・55%・70%）と比べる。</li>
        <li>95%区間が0をまたがなければ、差は偶然ではなさそう（同じ日・同じ銘柄の線どうしの連動を考えて計算）。</li>
        <li>5分足では1本の中で触れて戻ったか抜けたかが分かりにくい。1分足が40日分たまったら1分足に切り替わる。</li>
      </ul>
    </div>
  </section>
"""


def _pc(x: float | None) -> str:
    return "—" if x is None else f"{x * 100:.0f}%"


def _sg(x: float | None) -> str:
    return "—" if x is None else f"{x:+.2f}%"


def render_idea_section(ideas: dict | None) -> str:
    """メンサロ記事の考え方の検証"""
    title = "🧪 記事の考え方の検証（ドル円・S&amp;P500・金曜／月末・曜日・権利日・25日線）"
    if not ideas or not ideas.get("v2"):
        return f"""
  <section class="section-box">
    <h2 class="section-title">{title}</h2>
    <div class="muted" style="padding: 16px;">まだ結果がありません（データ取得失敗、または未実行）。</div>
  </section>
"""
    sig = {t["name"] for t in ideas.get("tests", []) if t["significant"]}
    qmap = {t["name"]: t["q"] for t in ideas.get("tests", [])}

    def mark(name: str) -> str:
        q = qmap.get(name)
        if q is None:
            return ""
        return f' <b style="color: var(--long);">q={q:.3f} ✅</b>' if name in sig else f' <span class="muted tiny">q={q:.2f}</span>'

    v = ideas["v2"]
    rows = "".join(
        f'<tr><td>{lab}</td><td class="num">{v[k]["n"]}</td><td class="num">{_pc(v[k]["hit"])}{mark(f"v2比較:{k} 的中")}</td>'
        f'<td class="num">{_sg(v[k]["mean"])}</td><td class="num">{_pc(v[k]["hands_off_share"])}</td></tr>'
        for k, lab in (("v1", "今の判定（米3指数＋現対）"), ("v2", "ドル円・S&amp;P500 入り"))
    )
    agree = "　".join(f'{a["agree"]}つ：{_pc(a["hit"])}（{a["n"]}日）' for a in v["by_agree"])
    sp_rows = ""
    for name, r in ideas["sp05"].items():
        cells = ""
        for lab in ("up", "down"):
            x = r[lab]
            cells += (f'<td class="num">{_pc(x["hit"])}（{x["n"]}）{mark(f"S&P±0.5%:{name}:{lab}")}</td>'
                      f'<td class="num">{_sg(x["mean"])}<div class="muted tiny">外れ値除く {_sg(x["mean_trim"])}</div></td>')
        sp_rows += f"<tr><td>{name}</td>{cells}</tr>"
    labs = {"all": "全部", "gap_same": "時間外も同じ向き", "gap_opposite": "時間外が逆向き", "gap_small": "時間外は小さい"}
    cl_rows = ""
    for kind, kl in (("friday", "金曜（今週の向きと逆）"), ("month_end", "月末（今月の向きと逆）"), ("both", "金曜×月末")):
        r = ideas["closing"][kind]
        cl_rows += f"<tr><td>{kl}</td>" + "".join(
            f'<td class="num">{_pc(r[k]["hit"])}（{r[k]["n"]}）<div class="muted tiny">{_sg(r[k]["mean"])}</div>{mark(f"手仕舞い:{kind}:{k}")}</td>'
            for k in labs) + "</tr>"
    wd_rows = ""
    for name, r in ideas["weekday"].items():
        wd_rows += f"<tr><td>{name}</td>" + "".join(
            f'<td class="num">{r[k]["trendiness"]:.2f}<div class="muted tiny">値幅 {r[k]["range_pct"]:.2f}%</div>{mark(f"曜日:{name}:{k}") if k != "火〜木" else ""}</td>'
            for k in ("週明け", "金曜", "火〜木")) + "</tr>"
    kn_rows = "".join(
        f'<tr><td>{lab}</td><td class="num">{_pc(r["last_day_buy"]["hit"])}（{r["last_day_buy"]["n"]}）</td>'
        f'<td class="num">{_sg(r["last_day_buy"]["mean"])}</td><td class="num">{_sg(r["ex_day_gap_mean"])}（{r["ex_day_n"]}）</td></tr>'
        for lab, r in ideas["kenri"].items())
    dv_rows = ""
    for name, r in ideas["dev25"].items():
        dv_rows += f"<tr><td>{name}</td>" + "".join(
            f'<td class="num">{_pc(x["up"])}（{x["n"]}）<div class="muted tiny">{_sg(x["mean"])}</div>{mark(f"乖離:{name}:{k}") if k != "その間" else ""}</td>'
            for k, x in r.items()) + "</tr>"
    st_rows = "".join(
        f"<tr><td>{name}</td>" + "".join(f'<td class="num">{_pc(r[k]["same"])}（{r[k]["n"]}）</td>' for k in ("high", "medium", "low")) + "</tr>"
        for name, r in (ideas.get("strength") or {}).items())
    ab = ideas.get("after_break") or {}
    ab_txt = (f'連休明け（{ab["連休明け"]["n"]}日）の窓 平均 {ab["連休明け"]["abs_gap"]:.2f}%・値幅 {ab["連休明け"]["range_pct"]:.2f}%'
              f' ／ 普段の日 {ab["普段の日"]["abs_gap"]:.2f}%・{ab["普段の日"]["range_pct"]:.2f}%（p={ab["p_gap"]:.3f}）') if ab else ""
    uc = ideas.get("us_closed") or {}
    gold_html = render_gold_block(ideas.get("gold"), mark)
    return f"""
  <section class="section-box">
    <h2 class="section-title">{title}</h2>
    <div class="period-note">日経平均・承認／試用銘柄の日足 {ideas["period"]}（{ideas["n_days"]}日）。成績はすべて寄り→引け。✅ は補正後（BH法 q&lt;0.05）でも偶然とは言いにくいもの</div>
    <div class="explainer" style="margin-top: 0;">
      <h3>判定材料にドル円・S&amp;P500 を足す（後半 {v["split"]}〜 だけで比較）</h3>
      <table class="results"><thead><tr><th></th><th class="num">方向ありの日</th><th class="num">的中</th><th class="num">平均</th><th class="num">手出し無用の割合</th></tr></thead><tbody>{rows}</tbody></table>
      <div class="muted tiny">6材料（米4指数・ドル円・現対）のうちそろった数ごとの的中（今の判定で方向ありの日）：{agree}</div>
      {gold_html}
      <h3>S&amp;P500 ±0.5% の翌日に、寄りで同じ向きに入って引けで手仕舞う</h3>
      <table class="results"><thead><tr><th></th><th class="num">+0.5%以上→買い 的中</th><th class="num">平均</th><th class="num">-0.5%以下→売り 的中</th><th class="num">平均</th></tr></thead><tbody>{sp_rows}</tbody></table>
      <h3>金曜・月末は今の向きと逆に動くか（時間外 = 前日引け→寄りの窓 ±0.2%）</h3>
      <table class="results"><thead><tr><th></th>{"".join(f'<th class="num">{x}</th>' for x in labs.values())}</tr></thead><tbody>{cl_rows}</tbody></table>
      <h3>曜日の型：トレンドらしさ（|引け−寄り| ÷ 値幅。大きいほど一方向）</h3>
      <table class="results"><thead><tr><th></th><th class="num">週明け（月曜・連休明け）</th><th class="num">金曜</th><th class="num">火〜木</th></tr></thead><tbody>{wd_rows}</tbody></table>
      <div class="muted tiny">{ab_txt}</div>
      <h3>権利付き最終日（買い）・権利落ち日（窓）　※件数が少ないので参考</h3>
      <table class="results"><thead><tr><th></th><th class="num">付き最終日 上げ</th><th class="num">平均</th><th class="num">落ち日の窓</th></tr></thead><tbody>{kn_rows}</tbody></table>
      <h3>25日線からの乖離（その銘柄自身の過去250日の ±2σ）と、その日の上げやすさ</h3>
      <table class="results"><thead><tr><th></th>{"".join(f'<th class="num">{k}</th>' for k in next(iter(ideas["dev25"].values())))}</tr></thead><tbody>{dv_rows}</tbody></table>
      <h3>判定の確度ごとに、寄り→10:00 の向きが 10:00→引け も続いた割合（1時間足）</h3>
      <table class="results"><thead><tr><th></th><th class="num">確度 高</th><th class="num">中</th><th class="num">方向なしの日</th></tr></thead><tbody>{st_rows}</tbody></table>
      <div class="muted tiny">米国休場明けの朝：{uc.get("n", 0)}日（今の判定はこの日を「手出し無用」にする）</div>
      {render_idea_extra(ideas.get("extra") or {}, mark)}
    </div>
  </section>
"""


def render_gold_block(g: dict | None, mark) -> str:
    """ゴールドを材料に入れる（L6-1：固定の符号／L7-4：直近60日の相関の符号）"""
    if not g:
        return '<div class="muted tiny">ゴールド：まだ結果がありません</div>'
    labs = {"fixed": "固定（ゴールド下げ＝上げ材料）", "corr": "直近60日の相関の符号（|r|&lt;0.1 は使わない）"}
    rows = ""
    for k, lab in labs.items():
        r = g.get(k)
        if not r:
            continue
        a, v, vg = r["alone"], r["v2"], r["v2_gold"]
        agree = "　".join(f'{x["agree"]}つ：{_pc(x["hit"])}（{x["n"]}）' for x in r["by_agree"])
        rows += (f'<tr><td>{lab}</td><td class="num">{_pc(a["hit"])}（{a["n"]}日）{mark(f"ゴールド:{k}:単独")}</td>'
                 f'<td class="num">{_pc(v["hit"])}（{v["n"]}日）</td>'
                 f'<td class="num">{_pc(vg["hit"])}（{vg["n"]}日）{mark(f"ゴールド:{k}:v2追加")}<div class="muted tiny">平均 {_sg(vg["mean"])}・手出し無用 {_pc(vg["hands_off_share"])}</div></td>'
                 f'<td class="tiny">{agree}</td></tr>')
    c = g.get("corr") or {}
    note = ""
    if c.get("last_r") is not None:
        note = (f'直近60日の相関 r={c["last_r"]:+.2f}（記事の「ゴールド下げ＝上げ材料」は r がマイナスの関係）・'
                f'相関がマイナスだった期間 {_pc(c.get("share_negative"))}・材料に使えた日 {_pc(c.get("share_used"))}')
    return f"""
      <h3>ゴールドを材料に入れる（前日のゴールド先物 GC=F の騰落 ±0.3%。v2 との比較は後半 {g["split"]}〜）</h3>
      <table class="results"><thead><tr><th>数え方</th><th class="num">ゴールドだけで入る 的中</th><th class="num">v2</th>
        <th class="num">v2 ＋ゴールドが逆なら見送り</th><th>7材料の一致数ごとの的中（v2 で方向ありの日）</th></tr></thead><tbody>{rows}</tbody></table>
      <div class="muted tiny">{note}。効いていなければ判定には入れない（毎朝の判定は変えていない）。</div>"""


def render_idea_extra(x: dict, mark) -> str:
    """その2（しきい値・欧州・非対称・先物ジャンプ・米先物・10:30・個別の手仕舞い・月足・前日終値ライン・配当・値嵩株・指数の±3σ・手出し無用の日）"""
    if not x:
        return ""

    def row(label: str, cells: list[str]) -> str:
        return f"<tr><td>{label}</td>" + "".join(f'<td class="num">{c}</td>' for c in cells) + "</tr>"

    def s_cells(s: dict, name: str = "") -> list[str]:
        return [f'{s["n"]}', _pc(s.get("hit")) + (mark(name) if name else ""), _sg(s.get("mean"))]

    rows = ""
    for k, s in (x.get("threshold") or {}).items():
        rows += row(f"「変動なし」±{k}%（後半）", s_cells(s, f"しきい値:{k}") + [f'手出し無用 {_pc(s["hands_off_share"])}'])
    for k, lab in (("agree", "欧州も同じ向き"), ("against", "欧州が逆向き")):
        if k in (x.get("europe") or {}):
            rows += row(f"今の判定・{lab}（後半）", s_cells(x["europe"][k], f"欧州:{k}") + [""])
    for k, s in (x.get("asym") or {}).items():
        r9 = s.get("reach_9h") or {}
        rows += row(f"{k}の日（寄り→引け）", s_cells(s) + [f'9時台に0.5%進んだ {_pc(r9.get("rate"))}'])
    for k, s in (x.get("jump") or {}).items():
        rows += row(f"日経：{k}→寄り→10:00 に逆", s_cells(s, f"先物ジャンプ:{k}") + [""])
    for n, r in (x.get("jump_stocks") or {}).items():
        for k, s in r.items():
            rows += row(f"{n}：{k}→寄り→10:00 に逆", s_cells(s, f"先物ジャンプ:{n}:{k}") + [""])
    for k, s in (x.get("us_fut") or {}).items():
        rows += row(f"米先物（引け→9:00）と同じ向き：{k}", s_cells(s, f"米先物:{k}") + [""])
    ho = x.get("hands_off") or {}
    for k, s in ho.items():
        if k.startswith("gap_fade"):
            rows += row(f"手出し無用の日に窓と逆（寄り→10:00）{k[8:]}", s_cells(s, f"手出し無用:窓と逆{k[8:]}") + [""])
    table1 = (f'<table class="results"><thead><tr><th></th><th class="num">回数</th><th class="num">当たり</th>'
              f'<th class="num">平均</th><th class="num"></th></tr></thead><tbody>{rows}</tbody></table>')
    rows2 = ""
    cp = x.get("cp1030") or {}
    if cp:
        rows2 += row("節目の前後30分で向きが変わった割合", [f'{k} {_pc(v["rate"])}（{v["n"]}）' for k, v in cp.items() if k != "p_vs_others"])
    for n, r in (x.get("stock_week") or {}).items():
        rows2 += row(f"{n}：自分の週足・月足と逆（金曜／月末／値幅小の週／日経と逆の週）",
                     [f'{_pc(r["friday"]["all"]["hit"])}（{r["friday"]["all"]["n"]}）', f'{_pc(r["month_end"]["all"]["hit"])}（{r["month_end"]["all"]["n"]}）',
                      f'{_pc(r["small_week"]["hit"])}（{r["small_week"]["n"]}）{mark(f"銘柄の手仕舞い:{n}:値幅小の週")}',
                      f'{_pc(r["vs_nikkei_mixed"]["hit"])}（{r["vs_nikkei_mixed"]["n"]}）'])
    for n, r in (x.get("month_bar") or {}).items():
        rows2 += row(f"{n}：先月の月足とその日の上げ", [f'{k} {_pc(s["up"])}（{s["n"]}）' for k, s in r.items()])
    for n, s in (x.get("prev_line") or {}).items():
        rows2 += row(f"{n}：9時台に前日終値を割ったあと10:00に戻した", [f'{_pc(s["rate"])}（{s["n"]}）'])
    dv = x.get("dividend") or {}
    if dv.get("n"):
        rows2 += row("権利落ち日（3銘柄）：その日に下げた", [f'{k} {_pc(v["down"])}（{v["n"]}）' for k, v in dv.items() if k != "n"])
    for k, s in (x.get("big_caps") or {}).items():
        rows2 += row(f"値嵩株のキリ：{k}（参考）", [f'日経 上げ {_pc(s["up"])}（{s["n"]}）', f'安値からの戻り {_pc(s["rebound"])}'])
    for k, s in (x.get("index_dev") or {}).items():
        rows2 += row(f"ナスダック・SOX {k} の翌日の日経", [f'上げ {_pc(s["up"])}（{s["n"]}）', f'前場 {_sg(s["am_mean"])}・後場 {_sg(s["pm_mean"])}（{s["n_hourly"]}日）'])
    for k, r in ho.items():
        if k.startswith("pattern:"):
            rows2 += row(f"{k[8:]}：9〜10時の向きが10時以降も続く型", [f'{lab} {_pc(s["rate"])}（{s["n"]}）' for lab, s in r.items()])
    return f"""
      <h3>その2：判定のしかた・寄り後の動き</h3>{table1}
      <h3>その2：節目・個別の手仕舞い・月足・前日終値・配当・値嵩株・指数の±3σ・型の持続</h3>
      <table class="results"><tbody>{rows2}</tbody></table>
      <div class="muted tiny">日経平均の寄り値は、まだ寄っていない銘柄があるうちに計算されるので、日経の「寄り→10:00」は個別株より動きが大きく出やすい。
      値嵩株のキリは「その日の安値で触れたか」で分けているので、下げた日ほど触れやすい（参考。補正には入れない）。</div>"""


MIN_STOP_YEN = 3000     # マイルールの損切り「-3,000〜-5,000円」の下限。これより小さい幅しか取れない銘柄は100株では利益も小さい


def rule_fit_rows(screen: dict | None, morning: dict | None) -> list[dict]:
    """売買代金上位の銘柄を、100株・損切り-5,000円・利確は損切りの3倍・10時前 のルールに当てはめる"""
    from trade_plan import bracket
    ratios = [v["ratio"] for v in ((morning or {}).get("per_code") or {}).values() if v.get("ratio")]
    ratio = sum(ratios) / len(ratios) if ratios else 0.62
    out = []
    for r in (screen or {}).get("results", []):
        if not r.get("range_yen") or not r.get("price"):
            continue
        per = ((morning or {}).get("per_code") or {}).get(r["code"], {}).get("ratio") or ratio
        w = r["range_yen"] / 100 * per
        b = bracket(w)
        small = bool(b and b["stop_yen"] < MIN_STOP_YEN)
        fit = "◎" if (b and not b["capped"] and not small) else "△" if b and b["capped"] else "×"
        out.append({"code": r["code"], "name": r["name"], "price": r["price"], "turnover_oku": r.get("turnover_oku"),
                    "morning_width": w, "bracket": b, "fit": fit, "small": small})
    order = {"◎": 0, "△": 1, "×": 2}
    out.sort(key=lambda x: (order[x["fit"]], -(x["turnover_oku"] or 0)))
    return out


def render_level_section(levels: dict | None, screen: dict | None, morning: dict | None) -> str:
    title = "🧭 ルールに合う銘柄・上値／下値の目安は止まりやすいか"
    fit = rule_fit_rows(screen, morning)
    rows = ""
    for x in fit:
        b = x["bracket"] or {}
        why = ("損切り-5,000円では近すぎる（値動きが大きい）" if b.get("capped")
               else "値動きが小さい（100株では1回の利益が小さい）" if x["small"] else "")
        rows += (f'<tr><td>{x["fit"]} {x["name"]}<span class="muted tiny"> {x["code"]}</span></td>'
                 f'<td class="num">{x["price"]:,.0f}円</td><td class="num">{x["morning_width"]:.0f}円</td>'
                 f'<td class="num">-{b.get("stop_yen", 0):,.0f}円 / +{b.get("target_yen", 0):,.0f}円</td>'
                 f'<td class="num">{(x["turnover_oku"] or 0):,.0f}億円</td><td>{why}</td></tr>')
    lv = ""
    if levels and levels.get("kinds"):
        base = levels.get("control_bounce") or 0
        cells = ""
        for k in levels["kinds"]:
            if k["bounce"] is None:
                continue
            mark = "✅" if k["significant"] and k["diff"] > 0 else ("（止まりにくい）" if k["significant"] else "")
            ci = f'{k["diff"] * 100:+.0f}pt（95%区間 {k["ci"][0] * 100:+.0f}〜{k["ci"][1] * 100:+.0f}）' if k["ci"] else "件数不足"
            cells += f'<tr><td>{k["kind"]}</td><td class="num">{k["bounce"] * 100:.0f}%（{k["n"]}回）</td><td class="num">{ci} {mark}</td></tr>'
        lv = f"""
      <h3>上値・下値の目安で9時台に止まった割合（比較用のただの値段は {base * 100:.0f}%・{levels.get("n_control", 0)}回）</h3>
      <div class="period-note">{levels.get("source", "")}</div>
      <table class="results"><thead><tr><th>水準</th><th class="num">止まった</th><th class="num">比較用との差</th></tr></thead><tbody>{cells}</tbody></table>"""
    else:
        lv = '<div class="muted">上値・下値の目安の検証：まだ結果がありません</div>'
    return f"""
  <section class="section-box">
    <h2 class="section-title">{title}</h2>
    <div class="explainer" style="margin-top: 0;">
      <h3>100株・損切り-5,000円まで・利確は損切りの3倍・10時前 に合う銘柄（売買代金上位）</h3>
      <table class="results"><thead><tr><th>銘柄</th><th class="num">株価</th><th class="num">9時台の目安</th>
        <th class="num">損切り / 利確（100株）</th><th class="num">売買代金</th><th></th></tr></thead><tbody>{rows}</tbody></table>
      <div class="muted tiny">◎ 損切りが -3,000〜-5,000円に収まり、利確が9時台に届く ／ △ 値動きが大きく、-5,000円の損切りでは振り回されやすい ／ × 値動きが小さく、100株では取れる額が小さい。
      損切りは9時台の目安の1/3（上限50円）、利確はその3倍。承認・試用銘柄を決め直すときの参考（毎朝の画面には承認・試用銘柄だけ出す）。</div>
      {lv}
    </div>
  </section>
"""


def render_us_range_section(r: dict | None) -> str:
    """米関連株が大きく動いた翌日の値幅（目安の何倍か）"""
    pc = (r or {}).get("per_code") or {}
    if not pc:
        return ""
    heads = "".join(f'<th class="num">{b["bucket"]}</th>' for b in next(iter(pc.values()))["buckets"])
    rows = ""
    for c, x in pc.items():
        cells = ""
        for b in x["buckets"]:
            q = f'<div class="muted tiny">q={b["q"]:.2f}{" ✅" if b.get("significant") else ""}</div>' if b.get("q") is not None else ""
            cells += f'<td class="num">{(b["mean"] or 0):.2f}倍（{b["n"]}）{q}</td>'
        rows += f'<tr><td>{x["name"]}</td>{cells}</tr>'
    return f"""
  <section class="section-box">
    <h2 class="section-title">🇺🇸 米関連株が大きく動いた翌日の値幅</h2>
    <div class="period-note">{r["period"]}・決算明けの日は除く。値幅（高値−安値）が目安（直近{r["window"]}日平均）の何倍だったか。列は前日の米関連株の平均の動きの大きさ</div>
    <table class="results"><thead><tr><th></th>{heads}</tr></thead><tbody>{rows}</tbody></table>
    <div class="explainer"><ul><li>✅ の区分（1%未満の日より広く、補正後 q&lt;0.05）だけ、毎朝の値幅・作戦の幅にその倍率を掛ける。</li></ul></div>
  </section>
"""


def _yen(x: float | None, sign: bool = True) -> str:
    if x is None:
        return "—"
    return f"{x:+,.0f}円" if sign else f"{x:,.0f}円"


def _exit_rows(groups: list[tuple[str, dict]]) -> str:
    """決済方法の比較の表の行（グループ × (a)(b)(c)）"""
    labels = {"a": "(a) 10時終了", "b": "(b) 時間方式", "c": "(c) 1：3"}
    rows = ""
    for title, g in groups:
        if not g or not g.get("a", {}).get("n"):
            continue
        for i, m in enumerate(("a", "b", "c")):
            s = g[m]
            ci = f'<div class="muted tiny">95%区間 {_yen(s["mean_ci"][0])}〜{_yen(s["mean_ci"][1])}</div>' if s.get("mean_ci") else ""
            wci = f'<div class="muted tiny">{s["win_ci"][0] * 100:.0f}〜{s["win_ci"][1] * 100:.0f}%</div>' if s.get("win_ci") else ""
            diff = ""
            if m != "a" and g.get(f"{m}_minus_a"):
                d = g[f"{m}_minus_a"]
                dci = f'（{_yen(d["ci"][0])}〜{_yen(d["ci"][1])}）' if d.get("ci") else ""
                sig = " ✅" if d.get("ci") and (d["ci"][0] > 0 or d["ci"][1] < 0) else ""
                diff = f'{_yen(d["mean"])}<div class="muted tiny">{dci}{sig}</div>'
            head = f'<td rowspan="3"><b>{title}</b><div class="muted tiny">{s["n"]}回・{s["n_days"]}日</div></td>' if i == 0 else ""
            rows += (f'<tr>{head}<td>{labels[m]}</td><td class="num">{_yen(s["mean"])}{ci}</td>'
                     f'<td class="num">{_yen(s["median"])}</td><td class="num">{s["win_rate"] * 100:.0f}%{wci}</td>'
                     f'<td class="num">{s["max_consec_losses"]}</td><td class="num">{_yen(s["max_drawdown"], False)}</td>'
                     f'<td class="num">{s["loss_limit_days"]}日</td><td class="num">{_yen(s["total"])}</td>'
                     f'<td class="num">{diff or "—"}</td></tr>')
    return rows


def render_exit_section(ex: dict | None) -> str:
    """決済方法の比較（10時終了／時間方式／1：3）。L7-1。毎朝の画面とマイルールは変えない"""
    title = "🕙 決済方法の比較（10時終了／時間方式／1：3）"
    if not ex or not ex.get("all", {}).get("a", {}).get("n"):
        return f"""
  <section class="section-box">
    <h2 class="section-title">{title}</h2>
    <div class="muted">まだ結果がありません（判定が買い・売りの日の5分足がたまると出ます）</div>
  </section>
"""
    vl = {"buy_bias": "買いポジ主体", "sell_bias": "売りポジ主体"}
    cl = {"high": "確度 高", "medium": "確度 中", "low": "確度 低"}
    names = ex.get("names") or {}
    groups = [("全体（判定どおり）", ex["all"])]
    groups += [(vl.get(k, k), v) for k, v in ex.get("by_verdict", {}).items()]
    groups += [(cl.get(k, k), v) for k, v in ex.get("by_confidence", {}).items()]
    groups += [(names.get(k, k), v) for k, v in ex.get("by_code", {}).items()]
    if ex.get("hands_off_reference"):
        groups.append(("参考：手出し無用の日（寄り→9:05 の向き）", ex["hands_off_reference"]))
    rows = _exit_rows(groups)
    reasons = ex.get("exit_reasons") or {}
    rs = "／".join(f'{m}: ' + "・".join(f"{k} {v}" for k, v in reasons.get(m, {}).items()) for m in ("a", "b", "c") if reasons.get(m))

    act = ex.get("actual") or {}
    if act.get("n"):
        arows = ""
        for r in act["rows"]:
            side = lambda s: "買い" if s > 0 else "売り"
            slip = f'{r["entry_slip"]:+,.0f}円' if r.get("entry_slip") is not None else "—"
            arows += (f'<tr><td>{r["date"]}</td><td>{r["name"]}</td>'
                      f'<td>{side(r["actual_side"])} {r["actual_open"] or ""}→{r["actual_close"] or ""}</td>'
                      f'<td class="num">{_yen(r["actual_pnl100"])}</td>'
                      f'<td>{side(r["sim_side"])} 09:05→{r["sim_time"]}</td><td class="num">{_yen(r["sim_pnl"])}</td>'
                      f'<td class="num">{slip}</td><td class="num">{_yen(r["pnl_diff"])}</td></tr>')
        slip_txt = f'・入った値段の差の平均 {act["mean_entry_slip"]:+,.1f}円（プラスは実際のほうが不利）' if act.get("mean_entry_slip") is not None else ""
        actual = f"""
      <h3>実際の約定との差（daytrade-journal・{act["n"]}件、向きが同じ {act["same_side"]}件）</h3>
      <div class="period-note">その日その銘柄で 10:00 までに建てた最初の取引を100株あたりに直して、(a) と並べる。平均：実績 {_yen(act["mean_actual"])} ／ (a) {_yen(act["mean_sim"])}{slip_txt}</div>
      <table class="results"><thead><tr><th>日付</th><th>銘柄</th><th>実際</th><th class="num">実績（100株）</th>
        <th>(a)</th><th class="num">(a) の損益</th><th class="num">入値の差</th><th class="num">実績 − (a)</th></tr></thead><tbody>{arows}</tbody></table>"""
    else:
        actual = '<div class="muted tiny">実際の約定との差：同じ日・同じ銘柄の約定記録（振り返りメモの時刻つき）がまだありません</div>'

    return f"""
  <section class="section-box">
    <h2 class="section-title">{title}</h2>
    <div class="period-note">{ex.get("period", "")}・承認・試用銘柄の5分足（Yahoo・取れる範囲の60営業日）・100株・損切り -{ex["stop_yen"]:,}円・
      判定が手出し無用の日（{ex.get("n_hands_off_days", 0)}日／{ex.get("n_market_days", 0)}日）は見送り</div>
    <table class="results"><thead><tr><th></th><th>決済方法</th><th class="num">1回の平均</th><th class="num">中央値</th>
      <th class="num">勝率</th><th class="num">最大連敗</th><th class="num">最大DD</th><th class="num">-1万円の日</th>
      <th class="num">合計</th><th class="num">(a) との差</th></tr></thead><tbody>{rows}</tbody></table>
    <div class="explainer">
      <ul>
        <li>(a) 9:05 に判定の向きで入り、-{ex["stop_yen"]:,}円に触れたら決済、触れなければ 10:00 の値段で決済（今のルール）。</li>
        <li>(b) 10:00 までは (a) と同じ。10:00 に含み益なら、10:00 の値段から損切り幅ぶん不利な位置に逆指値を置き直し、15:25 までに触れたら決済、触れなければ引けで決済。</li>
        <li>(c) -{ex["stop_yen"]:,}円／+{ex["target_yen"]:,}円 の OCO。どちらにも触れなければ引けで決済。</li>
        <li>5分足の高値・安値で判定するため、同じ足で損切りと利確の両方に触れたら損切りが先とみなす（不利側）。手数料・スリッページは入れていない。</li>
        <li>件数が少ないので、平均と勝率には95%区間を付けた（平均は日を単位に選び直すブートストラップ）。「(a) との差」の ✅ は区間が0をまたがないもの。</li>
        <li>この結果を見て、利確の目安（損切り×3）の表示やマイルールをどうするかはユーザーが決める。この節は検証だけで、毎朝の画面とルールは変えない。</li>
      </ul>
      <div class="muted tiny">決済の内訳 {rs}</div>
      {actual}
    </div>
  </section>
"""


def render_round_section(r: dict | None) -> str:
    """大台の初回タッチ（L6-2・L7-3）と、節目を少し割ったところ（L6-3）"""
    title = "📏 大台・節目は止まりやすいか（初回タッチ・節目を少し割ったところ）"
    if not r:
        return f"""
  <section class="section-box"><h2 class="section-title">{title}</h2><div class="muted">まだ結果がありません</div></section>
"""
    pc = lambda g: "—" if not g or g.get("rate") is None else f'{g["rate"] * 100:.0f}%（{g["n"]}）'

    def diff_txt(d: dict) -> str:
        if not d or d.get("diff") is None:
            return "件数不足"
        return (f'{d["diff"] * 100:+.0f}pt（95%区間 {d["ci"][0] * 100:+.0f}〜{d["ci"][1] * 100:+.0f}）'
                + (" ✅" if d.get("significant") else ""))
    t = r.get("touch") or {}
    g = t.get("groups") or {}
    names = r.get("names") or {}
    vl = {"buy_bias": "買いポジ主体", "sell_bias": "売りポジ主体", "hands_off": "手出し無用", "不明": "判定不明"}
    use = {"buy_bias": "support", "sell_bias": "resistance"}
    vrows = ""
    for v, x in (t.get("by_verdict") or {}).items():
        sup, res = x.get("support", {}), x.get("resistance", {})
        star = lambda ap: " ◀" if use.get(v) == ap or v == "hands_off" else ""
        vrows += (f'<tr><td>{vl.get(v, v)}</td><td class="num">{pc(sup.get("first"))}{star("support")}<div class="muted tiny">全部 {pc(sup.get("all"))}</div></td>'
                  f'<td class="num">{pc(res.get("first"))}{star("resistance")}<div class="muted tiny">全部 {pc(res.get("all"))}</div></td></tr>')
    crows = "".join(f'<tr><td>{names.get(c, c)}</td><td class="num">{pc(x)}</td></tr>' for c, x in (t.get("by_code") or {}).items())
    d = r.get("dip") or {}
    dcode = "　".join(f'{names.get(c, c)} {pc(x)}' for c, x in (d.get("by_code") or {}).items())
    bn = r.get("big_number") or {}
    big_html = ""
    if (bn.get("all") or {}).get("n"):
        brow = ""
        for k, lab in (("focus", "承認・試用銘柄"), ("all", "＋日経225の値嵩株")):
            x = bn.get(k) or {}
            if not x.get("n"):
                continue
            brow += (f'<tr><td>{lab}</td><td class="num">{pc(x["round"])}<div class="muted tiny">押し戻された {pc(x["pushed_round"])}</div></td>'
                     f'<td class="num">{pc(x["control"])}<div class="muted tiny">押し戻された {pc(x["pushed_control"])}</div></td>'
                     f'<td class="num">{diff_txt(x)}</td></tr>')
        big_html = f"""
      <h3>株価の大台（1,000円・10,000円・5,000円の倍数）の手前 {bn.get("near_pct", 0) * 100:.0f}% に来たあと、{bn.get("horizon")}日以内に終値で抜けた割合（{bn.get("period", "")}）</h3>
      <table class="results"><thead><tr><th></th><th class="num">大台</th><th class="num">比較用（±7%ずらした値段）</th><th class="num">差</th></tr></thead><tbody>{brow}</tbody></table>
      <div class="muted tiny">記事（手法10）は「大台の手前ではいったん止まりやすい」。大台のほうが抜けた割合が低ければ、その通り。カードの「大台の手前」の1行に、この数字を添える。</div>"""
    return f"""
  <section class="section-box">
    <h2 class="section-title">{title}</h2>
    <div class="period-note">{r.get("source", "")}・止まった／抜けた は水準の {r.get("step_pct", 0) * 100:.2f}% 戻したか進んだか</div>
    <div class="explainer" style="margin-top: 0;">
      <h3>日経平均・1570・1357 の 1,000円の大台（比較用は 500円ずらした値段）</h3>
      <table class="results"><thead><tr><th></th><th class="num">初めて触れた</th><th class="num">2回目以降</th></tr></thead><tbody>
        <tr><td>大台</td><td class="num">{pc(g.get("big_first"))}</td><td class="num">{pc(g.get("big_later"))}</td></tr>
        <tr><td>比較用</td><td class="num">{pc(g.get("ctl_first"))}</td><td class="num">{pc(g.get("ctl_later"))}</td></tr>
      </tbody></table>
      <div class="muted tiny">大台の初回 − 比較用の初回：{diff_txt(t.get("first_vs_control"))} ／ 大台の初回 − 2回目以降：{diff_txt(t.get("first_vs_later"))}</div>
      <h3>その日の判定ごと（大台に触れたとき止まった割合。◀ は毎朝の節目欄で使うと出している側）</h3>
      <table class="results"><thead><tr><th>判定</th><th class="num">サポート（上から触れた）初回</th><th class="num">レジスタンス（下から触れた）初回</th></tr></thead><tbody>{vrows}</tbody></table>
      <table class="results"><thead><tr><th>銘柄</th><th class="num">大台の初回</th></tr></thead><tbody>{crows}</tbody></table>
      <h3>承認・試用銘柄：1,000円の節目を割ったあと、{r.get("dip_pct", 0) * 100:.1f}% より下に行かずに戻った割合</h3>
      <div>節目 {pc(d.get("round"))} ／ 比較用（節目 ±1.5% の値段）{pc(d.get("control"))}：差 {diff_txt(d)}</div>
      <div class="muted tiny">{dcode}。✅ になったら、カードに「急落時の反発候補：節目と その少し下」を出す（それまでは出さない）。</div>
      {big_html}
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
    morning_result: dict | None = None,
    checkpoint_result: dict | None = None,
    fib_result: dict | None = None,
    idea_result: dict | None = None,
    level_result: dict | None = None,
    us_range_result: dict | None = None,
    exit_result: dict | None = None,
    round_result: dict | None = None,
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
    morning_section = render_morning_section(morning_result)
    checkpoint_section = render_checkpoint_section(checkpoint_result)
    fib_section = render_fib_section(fib_result)
    idea_section = render_idea_section(idea_result)
    level_section = render_level_section(level_result, screen_result, morning_result)
    us_range_section = render_us_range_section(us_range_result)
    exit_section = render_exit_section(exit_result)
    round_section = render_round_section(round_result)
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
  {morning_section}
  {checkpoint_section}
  {fib_section}
  {level_section}
  {exit_section}
  {round_section}
  {us_range_section}
  {idea_section}

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
        "morning_range": morning_result,
        "checkpoints": checkpoint_result,
        "fibonacci": fib_result,
        "ideas": idea_result,
        "levels": level_result,
        "us_range": us_range_result,
        "exit_study": exit_result,
        "round_study": round_result,
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
    print("🔍 Running morning range validation (5m 60d)")
    morning_result = None
    try:
        from morning_range import run_morning_validation
        morning_result = run_morning_validation(period="2y")
    except Exception as e:
        print(f"⚠️ Morning range validation failed: {e}")

    print()
    print("🔍 Running checkpoint study (寄り・9:05・9:15・9:30・10:00)")
    checkpoint_result = None
    try:
        from checkpoint_study import run_checkpoint_study
        checkpoint_result = run_checkpoint_study()
    except Exception as e:
        print(f"⚠️ Checkpoint study failed: {e}")

    print()
    print("🔍 Running Fibonacci level study")
    fib_result = None
    try:
        from fib_study import run_fib_study
        fib_result = run_fib_study()
    except Exception as e:
        print(f"⚠️ Fibonacci study failed: {e}")

    print()
    print("🔍 Running US move → next-day range study")
    us_range_result = None
    try:
        from us_range_study import run_us_range_study
        us_range_result = run_us_range_study("5y")
    except Exception as e:
        print(f"⚠️ US range study failed: {e}")

    print()
    print("🔍 Running level study (上値・下値の目安)")
    level_result = None
    try:
        from level_study import run_level_study
        level_result = run_level_study()
    except Exception as e:
        print(f"⚠️ Level study failed: {e}")

    print()
    print("🔍 Running exit study (10時終了／時間方式／1：3)")
    exit_result = None
    try:
        from exit_study import run_exit_study
        exit_result = run_exit_study()
    except Exception as e:
        print(f"⚠️ Exit study failed: {e}")

    print()
    print("🔍 Running round-number study (大台の初回タッチ・節目割れ)")
    round_result = None
    try:
        from round_study import run_round_study
        round_result = run_round_study()
    except Exception as e:
        print(f"⚠️ Round study failed: {e}")

    print()
    print("🔍 Running idea study (メンサロ記事の考え方)")
    idea_result = None
    try:
        from idea_study import run_idea_study
        idea_result = run_idea_study()
    except Exception as e:
        print(f"⚠️ Idea study failed: {e}")

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
        morning_result=morning_result,
        checkpoint_result=checkpoint_result,
        fib_result=fib_result,
        idea_result=idea_result,
        level_result=level_result,
        us_range_result=us_range_result,
        exit_result=exit_result,
        round_result=round_result,
    )


if __name__ == "__main__":
    import sys

    period = sys.argv[1] if len(sys.argv) > 1 else "6mo"
    direction_period = sys.argv[2] if len(sys.argv) > 2 else "1y"
    run_backtest_and_report(period=period, direction_period=direction_period)
