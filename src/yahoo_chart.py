"""
Yahoo のチャートデータ（分足）を直接取得する

yfinance 経由の東証の1分足は、寄りの 9:00〜9:05 の足が欠ける（2026/10 に確認: 9:04〜9:06始まり）。
チャートデータを直接取ると 9:00 の足（寄りの約定）も入るので、1分足の保存はこちらを使う。
（5分足はチャートデータでも 9:00 の足が欠ける日が多く、取れる日数も減るので yfinance のまま）

Yahoo の制限: 1分足は直近30日分を1回8日まで。
"""

from __future__ import annotations

import json
import time as _time
import urllib.request
from datetime import datetime, timedelta, timezone

import pandas as pd

CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?period1={p1}&period2={p2}&interval={interval}"
USER_AGENT = "Mozilla/5.0 (daytrade-signal-board)"
REQUEST_DELAY_SEC = 0.3


def chart_to_frame(payload: dict) -> pd.DataFrame:
    """チャートAPIの応答を、UTCの日時を index にした Open/High/Low/Close/Volume にする（純粋関数）。"""
    result = ((payload or {}).get("chart") or {}).get("result") or []
    if not result:
        return pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"])
    r = result[0]
    ts = r.get("timestamp") or []
    q = ((r.get("indicators") or {}).get("quote") or [{}])[0]
    df = pd.DataFrame({
        "Open": q.get("open", []), "High": q.get("high", []), "Low": q.get("low", []),
        "Close": q.get("close", []), "Volume": q.get("volume", []),
    }, index=pd.to_datetime(ts, unit="s", utc=True))
    return df.dropna(subset=["Open", "High", "Low", "Close"])


def fetch_chart(symbol: str, interval: str, start: datetime, end: datetime) -> pd.DataFrame:
    url = CHART_URL.format(symbol=symbol, p1=int(start.timestamp()), p2=int(end.timestamp()), interval=interval)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=30) as res:
        return chart_to_frame(json.load(res))


def fetch_intraday(symbol: str, interval: str = "1m", days: int = 29, chunk_days: int = 7) -> pd.DataFrame:
    """直近 days 日分の分足を、chunk_days 日ずつに分けて取って1つにまとめる。取れなければ空。"""
    end = datetime.now(timezone.utc)
    frames = []
    while days > 0:
        span = min(days, chunk_days)
        start = end - timedelta(days=span)
        try:
            frames.append(fetch_chart(symbol, interval, start, end))
        except Exception as e:
            print(f"⚠️ chart {symbol} {interval} {start:%m/%d}-{end:%m/%d}: {e}")
        _time.sleep(REQUEST_DELAY_SEC)
        end, days = start, days - span
    if not frames:
        return pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"])
    df = pd.concat(frames).sort_index()
    return df[~df.index.duplicated(keep="last")]
