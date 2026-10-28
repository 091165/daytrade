"""
1分足を毎日貯める（9:00〜10:29、売買代金上位20銘柄）

Yahoo の1分足は直近30日分しか取れないため、毎日の引け後に保存して、
「寄り直後の動きが続くか」などの分足の検証に使える期間を伸ばしていく。

保存先: data/minutes/YYYY-MM-DD.csv（code,time,open,high,low,close,volume）
すでにある日は上書きしない。今日の分は 15:30 以降の実行でだけ保存する（場中の未完成のデータを残さない）。
"""

from __future__ import annotations

import csv
import sys
from datetime import date, datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

JST = ZoneInfo("Asia/Tokyo")
KEEP_FROM, KEEP_TO = time(9, 0), time(10, 29)
MARKET_CLOSE = time(15, 30)
MINUTES_DIR = Path(__file__).resolve().parent.parent / "data" / "minutes"
COLUMNS = ["code", "time", "open", "high", "low", "close", "volume"]


def minute_rows(df: pd.DataFrame, code: str) -> dict[date, list[list]]:
    """1銘柄の1分足を、日付ごとの保存用の行（9:00〜10:29）に分ける（純粋関数）。"""
    if df is None or df.empty:
        return {}
    x = df.dropna(subset=["Open", "High", "Low", "Close"])
    if getattr(x.index, "tz", None) is not None:
        x = x.tz_convert(JST)
    out: dict[date, list[list]] = {}
    for ts, r in x.iterrows():
        t = ts.time()
        if not (KEEP_FROM <= t <= KEEP_TO):
            continue
        vol = r.get("Volume", 0)
        out.setdefault(ts.date(), []).append([
            code, ts.strftime("%H:%M"),
            round(float(r["Open"]), 2), round(float(r["High"]), 2), round(float(r["Low"]), 2), round(float(r["Close"]), 2),
            int(vol) if pd.notna(vol) else 0,
        ])
    return out


def days_to_save(available: set[date], existing: set[date], now: datetime) -> list[date]:
    """保存する日：まだ保存していない日。今日の分は引け後だけ。"""
    today = now.date()
    return sorted(d for d in available - existing if d < today or (d == today and now.time() >= MARKET_CLOSE))


def write_day(path: Path, rows: list[list]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(COLUMNS)
        w.writerows(sorted(rows, key=lambda r: (r[0], r[1])))


def load_minutes(minutes_dir: Path = MINUTES_DIR) -> pd.DataFrame:
    """保存した1分足を全部読み込む（index なし、date 列つき）。"""
    frames = []
    for p in sorted(minutes_dir.glob("????-??-??.csv")):
        df = pd.read_csv(p, dtype={"code": str})
        df["date"] = pd.Timestamp(p.stem)
        frames.append(df)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=COLUMNS + ["date"])


def collect(now: datetime | None = None) -> int:
    from stock_screen import SCREEN_JP
    from yahoo_chart import fetch_intraday

    now = now or datetime.now(JST)
    by_day: dict[date, list[list]] = {}
    for code, t, _ in SCREEN_JP:
        df = fetch_intraday(t, "1m", days=29, chunk_days=7)     # yfinance だと 9:00〜9:05 の足が欠けるため
        if df.empty:
            print(f"⚠️ no data: {t}")
            continue
        for d, rows in minute_rows(df, code).items():
            by_day.setdefault(d, []).extend(rows)
    existing = {date.fromisoformat(p.stem) for p in MINUTES_DIR.glob("????-??-??.csv")}
    saved = days_to_save(set(by_day), existing, now)
    for d in saved:
        write_day(MINUTES_DIR / f"{d.isoformat()}.csv", by_day[d])
        codes = {r[0] for r in by_day[d]}
        opened = {r[0] for r in by_day[d] if r[1] == "09:00"}
        print(f"✅ data/minutes/{d.isoformat()}.csv  {len(codes)}銘柄 {len(by_day[d])}行（9:00の足あり {len(opened)}銘柄）")
    print(f"保存 {len(saved)}日 ／ これまでの合計 {len(existing) + len(saved)}日")
    return len(saved)


if __name__ == "__main__":
    collect()
    sys.exit(0)
