"""
その日の判定 × マイルール 連動バナー

目的:
  方向予測そのものより「その日のタイプに応じて自分のルールを思い出す」ことを優先する。
  過去のトレード記録では、方向がはっきり出た日（買いポジ主体・売りポジ主体）ほど
  逆張り・塩持ち・承認外銘柄で大きく負けていた。予測が当たっていた日でも負けていた。
  そこで、トレンドが出る日ほどルールを強く表示する。

設計:
  純粋関数のみ（I/Oなし）。ルールの文言は定数で管理し、ここを書き換えれば反映される。
  文言とは別に、トレード記録と機械的に突き合わせられる形（checks）も出す。
  daytrade-journal の振り返りページが、これを使ってルールを守れたかを判定する。
"""

from __future__ import annotations

from dataclasses import dataclass, asdict, field
from typing import Literal


# ----- マイルール（ここを編集すれば表示が変わる） -----

_APPROVED: tuple[tuple[str, str], ...] = (("フジクラ", "5803"), ("SBG", "9984"))
APPROVED_STOCKS: tuple[str, ...] = tuple(f"{name}({code})" for name, code in _APPROVED)
APPROVED_CODES: tuple[str, ...] = tuple(code for _, code in _APPROVED)
# 試用銘柄: 条件つきで試し、TRIAL_REVIEW_AFTER 回たまったら承認するか決め直す
_TRIAL: tuple[tuple[str, str], ...] = (("村田", "6981"),)
TRIAL_STOCKS: tuple[str, ...] = tuple(f"{name}({code})" for name, code in _TRIAL)
TRIAL_CODES: tuple[str, ...] = tuple(code for _, code in _TRIAL)
TRIAL_SINCE = "2026-10-29"
TRIAL_MAX_QTY = 100
TRIAL_REVIEW_AFTER = 20
BANNED_STOCKS: tuple[str, ...] = ("太陽誘電(6976)", "イビデン(4062)", "アドバンテスト(6857)")
DAILY_LOSS_LIMIT_YEN = 10_000
PER_TRADE_STOP_MAX_YEN = 5_000
PER_TRADE_STOP_YEN = f"3,000〜{PER_TRADE_STOP_MAX_YEN:,}"
MAX_QTY = 100               # 毎日：1回の株数の上限（200株以上は使わない）
MAX_CONSECUTIVE_LOSSES = 2
# 利確の目安 = 損切り幅 × 1.5（表示だけ。判定には使わない）
# 2026/11/4 の検証（20銘柄・5分足60日・9:10に入る）：1・1.5・2・3倍のどれでも平均の損益はほぼ0。
# 10:00 までに決着した割合は 90%・79%・70%・58% で、3倍は逆張りで底・天井を拾わないと届きにくいので 1.5倍にした
REWARD_RISK_RATIO = 1.5
END_TIME = "10:00"
CHOP_WINDOW = "09:05〜09:15"   # 2026/11/4 まで「方向なしの日」に使っていた様子見の時間（今は DECIDE_AT に統一）
DECIDE_AT = "09:10"           # 方向を決める時刻。これより前は入らない（最初の10分を見る）

# 全日共通のルール（どの判定の日でも表示）
ALWAYS_RULES: tuple[str, ...] = (
    f"{DECIDE_AT}までは入らない。最初の10分の値幅が小さい日は、その向きに順張り（銘柄ページの「9:10 の確認」）",
    "寄りから大きく動いた銘柄を、逆向きに拾わない（落ちるナイフを掴まない）",
    f"{END_TIME}前に「取引終了」を宣言する",
    f"1日 -{DAILY_LOSS_LIMIT_YEN:,}円 でその日は終了",
    f"株数は{MAX_QTY}株まで（200株以上は使わない）",
    f"{MAX_CONSECUTIVE_LOSSES}回続けて損切りしたら、その日は終了",
    f"触るのは {'・'.join(APPROVED_STOCKS)} だけ。{'・'.join(BANNED_STOCKS)} と承認外の銘柄は触らない",
    f"試用：{'・'.join(TRIAL_STOCKS)} は {TRIAL_MAX_QTY}株だけ・{END_TIME}前だけ・-{PER_TRADE_STOP_MAX_YEN:,}円で即損切り",
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
    checks: dict = field(default_factory=dict)  # トレード記録との突き合わせ用（build_checks）

    def as_dict(self) -> dict:
        return asdict(self)


def build_checks(day_type: DayType) -> dict:
    """ルールのうち、約定記録から機械的に判定できるものを数値で返す。

    該当しないルールは None。2026/11/5 から、方向は寄り前の判定ではなく 9:10 に決める（direction は使わない）。
    銘柄ごとの「最初の10分が小さい」の境目と「大きく動いた」の幅は、毎朝の作戦から main で first10 に入れる。
    """
    return {
        "approved_codes": list(APPROVED_CODES),
        "daily_loss_limit_yen": DAILY_LOSS_LIMIT_YEN,
        "end_time": END_TIME,
        "require_day_trade": True,  # 期限が「日計り」であること
        "direction": None,
        "decide_at": DECIDE_AT,
        "first10": {},
        "per_trade_stop_yen": PER_TRADE_STOP_MAX_YEN,
        "max_qty": MAX_QTY,
        "wait_window": None,
        "max_consecutive_losses": MAX_CONSECUTIVE_LOSSES,
        "trial": {
            "codes": list(TRIAL_CODES),
            "names": [name for name, _ in _TRIAL],
            "since": TRIAL_SINCE,
            "max_qty": TRIAL_MAX_QTY,
            "stop_yen": PER_TRADE_STOP_MAX_YEN,
            "review_after": TRIAL_REVIEW_AFTER,
        },
    }


def build_trading_rules(verdict: str, confidence: str = "medium") -> TradingRulesBanner:
    """日経方針の判定から、その日に強調すべきルールを組み立てる。

    Args:
        verdict: "buy_bias" / "sell_bias" / "hands_off"
        confidence: "high" / "medium" / "low"
    """
    trend_subline = (
        "寄り前の判定は日経平均には少し効くが、承認・試用銘柄の寄り後の向きはほぼ五分（検証）。"
        f"向きは {DECIDE_AT} に、最初の10分を見て決める。"
    )
    stop_rule = f"逆行したら即損切り（-{PER_TRADE_STOP_YEN}円）。粘らない"

    if verdict == "buy_bias":
        banner = TradingRulesBanner(
            day_type="trend_up",
            tone="caution",
            headline="📈 米国株は上げ（寄り前の判定・参考）。向きは 9:10 に決める",
            subline=trend_subline,
            day_rules=[
                "上げやすい地合いでも、寄りから下げている銘柄を買いで拾わない",
                stop_rule,
            ],
        )
    elif verdict == "sell_bias":
        banner = TradingRulesBanner(
            day_type="trend_down",
            tone="caution",
            headline="📉 米国株は下げ（寄り前の判定・参考）。向きは 9:10 に決める",
            subline=trend_subline,
            day_rules=[
                "下げやすい地合いでも、寄りから上げている銘柄を売りで向かわない",
                stop_rule,
            ],
        )
    else:
        banner = TradingRulesBanner(
            day_type="no_trend",
            tone="caution",
            headline="⏸️ 方向なしの日：承認銘柄だけ、小さく",
            subline="指数の方向が読めない日。やるなら最小単位で、取れたらすぐ終える。",
            day_rules=[
                f"{'・'.join(APPROVED_STOCKS)} だけ。1回取れたら終える",
                stop_rule,
            ],
        )

    if confidence == "low":
        banner.day_rules.append("判定の確度が低い日。迷ったら見送り")

    banner.checks = build_checks(banner.day_type)

    return banner
