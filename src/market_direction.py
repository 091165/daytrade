"""
本日の日経デイトレ方針を判定するモジュール

PDFの判定パターン:
  「買いポジ主体」: 米3指数が揃って上げ、日経先物が追随して上げ
  「売りポジ主体」: 米3指数が揃って下げ、日経先物が追随して下げ
  「初心者手出し無用」: 米日非連動、または米指数の個体差が大きい

PDFから抽出した典型例:
  「ダウ微上げ、SOX下げの個体差相場」 → 初心者手出し無用
  「アメリカ株の下げに対し、日経先物は微上げ」 → 初心者手出し無用（非連動）
  「ダウ以外はプラス終了」「先物は現物対比で+170」 → 買いポジ主体
  「米国3指数すべて下げ、引けにかけてさらに下げ」 → 売りポジ主体
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Literal


Direction = Literal["bullish", "bearish", "neutral"]


# 判定しきい値
STRONG_MOVE_THRESHOLD = 1.0    # ±1%以上の動きを「明確な方向性」とみなす
WEAK_MOVE_THRESHOLD = 0.3       # ±0.3%未満は実質フラットとみなす

# 米3指数の一致度（何指数が同方向なら連動とみなすか）
CONSENSUS_REQUIRED = 2          # 3指数中2つ以上が同方向で連動判定

# 現対のしきい値（先物が米日連動を確認する材料）
FUTURES_ALIGNMENT_THRESHOLD = 200   # 現対±200円以上でないと明確な方向性とは言えない


@dataclass
class IndexMove:
    """個別指数の動き"""
    name: str
    change_pct: float
    direction: Direction

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class MarketDirectionSignal:
    """本日の日経デイトレ方針"""
    verdict: Literal["buy_bias", "sell_bias", "hands_off"]   # 買いポジ主体/売りポジ主体/初心者手出し無用
    verdict_label: str
    confidence: Literal["high", "medium", "low"]              # 判定の確度
    us_indices: list[IndexMove]
    us_consensus_direction: Direction                         # 米3指数の多数決方向
    futures_diff: float                                        # 現対（先物 - 現物）
    futures_aligned: bool                                      # 先物が米の方向と一致しているか
    reasons: list[str]                                         # 判定根拠（箇条書き）

    def as_dict(self) -> dict:
        d = asdict(self)
        d["us_indices"] = [m.as_dict() if hasattr(m, "as_dict") else m for m in d["us_indices"]]
        return d


def classify_move(change_pct: float, threshold: float = WEAK_MOVE_THRESHOLD) -> Direction:
    """騰落率からdirectionを分類（±threshold 未満は「変動なし」）"""
    if change_pct >= threshold:
        return "bullish"
    if change_pct <= -threshold:
        return "bearish"
    return "neutral"


def _majority_direction(moves: list[IndexMove]) -> Direction:
    """多数決でのdirection"""
    bull = sum(1 for m in moves if m.direction == "bullish")
    bear = sum(1 for m in moves if m.direction == "bearish")
    if bull >= CONSENSUS_REQUIRED:
        return "bullish"
    if bear >= CONSENSUS_REQUIRED:
        return "bearish"
    return "neutral"


def build_market_direction(
    nasdaq_change_pct: float,
    dow_change_pct: float,
    sox_change_pct: float,
    futures_diff: float,
    us_closed: bool = False,
    weak_threshold: float = WEAK_MOVE_THRESHOLD,
) -> MarketDirectionSignal:
    """本日のデイトレ方針を判定する。

    us_closed=True（前日の米国が休場）のときは、米国の騰落が休場前の日のものなので
    連動の材料なしとして「初心者手出し無用」にする。

    Args:
        nasdaq_change_pct: 前日ナスダック騰落率(%)
        dow_change_pct: 前日ダウ騰落率(%)
        sox_change_pct: 前日SOX騰落率(%)
        futures_diff: 日経先物 − 日経現物（円）
    """
    indices = [
        IndexMove("ナスダック", nasdaq_change_pct, classify_move(nasdaq_change_pct, weak_threshold)),
        IndexMove("ダウ", dow_change_pct, classify_move(dow_change_pct, weak_threshold)),
        IndexMove("SOX", sox_change_pct, classify_move(sox_change_pct, weak_threshold)),
    ]

    us_direction = _majority_direction(indices)

    # 先物の方向
    if futures_diff >= FUTURES_ALIGNMENT_THRESHOLD:
        futures_direction: Direction = "bullish"
    elif futures_diff <= -FUTURES_ALIGNMENT_THRESHOLD:
        futures_direction = "bearish"
    else:
        futures_direction = "neutral"

    # 先物が米の方向に追随しているか
    futures_aligned = (
        us_direction != "neutral"
        and futures_direction == us_direction
    )

    reasons: list[str] = []

    # 米指数の状態
    if us_direction == "bullish":
        up_indices = [m.name for m in indices if m.direction == "bullish"]
        reasons.append(f"米指数が揃って上げ（{', '.join(up_indices)}）")
    elif us_direction == "bearish":
        down_indices = [m.name for m in indices if m.direction == "bearish"]
        reasons.append(f"米指数が揃って下げ（{', '.join(down_indices)}）")
    else:
        mixed = []
        for m in indices:
            if m.direction == "bullish":
                mixed.append(f"{m.name}+")
            elif m.direction == "bearish":
                mixed.append(f"{m.name}-")
            else:
                mixed.append(f"{m.name}={m.change_pct:+.2f}%")
        reasons.append(f"米指数に個体差あり（{' / '.join(mixed)}）")

    # 先物の状態
    if futures_aligned:
        sign = "+" if futures_diff > 0 else ""
        reasons.append(f"日経先物が米に追随（現対{sign}{futures_diff:.0f}円）")
    elif us_direction != "neutral" and futures_direction != "neutral" and futures_direction != us_direction:
        sign = "+" if futures_diff > 0 else ""
        if us_direction == "bullish":
            reasons.append(f"米強日弱の非連動（現対{sign}{futures_diff:.0f}円）")
        else:
            reasons.append(f"米弱日強の非連動（現対{sign}{futures_diff:.0f}円）")
    else:
        sign = "+" if futures_diff > 0 else ""
        reasons.append(f"先物の方向性が不明確（現対{sign}{futures_diff:.0f}円）")

    # 判定
    # 強いシグナル: 米3指数の平均絶対値が強い + 米連動 + 先物追随
    us_avg = (nasdaq_change_pct + dow_change_pct + sox_change_pct) / 3
    strong_move = abs(us_avg) >= STRONG_MOVE_THRESHOLD

    if us_closed:
        verdict = "hands_off"
        verdict_label = "初心者手出し無用"
        confidence = "low"
        reasons.append("→ 前日の米国は休場。米国の騰落は休場前の日のもので、連動の材料にならない")
    elif us_direction == "bullish" and futures_aligned:
        verdict = "buy_bias"
        verdict_label = "買いポジ主体"
        confidence = "high" if strong_move else "medium"
        reasons.append("→ 米日連動の上昇相場として買い目線")
    elif us_direction == "bearish" and futures_aligned:
        verdict = "sell_bias"
        verdict_label = "売りポジ主体"
        confidence = "high" if strong_move else "medium"
        reasons.append("→ 米日連動の下落相場として売り目線")
    elif us_direction != "neutral" and not futures_aligned:
        # 米が方向性ありだが先物追随せず
        verdict = "hands_off"
        verdict_label = "初心者手出し無用"
        confidence = "low"
        reasons.append("→ 米日非連動のため、方向性読みづらく様子見")
    else:
        # 米の個体差が大きい または 全て中立
        verdict = "hands_off"
        verdict_label = "初心者手出し無用"
        confidence = "low"
        if us_direction == "neutral":
            reasons.append("→ 米指数の個体差が大きく、レンジ・優柔不断相場想定")

    return MarketDirectionSignal(
        verdict=verdict,
        verdict_label=verdict_label,
        confidence=confidence,
        us_indices=indices,
        us_consensus_direction=us_direction,
        futures_diff=futures_diff,
        futures_aligned=futures_aligned,
        reasons=reasons,
    )


# ========== 拡張版（ドル円・S&P500 入り）: 検証用。毎朝の判定はまだ現行版 ==========

USDJPY_THRESHOLD = 0.3          # ドル円 ±0.3% 以上で向きありとみなす（円安＝日本株に追い風）
CONSENSUS_REQUIRED_V2 = 3       # 米4指数中3つ以上が同方向で連動判定


@dataclass
class MarketDirectionV2:
    verdict: Literal["buy_bias", "sell_bias", "hands_off"]
    us_direction: Direction       # 米4指数（ナス・ダウ・SOX・S&P500）の多数決
    usdjpy_direction: Direction
    agreement_count: int          # 6材料（米4指数・ドル円・現対）のうち、多い側の向きにそろった数
    agreement_direction: Direction

    def as_dict(self) -> dict:
        return asdict(self)


def build_market_direction_v2(
    nasdaq_change_pct: float,
    dow_change_pct: float,
    sox_change_pct: float,
    sp500_change_pct: float,
    usdjpy_change_pct: float,
    futures_diff: float,
) -> MarketDirectionV2:
    """米4指数の3つ以上がそろい、現対が追随し、ドル円が逆を向いていなければ方向あり。"""
    us = [classify_move(x) for x in (nasdaq_change_pct, dow_change_pct, sox_change_pct, sp500_change_pct)]
    if us.count("bullish") >= CONSENSUS_REQUIRED_V2:
        us_dir: Direction = "bullish"
    elif us.count("bearish") >= CONSENSUS_REQUIRED_V2:
        us_dir = "bearish"
    else:
        us_dir = "neutral"
    fx: Direction = ("bullish" if usdjpy_change_pct >= USDJPY_THRESHOLD
                     else "bearish" if usdjpy_change_pct <= -USDJPY_THRESHOLD else "neutral")
    fut: Direction = ("bullish" if futures_diff >= FUTURES_ALIGNMENT_THRESHOLD
                      else "bearish" if futures_diff <= -FUTURES_ALIGNMENT_THRESHOLD else "neutral")
    materials = us + [fx, fut]
    bull, bear = materials.count("bullish"), materials.count("bearish")
    agree_dir: Direction = "bullish" if bull > bear else "bearish" if bear > bull else "neutral"
    opposite = {"bullish": "bearish", "bearish": "bullish"}
    if us_dir != "neutral" and fut == us_dir and fx != opposite[us_dir]:
        verdict = "buy_bias" if us_dir == "bullish" else "sell_bias"
    else:
        verdict = "hands_off"
    return MarketDirectionV2(verdict, us_dir, fx, max(bull, bear), agree_dir)
