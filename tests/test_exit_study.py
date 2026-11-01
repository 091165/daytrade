"""
exit_study のユニットテスト（作った5分足で (a)(b)(c) の決済価格・決済時刻を確かめる）
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from exit_study import (build_trades, compare_actual, max_consecutive_losses, max_drawdown,
                        parse_real_trades, simulate_day, study, summarize)

DAY = "2026-10-01"


def _bars(spec: dict[str, tuple[float, float, float, float]], day: str = DAY) -> pd.DataFrame:
    """{"09:05": (open, high, low, close), ...} の5分足"""
    idx = pd.to_datetime([f"{day} {t}" for t in spec])
    o, h, l, c = zip(*spec.values())
    return pd.DataFrame({"Open": o, "High": h, "Low": l, "Close": c}, index=idx).sort_index()


def _flat(start: str, end: str, px: float, day: str = DAY) -> dict:
    """start〜end（end は含まない）の、値段が動かない足"""
    out = {}
    for t in pd.date_range(f"{day} {start}", f"{day} {end}", freq="5min", inclusive="left"):
        out[t.strftime("%H:%M")] = (px, px + 1, px - 1, px)
    return out


def test_stop_before_10_all_methods():
    """買いで 9:15 に損切り幅（50円）に触れる → 3通りとも 9:15 に -5,000円"""
    spec = {"09:00": (990, 1002, 985, 1000), "09:05": (1000, 1010, 995, 1005), "09:10": (1005, 1006, 990, 992),
            "09:15": (992, 993, 940, 945), **_flat("09:20", "15:30", 945)}
    r = simulate_day(_bars(spec), side=1, close=945)
    assert r["entry"] == 1000
    for m in ("a", "b", "c"):
        assert r[m]["exit_price"] == 950 and r[m]["exit_time"] == "09:15" and r[m]["pnl"] == -5000, (m, r[m])
    print("✅ test_stop_before_10_all_methods")


def test_gap_through_stop_fills_at_open():
    """売りで、足の始値がもう逆指値の上 → 始値で約定（不利側）"""
    spec = {"09:05": (1000, 1004, 996, 1002), "09:10": (1070, 1080, 1065, 1075), **_flat("09:15", "15:30", 1075)}
    r = simulate_day(_bars(spec), side=-1, close=1075)
    assert r["a"]["exit_price"] == 1070 and r["a"]["exit_time"] == "09:10" and r["a"]["pnl"] == -7000
    print("✅ test_gap_through_stop_fills_at_open")


def test_ten_oclock_exit_and_time_method_hold():
    """10時に含み益 → (a) は 10:00 の始値で決済、(b) は 10:00 の値段 −50円 の逆指値で引けまで"""
    spec = {"09:05": (1000, 1003, 997, 1002), **_flat("09:10", "10:00", 1030),
            "10:00": (1040, 1045, 1035, 1042), **_flat("10:05", "13:00", 1060),
            "13:00": (1060, 1061, 985, 990), **_flat("13:05", "15:30", 990)}
    r = simulate_day(_bars(spec), side=1, close=990)
    assert r["a"]["exit_price"] == 1040 and r["a"]["exit_time"] == "10:00" and r["a"]["pnl"] == 4000
    assert r["b"]["exit_price"] == 990 and r["b"]["exit_time"] == "13:00", r["b"]   # 逆指値 1040-50=990
    assert r["b"]["pnl"] == -1000
    # (c) 利確 1150 に届かず、13:00 の安値 985 で損切り 950 には触れない → 引け
    assert r["c"]["exit_time"] == "15:30" and r["c"]["exit_price"] == 990
    print("✅ test_ten_oclock_exit_and_time_method_hold")


def test_time_method_runs_to_close():
    """10時に含み益で、逆指値に触れなければ (b) は引けで決済"""
    spec = {"09:05": (1000, 1003, 997, 1002), **_flat("09:10", "10:00", 1020),
            "10:00": (1020, 1025, 1015, 1022), **_flat("10:05", "15:30", 1100)}
    r = simulate_day(_bars(spec), side=1, close=1105)
    assert r["b"]["exit_price"] == 1105 and r["b"]["exit_time"] == "15:30" and r["b"]["pnl"] == 10500
    assert r["a"]["pnl"] == 2000
    print("✅ test_time_method_runs_to_close")


def test_time_method_no_profit_at_10():
    """10時に含み益なし → (b) も 10:00 で決済"""
    spec = {"09:05": (1000, 1003, 997, 1002), **_flat("09:10", "10:00", 990),
            "10:00": (985, 990, 980, 988), **_flat("10:05", "15:30", 900)}
    r = simulate_day(_bars(spec), side=1, close=900)
    assert r["b"]["exit_time"] == "10:00" and r["b"]["exit_price"] == 985 and r["b"]["pnl"] == -1500
    assert r["b"]["reason"] == "10時（含み益なし）"
    print("✅ test_time_method_no_profit_at_10")


def test_oco_target_and_same_bar_both():
    """(c) 売りで利確に届く／同じ足で損切りと利確の両方に触れたら損切りが先"""
    spec = {"09:05": (1000, 1004, 996, 999), **_flat("09:10", "11:00", 960),
            "11:00": (900, 905, 840, 845), **_flat("11:05", "15:30", 845)}
    r = simulate_day(_bars(spec), side=-1, close=845)
    assert r["c"]["exit_price"] == 850 and r["c"]["exit_time"] == "11:00" and r["c"]["pnl"] == 15000
    assert r["c"]["reason"] == "利確"

    both = {"09:05": (1000, 1004, 996, 999), **_flat("09:10", "11:00", 1000),
            "11:00": (1000, 1160, 940, 1100), **_flat("11:05", "15:30", 1100)}
    r = simulate_day(_bars(both), side=1, close=1100)
    assert r["c"]["reason"] == "損切り" and r["c"]["exit_price"] == 950 and r["c"]["exit_time"] == "11:00"
    # 9:05 の足（入った足）の中で両方触れても損切りが先
    first = {"09:05": (1000, 1200, 900, 1100), **_flat("09:10", "15:30", 1100)}
    r = simulate_day(_bars(first), side=1, close=1100)
    assert r["c"]["reason"] == "損切り" and r["c"]["exit_time"] == "09:05"
    print("✅ test_oco_target_and_same_bar_both")


def test_no_entry_bar_or_no_side():
    spec = _flat("09:10", "15:30", 1000)
    assert simulate_day(_bars(spec), side=1) is None
    assert simulate_day(_bars({"09:05": (1, 1, 1, 1)}), side=0) is None
    print("✅ test_no_entry_bar_or_no_side")


def test_metrics():
    assert max_consecutive_losses([100, -1, -2, 5, -1, -1, -1, 0]) == 3
    assert max_drawdown([1000, -3000, 500, -2000, 4000]) == -4500
    t = pd.DataFrame({"date": ["d1", "d1", "d2"], "code": ["A", "B", "A"], "a_pnl": [-5000.0, -5000.0, 3000.0]})
    s = summarize(t, "a")
    assert s["n"] == 3 and s["loss_limit_days"] == 1 and abs(s["win_rate"] - 1 / 3) < 1e-9
    assert s["median"] == -5000 and s["max_consec_losses"] == 2
    print("✅ test_metrics")


def test_build_trades_and_actual():
    spec = {"09:00": (995, 1000, 990, 998), "09:05": (1000, 1003, 997, 1002), **_flat("09:10", "10:00", 1020),
            "10:00": (1020, 1025, 1015, 1022), **_flat("10:05", "15:30", 1030)}
    day2 = "2026-10-02"
    spec2 = {k: v for k, v in spec.items()}
    m5 = {"5803": pd.concat([_bars(spec), _bars(spec2, day2)])}
    daily = {"5803": pd.DataFrame({"Open": [990.0, 1010.0], "Close": [1030.0, 1030.0]},
                                  index=pd.to_datetime([DAY, day2]))}
    verdicts = {DAY: {"verdict": "buy_bias", "confidence": "high"}, day2: {"verdict": "hands_off", "confidence": "low"}}
    trades, ref = build_trades(m5, daily, verdicts, {"5803": "フジクラ"})
    assert len(trades) == 1 and trades.iloc[0]["a_pnl"] == 2000 and trades.iloc[0]["b_pnl"] == 3000
    # 手出し無用の日は参考：寄り 1010 → 9:05 1000 で下向き → 売り
    assert len(ref) == 1 and ref.iloc[0]["side"] == -1
    res = study(trades, ref)
    assert res["all"]["a"]["n"] == 1 and "hands_off_reference" in res and "buy_bias" in res["by_verdict"]

    notes = {"realTrades": {"5803": [
        {"direction": "buy", "qty": 200, "openTime": "09:08", "openPrice": 1004, "closeTime": "09:30", "closePrice": 1014, "pnl": 2000},
        {"direction": "sell", "qty": 100, "openTime": "09:40", "openPrice": 1020, "closeTime": "09:50", "closePrice": 1025, "pnl": -500}]}}
    act = compare_actual(trades, parse_real_trades(DAY, notes))
    assert act["n"] == 1
    row = act["rows"][0]
    assert row["actual_pnl100"] == 1000 and row["entry_slip"] == 4 and row["same_side"] and row["pnl_diff"] == -1000
    print("✅ test_build_trades_and_actual")


if __name__ == "__main__":
    test_stop_before_10_all_methods()
    test_gap_through_stop_fills_at_open()
    test_ten_oclock_exit_and_time_method_hold()
    test_time_method_runs_to_close()
    test_time_method_no_profit_at_10()
    test_oco_target_and_same_bar_both()
    test_no_entry_bar_or_no_side()
    test_metrics()
    test_build_trades_and_actual()
    print("\n🎉 All exit_study tests passed!")
