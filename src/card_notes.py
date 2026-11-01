"""
承認・試用銘柄のカードに足す短い注意書き（純粋関数）

  - 25日線乖離・月足の向き（目安の文は、週次レポートで当たりが確かめられたときだけ）
  - 決算月（中間配当の月も）
  - 金曜・月末：その銘柄の今週（今月）の足の向き、値幅の小さい週、日経と向きが逆の週
  - 権利落ち日：1株配当と、前日終値に対する割合
  - SOX を関連に持つ銘柄で、SOX の向きと金曜・月末の警告の向きが逆の日

週次レポートの結果（backtest_report.json の ideas）を validation として受け取り、
効いていない目安は「検証：関係なし」と添える。
"""

from __future__ import annotations

from datetime import date

import pandas as pd

from stock_trend import completed, this_week_bar, trend_summary
from warnings_module import dividend_days, is_last_trading_day_of_month

SHORT_NAME = {"5803": "フジクラ", "9984": "SBG", "6981": "村田"}


def _significant(validation: dict | None, prefix: str) -> bool:
    return any(t["significant"] for t in (validation or {}).get("tests", []) if t["name"].startswith(prefix))


def _pct(x: float | None) -> str:
    return "—" if x is None else f"{x * 100:.0f}%"


def trend_note(ohlc: pd.DataFrame | None, today: date, code: str, validation: dict | None) -> dict | None:
    t = trend_summary(ohlc, today)
    if not t:
        return None
    name = SHORT_NAME.get(code, code)
    if not validation:
        tail = "（未検証）" if t["hint"] else ""
        return {"text": f'{t["text"]}{"。" + t["hint"] if t["hint"] else ""}{tail}', "level": "info"}
    if _significant(validation, f"乖離:{name}:") and t["hint"]:
        return {"text": f'{t["text"]}。{t["hint"]}（検証済み）', "level": "info"}
    return {"text": f'{t["text"]}（検証：乖離・月足とその日の寄り後の向きは関係なし）', "level": "muted"}


def fiscal_note(fiscal_month: int | None) -> dict | None:
    if not fiscal_month:
        return None
    half = (fiscal_month + 6 - 1) % 12 + 1
    return {"text": f"{fiscal_month}月決算（中間 {half}月）。権利付き最終日・権利落ち日は上の警告に出る", "level": "muted"}


def closing_note(ohlc: pd.DataFrame | None, today: date, code: str, nikkei_week_positive: bool | None,
                 validation: dict | None) -> dict | None:
    """金曜（または月末）だけ：この銘柄の今週の週足の向きと、記事の手仕舞いの考え方"""
    if ohlc is None or ohlc.empty or (today.weekday() != 4 and not is_last_trading_day_of_month(today)):
        return None
    w = this_week_bar(ohlc, today)
    if not w:
        return None
    side = "陽線（記事では手仕舞い売りに注意）" if w["up"] else "陰線（記事では買い戻しに注意）"
    parts = [f"今週の週足：{side}"]
    if w["small_range"]:
        parts.append("値幅の小さい週（記事では手仕舞いの力は弱め）")
    if nikkei_week_positive is not None and nikkei_week_positive != w["up"]:
        parts.append("日経と向きがバラバラ：見送り候補")
    sw = ((validation or {}).get("extra") or {}).get("stock_week", {}).get(SHORT_NAME.get(code, ""))
    ev = ""
    if sw:
        f = sw["friday"]["all"]
        ev = f"（検証：金曜に今週と逆に動いた日 {_pct(f['hit'])}・{f['n']}回"
        if w["small_range"]:
            s = sw["small_week"]
            ev += f"／値幅の小さい週 {_pct(s['hit'])}・{s['n']}回"
        ev += "）"
    return {"text": "。".join(parts) + ev, "level": "info"}


def dividend_note(today: date, dividend: float | None, prev_close: float | None) -> dict | None:
    """権利落ち日だけ：1株配当と、それが前日終値の何%か"""
    days = dividend_days(today.year, today.month) if today.month in (3, 6, 9, 12) else None
    if not days or today != days[1] or not dividend or not prev_close:
        return None
    pct = dividend / prev_close * 100
    return {"text": f"権利落ち日：1株配当 約{dividend:,.1f}円（前日終値の{pct:.2f}%）。寄りがこの分だけ下がるのが普通。"
                    "記事では「配当ほど下がらずに寄った銘柄はその日に下げやすい」（検証：44回で差なし）",
            "level": "info"}


def sox_priority_note(us_proxies: tuple[str, ...], sox_change_pct: float | None, closing_reversal_dir: int) -> dict | None:
    """SOX（SOXX）を関連に持つ銘柄で、SOX の向きと、金曜・月末の手仕舞いで予想される向きが逆のとき"""
    if not any(t in ("SOXX", "^SOX", "SMH") for t in us_proxies) or sox_change_pct is None or closing_reversal_dir == 0:
        return None
    sox_dir = 1 if sox_change_pct >= 0.3 else -1 if sox_change_pct <= -0.3 else 0
    if sox_dir == 0 or sox_dir == closing_reversal_dir:
        return None
    return {"text": f"SOX {sox_change_pct:+.1f}% と、金曜・月末の手仕舞いの向きが逆。記事では SOX の向きを優先"
                    "（カレンダー要因より強い）。過去の検証でも金曜の反転はほぼ五分",
            "level": "info"}


def closing_reversal_dir(today: date, week_positive: bool | None, month_positive: bool | None) -> int:
    """金曜・月末の手仕舞いで記事が予想する向き（+1=上げ / -1=下げ / 0=なし）"""
    if is_last_trading_day_of_month(today) and month_positive is not None:
        return -1 if month_positive else 1
    if today.weekday() == 4 and week_positive is not None:
        return -1 if week_positive else 1
    return 0


def build_card_notes(*, code: str, ohlc: pd.DataFrame | None, today: date, validation: dict | None,
                     fiscal_month: int | None, us_proxies: tuple[str, ...], sox_change_pct: float | None,
                     nikkei_week_positive: bool | None, nikkei_month_positive: bool | None,
                     dividend: float | None = None) -> list[dict]:
    prev_close = None
    if ohlc is not None and not ohlc.empty:
        done = completed(ohlc, today)
        prev_close = float(done["Close"].iloc[-1]) if not done.empty else None
    notes = [
        trend_note(ohlc, today, code, validation),
        closing_note(ohlc, today, code, nikkei_week_positive, validation),
        sox_priority_note(us_proxies, sox_change_pct, closing_reversal_dir(today, nikkei_week_positive, nikkei_month_positive)),
        dividend_note(today, dividend, prev_close),
        fiscal_note(fiscal_month),
    ]
    return [n for n in notes if n]
