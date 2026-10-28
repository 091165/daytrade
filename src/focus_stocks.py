"""
承認銘柄（フジクラ・SBG）と試用銘柄（村田製作所）の個別方向予測

前日の米国関連株の動きから、翌朝の各銘柄の方向を出す。

重要な区別:
  米国市場の動きが日本株に効くのは主に「寄り付きの窓（ギャップ）」。
  デイトレで取れるのは寄り後（寄り→引け）の値動きだけなので、
  バックテストでは「窓」と「寄り後」を分けて検証し、表示も分ける。
  検証結果がない・弱い場合は、その旨をそのまま表示する。
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Literal


Direction = Literal["bullish", "bearish", "neutral"]
Strength = Literal["strong", "weak", "neutral"]

# 個別株は指数よりボラが大きいので、しきい値はセクターより広め
WEAK_MOVE_THRESHOLD = 1.0     # 米関連株の平均が ±1.0%以上 → 弱いシグナル
STRONG_MOVE_THRESHOLD = 2.5   # ±2.5%以上 → 強いシグナル

# 検証の判定しきい値（focus_backtest と共有）
CONFIRMED_R = 0.2
WEAK_R = 0.1
SIGNIFICANT_P = 0.05


@dataclass(frozen=True)
class FocusStock:
    code: str                      # "5803"
    ticker: str                    # yfinance用 "5803.T"
    name: str
    us_proxies: tuple[str, ...]    # 連動を見る米国ティッカー
    us_label: str
    note: str
    trial: bool = False            # 試用銘柄（条件つきで試し、回数がたまったら承認を決め直す）


FOCUS_STOCKS: list[FocusStock] = [
    FocusStock(
        code="5803",
        ticker="5803.T",
        name="フジクラ",
        us_proxies=("GLW", "COHR", "LITE", "VRT"),
        us_label="光ファイバー・AIデータセンター（コーニング・コヒレント・ルメンタム・バーティブ）",
        note="AIデータセンター向け光ファイバー需要で動く。建機株（CAT等）より光通信・DC関連が近い",
    ),
    FocusStock(
        code="9984",
        ticker="9984.T",
        name="ソフトバンクG",
        us_proxies=("ARM", "NVDA", "QQQ"),
        us_label="Arm・NVIDIA・ナスダック100",
        note="保有株Arm（約9割保有）とAI関連の値動きに敏感",
    ),
    FocusStock(
        code="6981",
        ticker="6981.T",
        name="村田製作所",
        us_proxies=("SOXX", "QQQ", "NVDA"),
        us_label="半導体ETF・ナスダック100・NVIDIA（AIサーバー向け電子部品）",
        note="試用銘柄。100株だけ・10時前だけ・-5,000円で即損切り。値動きは承認銘柄の約2倍",
        trial=True,
    ),
]


@dataclass
class FocusSignal:
    code: str
    name: str
    us_label: str
    us_avg_change_pct: float
    us_changes: dict[str, float]           # 各ティッカーの騰落率
    direction: Direction
    strength: Strength
    gap_text: str                          # 寄り（窓）についての一文
    intraday_text: str                     # 寄り後についての一文
    conflict_with_market: bool             # 指数判定と逆向きか
    conflict_text: str
    validation: dict | None                # バックテスト結果（なければNone）
    note: str
    trial: bool = False

    def as_dict(self) -> dict:
        return asdict(self)


def classify_move(avg_pct: float) -> tuple[Direction, Strength]:
    a = abs(avg_pct)
    if a < WEAK_MOVE_THRESHOLD:
        return "neutral", "neutral"
    d: Direction = "bullish" if avg_pct > 0 else "bearish"
    return d, ("strong" if a >= STRONG_MOVE_THRESHOLD else "weak")


def classify_link(r: float | None, p: float | None) -> Literal["confirmed", "weak", "none", "reverse", "unknown"]:
    """相関係数とp値から連動の種類を判定する。

    reverse: 有意な負の相関（米が上げると逆に下がりやすい = 寄り天・寄り底型）
    """
    if r is None or p is None:
        return "unknown"
    if r <= -CONFIRMED_R and p < SIGNIFICANT_P:
        return "reverse"
    if r >= CONFIRMED_R and p < SIGNIFICANT_P:
        return "confirmed"
    if r >= WEAK_R:
        return "weak"
    return "none"


def _gap_text(direction: Direction, link: str, r: float | None) -> str:
    if direction == "neutral":
        return "米関連株は小動き。寄りの方向感なし"
    up = direction == "bullish"
    where = "高く" if up else "安く"
    r_txt = f"（r={r:+.2f}）" if r is not None else ""
    r_in = f" r={r:+.2f}" if r is not None else ""
    if link == "confirmed":
        return f"寄りは{where}始まりやすい（窓の連動：検証済み{r_in}）"
    if link == "weak":
        return f"寄りは{where}始まる傾向（窓の連動は弱め{r_in}）"
    if link == "unknown":
        return f"寄りは{where}始まる想定（未検証。週次バックテスト後に確度を表示）"
    return f"米との連動が確認できていない{r_txt}。参考程度"


def _intraday_text(direction: Direction, link: str, r: float | None) -> str:
    if direction == "neutral":
        return "寄り後も手がかりなし"
    r_txt = f"（r={r:+.2f}）" if r is not None else ""
    r_in = f" r={r:+.2f}" if r is not None else ""
    if link == "confirmed":
        side = "上" if direction == "bullish" else "下"
        return f"寄り後も{side}に動きやすい（検証済み{r_in}）"
    if link == "reverse":
        kind = "寄り天" if direction == "bullish" else "寄り底"
        return f"寄り後は逆に動きやすい{r_txt}。{kind}に注意、寄りで飛びつかない"
    if link == "unknown":
        return "寄り後の方向は未検証"
    return f"寄り後の方向は読めない{r_txt}。寄り値で飛びつかない"


def build_focus_signal(
    stock: FocusStock,
    us_changes: dict[str, float],
    market_verdict: str,
    validation: dict | None = None,
) -> FocusSignal:
    """1銘柄分の個別予測を組み立てる。

    Args:
        stock: 対象銘柄
        us_changes: 米ティッカー → 前日騰落率(%)
        market_verdict: 日経方針（buy_bias / sell_bias / hands_off）
        validation: focus_backtest の結果辞書（gap_r, gap_p, intraday_r, intraday_p など）
    """
    used = {t: us_changes[t] for t in stock.us_proxies if t in us_changes}
    avg = sum(used.values()) / len(used) if used else 0.0
    direction, strength = classify_move(avg)

    v = validation or {}
    gap_link = classify_link(v.get("gap_r"), v.get("gap_p")) if validation else "unknown"
    intra_link = classify_link(v.get("intraday_r"), v.get("intraday_p")) if validation else "unknown"

    conflict = (
        (market_verdict == "buy_bias" and direction == "bearish")
        or (market_verdict == "sell_bias" and direction == "bullish")
    )
    conflict_text = (
        "指数の判定と逆向き。今日はこの銘柄を見送る候補（1銘柄だけ逆を張らない）"
        if conflict else ""
    )

    return FocusSignal(
        code=stock.code,
        name=stock.name,
        us_label=stock.us_label,
        us_avg_change_pct=avg,
        us_changes=used,
        direction=direction,
        strength=strength,
        gap_text=_gap_text(direction, gap_link, v.get("gap_r")),
        intraday_text=_intraday_text(direction, intra_link, v.get("intraday_r")),
        conflict_with_market=conflict,
        conflict_text=conflict_text,
        validation=validation,
        note=stock.note,
        trial=stock.trial,
    )


def build_all_focus_signals(
    us_changes: dict[str, float],
    market_verdict: str,
    validation_by_code: dict[str, dict] | None = None,
) -> list[FocusSignal]:
    vmap = validation_by_code or {}
    return [
        build_focus_signal(s, us_changes, market_verdict, vmap.get(s.code))
        for s in FOCUS_STOCKS
    ]


def get_focus_us_tickers() -> list[str]:
    tickers: set[str] = set()
    for s in FOCUS_STOCKS:
        tickers.update(s.us_proxies)
    return sorted(tickers)
