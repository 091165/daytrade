"""
gold_study のユニットテスト
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from gold_study import agree_with_gold, collect_gold_pvalues, gold_sides, study_gold, with_gold


def _days(n: int, beta: float, seed: int = 0, flip_at: int | None = None) -> pd.DataFrame:
    """ゴールドの前日騰落 gold と、日経の寄り→引け ret = beta×gold + ノイズ（flip_at 以降は向きが逆）"""
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2020-01-01", periods=n)
    gold = rng.normal(0, 1.0, n)
    b = np.full(n, beta)
    if flip_at is not None:
        b[flip_at:] = -beta
    ret = b * gold + rng.normal(0, 1.0, n)
    side = rng.choice([-1, 0, 1], n)
    return pd.DataFrame({"gold": gold, "ret": ret, "side_v2": side, "agree": 4,
                         "agree_dir": np.where(side > 0, "bullish", np.where(side < 0, "bearish", "neutral"))}, index=idx)


def test_fixed_sign_and_threshold():
    df = pd.DataFrame({"gold": [1.0, -0.5, 0.1, -0.2], "ret": [0, 0, 0, 0]}, index=pd.bdate_range("2026-01-01", periods=4))
    s = gold_sides(df)
    assert list(s["fixed"]) == [-1, 1, 0, 0]          # ゴールド下げ＝上げ材料、±0.3%未満はなし
    print("✅ test_fixed_sign_and_threshold")


def test_corr_uses_only_past_and_follows_flip():
    df = _days(400, beta=0.6, flip_at=200, seed=1)
    s = gold_sides(df)
    assert (s["corr"].iloc[:60] == 0).all()           # 60日たまるまでは使わない
    # 前半はゴールドと同じ向き（+）、後半の終わりごろは逆向き（-）の相関
    assert s["corr_r"].iloc[150] > 0.1 and s["corr_r"].iloc[-1] < -0.1
    # その日の値を変えても、その日の相関（前日まで）は変わらない
    df2 = df.copy()
    df2.iloc[300, df2.columns.get_loc("ret")] = 100
    assert gold_sides(df2)["corr_r"].iloc[300] == s["corr_r"].iloc[300]
    print("✅ test_corr_uses_only_past_and_follows_flip")


def test_with_gold_and_agree():
    side = pd.Series([1, 1, -1, 0])
    gold = pd.Series([-1, 1, 1, 1])
    assert list(with_gold(side, gold)) == [0, 1, 0, 0]
    df = pd.DataFrame({"agree": [4, 4, 3], "agree_dir": ["bullish", "bearish", "neutral"]})
    assert list(agree_with_gold(df, pd.Series([1, 1, 1]))) == [5, 4, 3]
    print("✅ test_with_gold_and_agree")


def test_study_detects_link_and_corr_version_survives_flip():
    r = study_gold(_days(1200, beta=-0.5, seed=2))     # ゴールド下げの日に日経が上げる（記事どおり）
    assert r["fixed"]["alone"]["hit"] > 0.6
    flip = study_gold(_days(1200, beta=-0.5, seed=3, flip_at=600))
    # 関係が途中で逆になると、固定の符号は当たらなくなり、相関の符号の版は当たり続ける
    assert flip["corr"]["alone"]["hit"] > flip["fixed"]["alone"]["hit"] + 0.05
    assert len(collect_gold_pvalues(r)) == 4
    print("✅ test_study_detects_link_and_corr_version_survives_flip")


if __name__ == "__main__":
    test_fixed_sign_and_threshold()
    test_corr_uses_only_past_and_follows_flip()
    test_with_gold_and_agree()
    test_study_detects_link_and_corr_version_survives_flip()
    print("\n🎉 All gold_study tests passed!")
