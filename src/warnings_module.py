"""
手仕舞い警告判定モジュール

PDFから抽出した警告パターン:
  - 金曜の週足手仕舞い: 日経週足が陽線なら後場の手仕舞い売り警戒
                       日経週足が陰線ならショートカバー買い警戒
  - 月末の月足手仕舞い: 日経月足が陽線なら手仕舞い売り警戒（金曜より重い）
  - 3連休前: 祝日前営業日は手仕舞いが入りやすい
  - 月初: 「月初めという節目での下げ → 月全体に波及する恐れ」
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import date, timedelta
from typing import Literal


Severity = Literal["high", "medium", "low"]


@dataclass
class TradingWarning:
    """1つの警告"""
    type: str                     # "friday_closing" / "month_end" / "pre_holiday" / "month_start"
    severity: Severity
    label: str                    # 見出し
    message: str                  # 本文
    action: str                   # 推奨アクション
    evidence: str = ""            # 過去データでの検証結果（週次レポート）。空なら表示しない

    def as_dict(self) -> dict:
        return asdict(self)


# ========== 日本の祝日判定 ==========

def _days_from_second_monday(d: date) -> int:
    """その月の第2月曜日からの日数（負値=まだ来ていない）"""
    first = date(d.year, d.month, 1)
    # 第2月曜 = 最初の月曜 + 7日
    first_monday_offset = (0 - first.weekday()) % 7
    second_monday = first + timedelta(days=first_monday_offset + 7)
    return (d - second_monday).days


def _days_from_third_monday(d: date) -> int:
    first = date(d.year, d.month, 1)
    first_monday_offset = (0 - first.weekday()) % 7
    third_monday = first + timedelta(days=first_monday_offset + 14)
    return (d - third_monday).days


def is_japanese_holiday(d: date) -> bool:
    """日本の主要祝日を判定（完璧ではないが実用十分）。

    振替休日・国民の休日は簡略化のため省略。
    正確性が必要なら jpholiday ライブラリを requirements に追加することも可能。
    """
    y, m, day = d.year, d.month, d.day

    # 固定祝日
    fixed_holidays = {
        (1, 1),    # 元日
        (2, 11),   # 建国記念の日
        (2, 23),   # 天皇誕生日
        (4, 29),   # 昭和の日
        (5, 3),    # 憲法記念日
        (5, 4),    # みどりの日
        (5, 5),    # こどもの日
        (8, 11),   # 山の日
        (11, 3),   # 文化の日
        (11, 23),  # 勤労感謝の日
    }
    if (m, day) in fixed_holidays:
        return True

    # ハッピーマンデー系
    if m == 1 and _days_from_second_monday(d) == 0:
        return True  # 成人の日
    if m == 7 and _days_from_third_monday(d) == 0:
        return True  # 海の日
    if m == 9 and _days_from_third_monday(d) == 0:
        return True  # 敬老の日
    if m == 10 and _days_from_second_monday(d) == 0:
        return True  # スポーツの日

    # 春分の日・秋分の日（近似: 3/20-21、9/22-23）
    if m == 3 and day in (20, 21):
        return True
    if m == 9 and day in (22, 23):
        return True

    return False


def is_trading_day(d: date) -> bool:
    """東証の営業日か（週末・祝日・年末年始以外）"""
    if d.weekday() >= 5:  # 土日
        return False
    if is_japanese_holiday(d):
        return False
    # 年末年始（12/31, 1/1-1/3）
    if d.month == 12 and d.day == 31:
        return False
    if d.month == 1 and d.day in (1, 2, 3):
        return False
    return True


def next_trading_day(d: date, max_lookahead: int = 10) -> date | None:
    """d の翌営業日"""
    candidate = d + timedelta(days=1)
    for _ in range(max_lookahead):
        if is_trading_day(candidate):
            return candidate
        candidate += timedelta(days=1)
    return None


def is_last_trading_day_of_month(d: date) -> bool:
    """その月の最終営業日か"""
    if not is_trading_day(d):
        return False
    nxt = next_trading_day(d)
    if nxt is None:
        return False
    return nxt.month != d.month


def is_pre_holiday(d: date) -> bool:
    """翌営業日まで2日以上間が空いているか（3連休前など）"""
    if not is_trading_day(d):
        return False
    nxt = next_trading_day(d)
    if nxt is None:
        return False
    return (nxt - d).days >= 3


def prev_trading_day(d: date, max_lookback: int = 10) -> date | None:
    """d の前営業日"""
    candidate = d - timedelta(days=1)
    for _ in range(max_lookback):
        if is_trading_day(candidate):
            return candidate
        candidate -= timedelta(days=1)
    return None


# ========== 米国（NYSE）の休場日 ==========

def _nth_weekday(y: int, m: int, weekday: int, n: int) -> date:
    first = date(y, m, 1)
    return first + timedelta(days=(weekday - first.weekday()) % 7 + 7 * (n - 1))


def _last_weekday(y: int, m: int, weekday: int) -> date:
    last = date(y, m + 1, 1) - timedelta(days=1) if m < 12 else date(y, 12, 31)
    return last - timedelta(days=(last.weekday() - weekday) % 7)


def _observed(d: date) -> date:
    """土曜の祝日は前の金曜、日曜の祝日は翌月曜に休む"""
    if d.weekday() == 5:
        return d - timedelta(days=1)
    if d.weekday() == 6:
        return d + timedelta(days=1)
    return d


def _easter(y: int) -> date:
    a, b, c = y % 19, y // 100, y % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = (h + l - 7 * m + 114) % 31 + 1
    return date(y, month, day)


def nyse_holidays(y: int) -> set[date]:
    """NYSE の定例休場日（臨時休場は含まない）"""
    days = {
        _nth_weekday(y, 1, 0, 3),            # キング牧師記念日
        _nth_weekday(y, 2, 0, 3),            # 大統領の日
        _easter(y) - timedelta(days=2),      # 聖金曜日
        _last_weekday(y, 5, 0),              # 戦没者追悼記念日
        _observed(date(y, 7, 4)),            # 独立記念日
        _nth_weekday(y, 9, 0, 1),            # レイバーデー
        _nth_weekday(y, 11, 3, 4),           # 感謝祭
        _observed(date(y, 12, 25)),          # クリスマス
    }
    if date(y, 1, 1).weekday() != 5:         # 元日が土曜のときは前年12/31を休まない
        days.add(_observed(date(y, 1, 1)))
    if y >= 2022:
        days.add(_observed(date(y, 6, 19)))  # ジューンティーンス
    return days


def us_session_before(jp_day: date) -> date:
    """日本の jp_day の朝に見る米国の取引日（暦の前日から、土日をさかのぼった平日）"""
    d = jp_day - timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def us_was_closed(jp_day: date) -> bool:
    """日本の今朝の材料になる米国の取引日が休場だったか"""
    d = us_session_before(jp_day)
    return d in nyse_holidays(d.year)


# ========== 各警告ロジック ==========

OVERNIGHT_THRESHOLD = 200      # 現対 ±200円 以上で「時間外に動いた」とみなす（market_direction と同じ）


def _overnight_pattern(period_positive: bool | None, overnight_diff: float | None) -> str | None:
    """今週（今月）の向きと、今朝の現対の向きの関係。gap_same / gap_opposite / gap_small"""
    if period_positive is None or overnight_diff is None:
        return None
    if abs(overnight_diff) < OVERNIGHT_THRESHOLD:
        return "gap_small"
    return "gap_same" if (overnight_diff > 0) == period_positive else "gap_opposite"


def _closing_evidence(stats: dict | None, pattern: str | None) -> str:
    """週次レポートの結果から「検証：n=◯回・反転◯%」を作る"""
    if not stats:
        return "未検証"
    s = stats.get(pattern or "all") or stats.get("all")
    if not s or not s.get("n"):
        return "未検証"
    head = {"gap_same": "時間外も同じ向きの日", "gap_opposite": "時間外が逆向きの日",
            "gap_small": "時間外の動きが小さい日"}.get(pattern or "", "全部の日")
    return (f"検証（{head}・{s['n']}回）：寄り→引けで今の向きと逆に動いた日 {s['hit'] * 100:.0f}%"
            f"・平均 {s['mean']:+.2f}%")


def _overnight_note(pattern: str | None, stats: dict | None) -> str:
    """時間外の向きについての一言。記事の説と、検証の結果が逆なら結果のほうを書く"""
    if pattern is None:
        return ""
    s = (stats or {}).get(pattern) or {}
    hit = s.get("hit")
    if pattern == "gap_same":
        if hit is not None and hit < 0.5:
            return "今朝の現対は今の向きと同じ。記事では「反転が強まる」とされるが、過去はそのまま続いた日のほうが多い。"
        return "今朝の現対は今の向きと同じ（記事では反転が強まりやすいとされる）。"
    if pattern == "gap_opposite":
        return "今朝の現対は今の向きと逆（記事では反転は弱まるとされる）。"
    return "今朝の現対は小さい。"


def check_friday_warning(
    today: date,
    nikkei_week_is_positive: bool | None,
    overnight_diff: float | None = None,
    validation: dict | None = None,
) -> TradingWarning | None:
    """金曜の週足手仕舞い警告。

    Args:
        today: 今日の日付
        nikkei_week_is_positive: 今週の日経が陽線か（月曜始値より現時点の終値が上か）
                                  None の場合は判定不能
    """
    if today.weekday() != 4:  # 4 = 金曜
        return None
    w = _friday_base(nikkei_week_is_positive)
    pattern = _overnight_pattern(nikkei_week_is_positive, overnight_diff)
    note = _overnight_note(pattern, validation)
    if note:
        w.message = f"{w.message}{note}"
    w.evidence = _closing_evidence(validation, pattern)
    return w


def _friday_base(nikkei_week_is_positive: bool | None) -> TradingWarning:

    if nikkei_week_is_positive is None:
        return TradingWarning(
            type="friday_closing",
            severity="medium",
            label="📅 金曜の手仕舞い警戒",
            message="今日は金曜日。後場にロングカバーの手仕舞い動向が入りやすい傾向。",
            action="午後からの持ち越しポジは慎重に。前場上げ→後場下げのパターンにも注意。",
        )

    if nikkei_week_is_positive:
        return TradingWarning(
            type="friday_closing",
            severity="high",
            label="📅 金曜 × 週足陽線 = 手仕舞い売り警戒",
            message="今週の日経は陽線。金曜のロングカバーで後場は売りに振れやすい。",
            action="午前に買いポジを持ってる場合、前場のうちに一部利確を検討。"
                   "デイトレは「前場上げ→後場下げ」のアノマリーを意識。",
        )
    else:
        return TradingWarning(
            type="friday_closing",
            severity="medium",
            label="📅 金曜 × 週足陰線 = ショートカバー買い警戒",
            message="今週の日経は陰線。金曜のショートカバーで後場上げの可能性。",
            action="安易な売りポジ持ち越しに注意。後場の買い戻しに巻き込まれる懸念。",
        )


def check_month_end_warning(
    today: date,
    nikkei_month_is_positive: bool | None,
    overnight_diff: float | None = None,
    validation: dict | None = None,
) -> TradingWarning | None:
    """月末最終営業日の月足手仕舞い警告。"""
    if not is_last_trading_day_of_month(today):
        return None
    w = _month_end_base(nikkei_month_is_positive)
    pattern = _overnight_pattern(nikkei_month_is_positive, overnight_diff)
    note = _overnight_note(pattern, validation)
    if note:
        w.message = f"{w.message}{note}"
    w.evidence = _closing_evidence(validation, pattern)
    return w


def _month_end_base(nikkei_month_is_positive: bool | None) -> TradingWarning:

    if nikkei_month_is_positive is None:
        return TradingWarning(
            type="month_end",
            severity="high",
            label="🗓️ 月末最終営業日 = 月足手仕舞い警戒",
            message="今日は月の最終営業日。月足ベースの手仕舞い動向に警戒。",
            action="週足より強い動きになりやすい。翌月月初から新トレンドが出る可能性も。",
        )

    if nikkei_month_is_positive:
        return TradingWarning(
            type="month_end",
            severity="high",
            label="🗓️ 月末 × 月足陽線 = 大きな手仕舞い売り警戒",
            message="今月の日経は陽線。月末にロングカバーの売りが入りやすく、週足手仕舞いより強い。",
            action="月末近くの大引けにかけての下げを警戒。"
                   "長期玉でもボラが大きくなるので、逆指値を引き締めておく。",
        )
    else:
        return TradingWarning(
            type="month_end",
            severity="medium",
            label="🗓️ 月末 × 月足陰線 = ショートカバー買い警戒",
            message="今月の日経は陰線。月末にショートカバーの買いが入りやすい。",
            action="ショートポジの翌月持ち越しは注意。月末大引けからの買い戻しで巻き込まれる懸念。",
        )


def check_pre_holiday_warning(today: date) -> TradingWarning | None:
    """3連休前警告。"""
    if not is_pre_holiday(today):
        return None

    nxt = next_trading_day(today)
    assert nxt is not None
    gap_days = (nxt - today).days
    gap_label = "3連休前" if gap_days == 3 else f"{gap_days}日休暇前"

    return TradingWarning(
        type="pre_holiday",
        severity="medium",
        label=f"🏖️ {gap_label} = 持ち越しリスク増",
        message=f"次の営業日は{nxt.isoformat()}。休暇中は地政学リスクで窓開けしやすい。",
        action="ポジションを縮小する、ヘッジポジを入れる等の検討を。"
               "特にレバレッジ先物・CFDは想定外の窓開けに注意。",
    )


def check_month_start_warning(today: date) -> TradingWarning | None:
    """月初営業日警告（PDFの「月初めという節目での下げ → 月全体に波及する恐れ」）。"""
    if not is_trading_day(today):
        return None
    # 前営業日が前月なら、今日は月初営業日
    # 簡易判定: 過去7日の範囲で前月の最終営業日を探す
    for delta in range(1, 8):
        prev = today - timedelta(days=delta)
        if is_trading_day(prev):
            if prev.month != today.month:
                return TradingWarning(
                    type="month_start",
                    severity="low",
                    label="🆕 月初営業日 = 月間トレンド形成に注目",
                    message="月初の値動きが月全体のバイアスになりやすい（特に下げの場合）。",
                    action="今日の方向性は月間トレンドの示唆になりうる。過大な逆張りは控えめに。",
                )
            return None
    return None


def check_us_holiday_warning(today: date) -> TradingWarning | None:
    """前日の米国が休場なら、連動の材料がない（米国の騰落率は休場前のものが残っている）"""
    if not us_was_closed(today):
        return None
    return TradingWarning(
        type="us_holiday",
        severity="high",
        label="🇺🇸 米国休場明け = 連動の材料なし",
        message=f"前日（{us_session_before(today):%m/%d}）の米国市場は休場。表示している米国の騰落は休場前の日のもの。",
        action="今日の方針は「初心者手出し無用」。米国の動きを手がかりにしない。",
    )


def check_after_break_warning(today: date) -> TradingWarning | None:
    """日本の連休明け（前営業日から4日以上あいた日）は窓が大きくなりやすい"""
    if not is_trading_day(today):
        return None
    prev = prev_trading_day(today)
    if prev is None or (today - prev).days < 4:
        return None
    return TradingWarning(
        type="after_break",
        severity="medium",
        label=f"🏖️ 連休明け（{(today - prev).days - 1}日休み）= 窓が大きくなりやすい",
        message="休みのあいだの海外の動きが寄り付きにまとめて出る。前日の米国だけでは読みにくい。",
        action="寄り直後の大きな窓に飛び乗らない。9時台の値幅が普段より大きくなる前提で株数は守る。",
    )


def dividend_days(year: int, month: int) -> tuple[date, date] | None:
    """その月の権利付き最終日（最終営業日の2営業日前）と権利落ち日（その翌営業日）"""
    d = date(year, month + 1, 1) - timedelta(days=1) if month < 12 else date(year, 12, 31)
    while not is_trading_day(d):
        d -= timedelta(days=1)
    last = prev_trading_day(d)
    last = prev_trading_day(last) if last else None
    if last is None:
        return None
    ex = next_trading_day(last)
    return (last, ex) if ex else None


def check_dividend_record_warning(today: date, validation: dict | None = None) -> TradingWarning | None:
    """3・6・9・12月の権利付き最終日と権利落ち日"""
    if today.month not in (3, 6, 9, 12):
        return None
    days = dividend_days(today.year, today.month)
    if days is None or today not in days:
        return None
    big = today.month in (3, 9)
    sev: Severity = "high" if big else "medium"
    k = (validation or {}).get("3月・9月" if big else "6月・12月") or {}
    if today == days[0]:
        s = k.get("last_day_buy") or {}
        ev = (f"検証（参考・{s['n']}回しかない）：日経が寄り→引けで上げた日 {s['hit'] * 100:.0f}%・平均 {s['mean']:+.2f}%"
              if s.get("n") else "未検証")
        return TradingWarning(
            type="dividend_last", severity=sev,
            label=f"💴 権利付き最終日（{today.month}月）",
            message="配当・優待の権利を取る買いが入りやすい日。記事では「曜日に関係なく買いの順張りが基本」。",
            action="今日のマイルールの向きが優先。売りで入るなら持ち越さない（逆日歩・配当落ち調整金がかかる）。",
            evidence=ev)
    ev = (f"検証（参考・{k['ex_day_n']}回）：権利落ち日の日経の窓 平均 {k['ex_day_gap_mean']:+.2f}%"
          if k.get("ex_day_n") else "未検証")
    return TradingWarning(
        type="dividend_ex", severity=sev,
        label=f"💴 権利落ち日（{today.month}月）",
        message="配当の分だけ寄りが下がりやすい日。下がり方が小さい銘柄は、その日に下げやすいとされる。",
        action="寄りの窓が普段と違う前提で。窓の大きさを見てから入る。",
        evidence=ev)


# ========== 統合API ==========

def get_all_warnings(
    today: date,
    nikkei_week_is_positive: bool | None = None,
    nikkei_month_is_positive: bool | None = None,
    overnight_diff: float | None = None,
    validation: dict | None = None,
) -> list[TradingWarning]:
    """今日該当する警告を全て返す（severity=high を先頭に）。

    冗長性の解消ルール:
      - 金曜のpre_holiday警告は friday_closing と内容が被るので除外
      - 月末のpre_holiday警告は month_end と被るので除外
    """
    v = validation or {}
    closing = v.get("closing") or {}
    friday = check_friday_warning(today, nikkei_week_is_positive, overnight_diff, closing.get("friday"))
    month_end = check_month_end_warning(today, nikkei_month_is_positive, overnight_diff, closing.get("month_end"))
    if friday and month_end:
        # 週末×月末が重なる日は1枚にまとめる（月足の向きで見る）
        month_end.type = "friday_month_end"
        month_end.label = "📅🗓️ 週末×月末が重なる日：" + month_end.label.split("= ", 1)[-1]
        pattern = _overnight_pattern(nikkei_month_is_positive, overnight_diff)
        month_end.evidence = _closing_evidence(closing.get("both"), pattern)
        friday = None
    candidates = [
        friday,
        month_end,
        check_dividend_record_warning(today, v.get("kenri")),
        check_pre_holiday_warning(today),
        check_month_start_warning(today),
        check_us_holiday_warning(today),
        check_after_break_warning(today),
    ]
    warnings = [w for w in candidates if w is not None]

    # 冗長性チェック: 金曜手仕舞いまたは月末手仕舞いがあるなら、pre_holidayは抑制
    has_friday_or_month_end = any(
        w.type in ("friday_closing", "month_end", "friday_month_end") for w in warnings
    )
    if has_friday_or_month_end:
        warnings = [w for w in warnings if w.type != "pre_holiday"]

    severity_order = {"high": 0, "medium": 1, "low": 2}
    warnings.sort(key=lambda w: severity_order.get(w.severity, 9))
    return warnings
