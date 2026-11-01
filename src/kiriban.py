"""
キリバン値幅・現対・朝一鞘取りシグナル計算コア

Reference:
  - 前日終値±500/±1000/±1500円がレジサポになる（キリバン値幅アノマリー）
  - 現対（日経先物 - 日経現物）が±1000円以上のハイボラ時に特に機能
  - 現対の方向に応じて朝一鞘取り（逆張り）シグナル
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Literal


# 鞘取り発動のしきい値（円）。PDFでは「現対±1000円以上のハイボラ時」を基準にしている。
SAYATORI_THRESHOLD = 1000

# キリバン値幅のレベル（前日終値からの差分）。
KIRIBAN_LEVELS = [-1500, -1000, -500, 500, 1000, 1500]

# 株価キリバン節目（丸い数字）の粒度。日経なら1000円単位が通常。
ROUND_NUMBER_STEP = 1000


Direction = Literal["long", "short", "neutral"]


@dataclass
class KiribanBands:
    """前日終値ベースのキリバン値幅水準"""
    prev_close: float
    bands: dict[str, float]  # "+500" -> 価格, "-1000" -> 価格, ...

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class SayatoriSignal:
    """朝一先物鞘取りシグナル"""
    futures_price: float
    spot_price: float
    diff: float  # 先物 - 現物
    abs_diff: float
    direction: Direction  # long=鞘取りロング, short=鞘取りショート, neutral=発動なし
    threshold: int
    rationale: str

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class RoundNumberLevels:
    """株価キリバン節目（普通の丸い数字）"""
    current_price: float
    resistance: float  # 現在価格の上側で一番近い丸い数字
    support: float     # 現在価格の下側で一番近い丸い数字
    step: int

    def as_dict(self) -> dict:
        return asdict(self)


def calc_kiriban_bands(prev_close: float) -> KiribanBands:
    """前日終値から ±500/±1000/±1500 のキリバン値幅を算出する。

    PDFロジック:
      「前日比対で+500円、+1000円、-500円、-1000円などが天底の目安」
    """
    bands = {f"{'+' if lv > 0 else ''}{lv}": prev_close + lv for lv in KIRIBAN_LEVELS}
    return KiribanBands(prev_close=prev_close, bands=bands)


def calc_sayatori_signal(
    futures_price: float,
    spot_price: float,
    threshold: int = SAYATORI_THRESHOLD,
) -> SayatoriSignal:
    """朝一先物鞘取りシグナルを計算する。

    ロジック:
      現対 = 先物 - 現物
      現対 >= +threshold → ショート（先物が高すぎる → 鞘が閉じる方向を狙う）
      現対 <= -threshold → ロング（先物が安すぎる → 鞘が閉じる方向を狙う）
      それ以外           → ニュートラル（鞘取り不要）
    """
    diff = futures_price - spot_price
    abs_diff = abs(diff)

    if diff >= threshold:
        direction: Direction = "short"
        rationale = (
            f"現対+{diff:,.0f}円。先物が現物より{abs_diff:,.0f}円高い。"
            f"鞘が閉じる方向（先物売り）を朝一で狙う。"
        )
    elif diff <= -threshold:
        direction = "long"
        rationale = (
            f"現対{diff:,.0f}円。先物が現物より{abs_diff:,.0f}円安い。"
            f"鞘が閉じる方向（先物買い）を朝一で狙う。"
        )
    else:
        direction = "neutral"
        rationale = (
            f"現対{diff:+,.0f}円（しきい値±{threshold:,}円未満）。"
            f"鞘取り不要。ハイボラ時のみ機能する戦略のため様子見。"
        )

    return SayatoriSignal(
        futures_price=futures_price,
        spot_price=spot_price,
        diff=diff,
        abs_diff=abs_diff,
        direction=direction,
        threshold=threshold,
        rationale=rationale,
    )


def calc_round_number_levels(
    current_price: float,
    step: int = ROUND_NUMBER_STEP,
) -> RoundNumberLevels:
    """現在価格の上下最近接の丸い数字レジサポ（株価キリバン節目）を算出する。"""
    support = (int(current_price) // step) * step
    resistance = support + step
    return RoundNumberLevels(
        current_price=current_price,
        resistance=float(resistance),
        support=float(support),
        step=step,
    )


def is_high_volatility(sayatori: SayatoriSignal, vol_threshold: int = SAYATORI_THRESHOLD) -> bool:
    """ハイボラ判定。現対絶対値がしきい値以上ならハイボラとみなす。

    PDFロジック:
      「先物の現対±1000円以上のハイボラ時に、こうしたキリバン値幅は機能しやすい」
    """
    return sayatori.abs_diff >= vol_threshold


def build_daily_signal(
    nikkei_prev_close: float,
    nikkei_futures: float,
    nikkei_spot_now: float | None = None,
) -> dict:
    """1日分のシグナルをまとめて組み立てる。

    Args:
        nikkei_prev_close: 日経225現物の前日終値（キリバン値幅の基準）
        nikkei_futures: 寄り前の日経225先物価格（現対算出）
        nikkei_spot_now: 現在の日経225現物価格（朝寄り前は前日終値で代用）

    Returns:
        JSON化可能な辞書
    """
    spot = nikkei_spot_now if nikkei_spot_now is not None else nikkei_prev_close

    kiriban = calc_kiriban_bands(nikkei_prev_close)
    sayatori = calc_sayatori_signal(nikkei_futures, spot)
    round_levels = calc_round_number_levels(nikkei_prev_close)
    hivol = is_high_volatility(sayatori)

    return {
        "kiriban_bands": kiriban.as_dict(),
        "sayatori_signal": sayatori.as_dict(),
        "round_number_levels": round_levels.as_dict(),
        "high_volatility": hivol,
        "kiriban_effective": hivol,  # ハイボラ時のみキリバン値幅が機能
    }


# ----- 節目の使い方を、その日の判定で分ける（過去ログ7・L7-3。L6-2 の節目欄の見出しに出す） -----

KIRIBAN_DAY_HINTS = {
    "buy_bias": "今日はサポート（下の節目）の買いが中心。上の節目での売りは控えめに",
    "sell_bias": "今日はレジスタンス（上の節目）の売りが中心。下の節目での買いは控えめに",
    "hands_off": "レンジになりやすい日：上下の節目で逆張り",
}


def kiriban_day_hint(verdict: str) -> str:
    """節目欄の見出しに添える1行。トレンド日は判定方向と同じ向きの売買だけ（押し目買い・戻り売り）を出し、
    逆張りの文は手出し無用の日だけ（トレンド日の「逆張り禁止」と矛盾させない）"""
    return KIRIBAN_DAY_HINTS.get(verdict, "")
