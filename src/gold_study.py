"""
ゴールドを方向の材料に入れると当たりが良くなるか（研究用・週次レポート。idea_study から呼ぶ）

L6-1（過去ログ6）: 記事では「ドル円上げ・ゴールド下げ」を上げの材料にしている（リスクを取る日はゴールドが下がる）。
  fixed:  ゴールドの前日騰落を「下げ＝上げ材料」の固定の符号で数える
L7-4（過去ログ7）: 「株とゴールドの値動きがセットになりつつある」とあり、関係が逆になる時期がある。
  corr:   直近60営業日の「日経の寄り→引け」と「ゴールドの前日騰落」の相関の符号を使う（その日より前だけで計算）。
          |r| < 0.1 の期間は材料に入れない

どちらも
  単独:   ゴールドの向きだけで寄り→引けに入ったときの当たり
  v2追加: v2 の判定に、ゴールドが逆を向いていたら見送る条件を足した版と、v2 との比較（後半の期間）
  一致数: v2 の6材料にゴールドを足した7材料の一致数ごとの当たり
p値は idea_study の BH 補正にまとめて入れる。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from idea_study import signed_summary

GOLD_THRESHOLD = 0.3        # ±0.3% 未満は変動なし（米指数と同じ）
CORR_WINDOW = 60
CORR_MIN = 0.1


def gold_sides(df: pd.DataFrame, window: int = CORR_WINDOW, min_r: float = CORR_MIN) -> pd.DataFrame:
    """日ごとのゴールドの材料の向き（+1=日経の上げ材料 / -1=下げ材料 / 0=なし）（純粋関数）。

    df: index=日付、gold（前日のゴールド騰落%）・ret（日経 寄り→引け%）
    """
    g = df["gold"]
    move = np.sign(g).where(g.abs() >= GOLD_THRESHOLD, 0.0)
    out = pd.DataFrame(index=df.index)
    out["fixed"] = -move
    r = df["ret"].rolling(window, min_periods=window).corr(g).shift(1)   # その日より前の60日だけ
    sign = np.sign(r).where(r.abs() >= min_r, 0.0).fillna(0.0)
    out["corr"] = sign * move
    out["corr_r"] = r
    return out


def with_gold(side_v2: pd.Series, gold: pd.Series) -> pd.Series:
    """v2 の判定（+1/-1/0）に、ゴールドが逆を向いていたら見送る条件を足す"""
    return side_v2.where(~((side_v2 != 0) & (gold == -side_v2)), 0)


def agree_with_gold(df: pd.DataFrame, gold: pd.Series) -> pd.Series:
    """v2 の一致数（多い側の向き）に、ゴールドがその向きなら1を足す"""
    d = df["agree_dir"].map({"bullish": 1, "bearish": -1}).fillna(0)
    return df["agree"] + ((d != 0) & (gold == d)).astype(int)


def study_gold(df: pd.DataFrame) -> dict:
    """df: add_verdicts 後の日ごとの表（gold・ret・side_v2・agree・agree_dir）"""
    d = df.dropna(subset=["gold", "ret"])
    sides = gold_sides(d)
    half = d.index[len(d) // 2]
    late = d.index >= half
    out = {"split": str(half.date()), "n_days": len(d)}
    for k in ("fixed", "corr"):
        s = sides[k]
        g = with_gold(d["side_v2"], s)
        res = {
            "alone": signed_summary(d["ret"], s),
            "v2": signed_summary(d["ret"][late], d["side_v2"][late]),
            "v2_gold": signed_summary(d["ret"][late], g[late]),
            "by_agree": [],
        }
        res["v2"]["hands_off_share"] = float((d["side_v2"][late] == 0).mean())
        res["v2_gold"]["hands_off_share"] = float((g[late] == 0).mean())
        trend = d["side_v2"] != 0
        ag = agree_with_gold(d, s)
        for a, idx in ag[trend].groupby(ag[trend]).groups.items():
            ss = signed_summary(d["ret"][idx], d["side_v2"][idx])
            res["by_agree"].append({"agree": int(a), **ss})
        if k == "corr":
            r = sides["corr_r"].dropna()
            res["share_used"] = float((sides.loc[r.index, "corr"] != 0).mean()) if len(r) else None
            res["last_r"] = float(r.iloc[-1]) if len(r) else None
            res["share_negative"] = float((r <= -CORR_MIN).mean()) if len(r) else None
        out[k] = res
    return out


def collect_gold_pvalues(g: dict | None) -> list[tuple[str, float]]:
    ps = []
    for k in ("fixed", "corr"):
        r = (g or {}).get(k)
        if not r:
            continue
        ps.append((f"ゴールド:{k}:単独", r["alone"]["p_hit"]))
        ps.append((f"ゴールド:{k}:v2追加", r["v2_gold"]["p_hit"]))
    return ps
