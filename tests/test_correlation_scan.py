"""
correlation_scan のユニットテスト（ネットワーク不要・合成データ）
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from correlation_scan import SCAN_JP, SCAN_US, bh_qvalues, scan_pairs, summarize


def test_bh_qvalues_known_example():
    q = bh_qvalues([0.01, 0.04, 0.03, 0.005])
    assert np.allclose(q, [0.02, 0.04, 0.04, 0.02])
    print("✅ test_bh_qvalues_known_example")


def test_bh_handles_none_and_clips():
    q = bh_qvalues([None, 0.9, float("nan"), 0.95])
    assert q[0] is None and q[2] is None
    assert all(0 <= v <= 1 for v in (q[1], q[3]))
    assert bh_qvalues([]) == []
    print("✅ test_bh_handles_none_and_clips")


def test_bh_is_monotonic_in_p():
    rng = np.random.default_rng(0)
    ps = list(rng.uniform(0, 1, 50))
    q = bh_qvalues(ps)
    order = np.argsort(ps)
    qs = [q[i] for i in order]
    assert all(a <= b + 1e-12 for a, b in zip(qs, qs[1:]))
    assert all(qi >= pi - 1e-12 for qi, pi in zip(q, ps))
    print("✅ test_bh_is_monotonic_in_p")


def _synthetic(n_days=250, n_us=20, n_jp=6, seed=1):
    """ノイズだけの米国株 n_us 本と日本株 n_jp 本。
    ただし 日本株J0 × 米国株U0 だけ、窓と寄り後の両方に本物の連動を仕込む。"""
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2025-10-01", periods=n_days)
    us = {f"U{i}": pd.Series(rng.normal(0, 1.5, n_days), index=idx) for i in range(n_us)}
    jp = {}
    for j in range(n_jp):
        u = us["U0"].values
        gap = rng.normal(0, 1.0, n_days)
        intra = rng.normal(0, 1.0, n_days)
        if j == 0:
            gap[1:] += 0.8 * u[:-1]      # 日本T日 ← 米T-1日
            intra[1:] += 0.6 * u[:-1]
        close = [1000.0]
        opens = [1000.0]
        for t in range(1, n_days):
            o = close[-1] * (1 + gap[t] / 100)
            opens.append(o)
            close.append(o * (1 + intra[t] / 100))
        jp[f"J{j}"] = (pd.Series(opens, index=idx), pd.Series(close, index=idx))
    return us, jp


def test_scan_finds_planted_pair_and_rejects_noise():
    us, jp = _synthetic()
    pairs = scan_pairs(us, jp)
    assert len(pairs) == 20 * 6
    hits = [(p["jp"], p["us"]) for p in pairs if p["intraday_significant"]]
    assert ("J0", "U0") in hits, hits
    # ノイズ組み合わせが大量に「有意」にならないこと（補正の効果）
    assert len(hits) <= 2, hits
    planted = next(p for p in pairs if p["jp"] == "J0" and p["us"] == "U0")
    assert planted["gap_significant"] and planted["intraday_r"] > 0.3
    print("✅ test_scan_finds_planted_pair_and_rejects_noise")


def test_correction_is_stricter_than_raw_p():
    """補正前 p<0.05 の数 >= 補正後に有意な数（ノイズでの偶然の当たりが削られる）"""
    us, jp = _synthetic(seed=7)
    pairs = scan_pairs(us, jp)
    raw = sum(1 for p in pairs if p["intraday_p"] is not None and p["intraday_p"] < 0.05)
    adj = sum(1 for p in pairs if p["intraday_significant"])
    assert raw >= adj
    print(f"✅ test_correction_is_stricter_than_raw_p (補正前{raw}組 → 補正後{adj}組)")


def test_short_series_are_skipped():
    idx = pd.bdate_range("2026-01-01", periods=30)
    us = {"U0": pd.Series(np.arange(30, dtype=float), index=idx)}
    jp = {"J0": (pd.Series(100.0, index=idx), pd.Series(100.0, index=idx))}
    assert scan_pairs(us, jp) == []
    print("✅ test_short_series_are_skipped")


def test_summarize_structure_with_real_universe_codes():
    """本番の銘柄コードで summarize が全日本株分の行を返すこと"""
    rng = np.random.default_rng(3)
    idx = pd.bdate_range("2025-10-01", periods=200)
    us = {t: pd.Series(rng.normal(0, 1.5, 200), index=idx) for t, _ in SCAN_US[:4]}
    jp = {}
    for code, _, _ in SCAN_JP[:3]:
        o = pd.Series(100 * np.exp(rng.normal(0, 0.02, 200).cumsum()), index=idx)
        jp[code] = (o, o * np.exp(rng.normal(0, 0.01, 200)))
    sm = summarize(scan_pairs(us, jp))
    assert sm["n_pairs"] == 12
    assert len(sm["by_jp"]) == len(SCAN_JP)
    filled = [j for j in sm["by_jp"] if j["top_gap"]]
    assert len(filled) == 3 and all(len(j["top_gap"]) == 3 for j in filled)
    empty = [j for j in sm["by_jp"] if not j["top_gap"]]
    assert all(j["n"] == 0 for j in empty)
    g = filled[0]["top_gap"]
    assert g[0]["gap_r"] >= g[1]["gap_r"] >= g[2]["gap_r"]
    assert "us_name" in g[0]
    print("✅ test_summarize_structure_with_real_universe_codes")


def test_universe_has_no_duplicates():
    assert len({t for t, _ in SCAN_US}) == len(SCAN_US)
    assert len({c for c, _, _ in SCAN_JP}) == len(SCAN_JP)
    print("✅ test_universe_has_no_duplicates")


if __name__ == "__main__":
    test_bh_qvalues_known_example()
    test_bh_handles_none_and_clips()
    test_bh_is_monotonic_in_p()
    test_scan_finds_planted_pair_and_rejects_noise()
    test_correction_is_stricter_than_raw_p()
    test_short_series_are_skipped()
    test_summarize_structure_with_real_universe_codes()
    test_universe_has_no_duplicates()
    print("\n🎉 All correlation_scan tests passed!")
