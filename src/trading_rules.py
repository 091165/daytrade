"""
その日の判定 × マイルール 連動バナー

目的:
  方向予測そのものより「その日のタイプに応じて自分のルールを思い出す」ことを優先する。
  過去のトレード記録では、方向がはっきり出た日（買いポジ主体・売りポジ主体）ほど
  逆張り・塩持ち・承認外銘柄で大きく負けていた。予測が当たっていた日でも負けていた。
  そこで、トレンドが出る日ほどルールを強く表示する。

設計:
  純粋関数のみ（I/Oなし）。ルールの文言は定数で管理し、ここを書き換えれば反映される。
"""

from __future__ import annotations

from dataclasses import dataclass, asdict, field
from typing import Literal


# ----- マイルール（ここを編集すれば表示が変わる） -----

APPROVED_STOCKS: tuple[str, ...] = ("フジクラ(5803)", "SBG(9984)")
BANNED_STOCKS: tuple[str, ...] = ("太陽誘電(6976)", "イビデン(4062)", "アドバンテスト(6857)")
DAILY_LOSS_LIMIT_YEN = 10_000
PER_TRADE_STOP_YEN = "3,000〜5,000"
END_TIME = "10:00"
CHOP_WINDOW = "09:05〜09:15"

# 全日共通のルール（どの判定の日でも表示）
ALWAYS_RULES: tuple[str, ...] = (
    f"{END_TIME}前に「取引終了」を宣言する",
    f"1日 -{DAILY_LOSS_LIMIT_YEN:,}円 でその日は終了",
    f"触るのは {'・'.join(APPROVED_STOCKS)} だけ。{'・'.join(BANNED_STOCKS)} と承認外の銘柄は触らない",
    "注文画面で「日計り」になっているか毎回確認する",
)


DayType = Literal["trend_up", "trend_down", "no_trend"]
Tone = Literal["danger", "caution"]


@dataclass
class TradingRulesBanner:
    day_type: DayType
    tone: Tone                       # danger=赤（強く警告） caution=黄
    headline: str                    # 一番大きく出す一文
    subline: str                     # 補足（なぜ注意なのか）
    day_rules: list[str]             # その日タイプ専用のルール
    always_rules: list[str] = field(default_factory=lambda: list(ALWAYS_RULES))

    def as_dict(self) -> dict:
        return asdict(self)


def build_trading_rules(verdict: str, confidence: str = "medium") -> TradingRulesBanner:
    """日経方針の判定から、その日に強調すべきルールを組み立てる。

    Args:
        verdict: "buy_bias" / "sell_bias" / "hands_off"
        confidence: "high" / "medium" / "low"
    """
    trend_subline = (
        "方向がはっきり出る日は値動きが荒い。"
        "過去の記録では、予想が当たった日ほど逆張り・塩持ちで大きく負けている。"
    )

    if verdict == "buy_bias":
        banner = TradingRulesBanner(
            day_type="trend_up",
            tone="danger",
            headline="📈 トレンド日（上）：全部「買い」で統一する",
            subline=trend_subline,
            day_rules=[
                "売りは入れない（1銘柄だけ逆を張らない）",
                "下げている銘柄を拾わない（ナンピン禁止）",
                "ロットは普段の半分",
                f"逆行したら即損切り（-{PER_TRADE_STOP_YEN}円）。粘らない",
            ],
        )
    elif verdict == "sell_bias":
        banner = TradingRulesBanner(
            day_type="trend_down",
            tone="danger",
            headline="📉 トレンド日（下）：全部「売り」で統一する",
            subline=trend_subline,
            day_rules=[
                "買いは入れない（1銘柄だけ逆を張らない）",
                "落ちている銘柄を拾わない（落ちるナイフを掴まない）",
                "ロットは普段の半分",
                f"逆行したら即損切り（-{PER_TRADE_STOP_YEN}円）。粘らない",
            ],
        )
    else:
        banner = TradingRulesBanner(
            day_type="no_trend",
            tone="caution",
            headline="⏸️ 方向なしの日：承認銘柄だけ、小さく",
            subline="指数の方向が読めない日。やるなら最小単位で、取れたらすぐ終える。",
            day_rules=[
                f"{'・'.join(APPROVED_STOCKS)} の100株だけ",
                f"{CHOP_WINDOW} は方向が定まりにくいので様子見",
                "2回続けて損切りしたら、その日は終了",
            ],
        )

    if confidence == "low":
        banner.day_rules.append("判定の確度が低い日。迷ったら見送り")

    return banner
