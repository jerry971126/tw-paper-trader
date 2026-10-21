"""用 `python tests/test_engine.py` 執行（不需要 pytest）。"""
import copy
import json
import os
import sys
import traceback

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config as C                      # noqa: E402
import engine as E                      # noqa: E402
from strategies import ALL_STRATEGIES   # noqa: E402
import synth                            # noqa: E402


def run_all(codes, prices, inst, start, upto=None):
    p = E.Panel(prices, inst, universe=codes, benchmark=C.BENCHMARK)
    st = E.new_state(start_date=start)
    E.advance(st, p, upto=upto)
    return st, p


def snapshot(st):
    return json.dumps(st["accounts"], sort_keys=True, default=str)


# --------------------------------------------------------------------------
def test_cost_math():
    assert E.calc_fee(1000) == 1                       # 1000*0.001425*0.6=0.855 -> 最低 1 元
    assert E.calc_fee(100_000) == 86                   # 85.5 -> 86
    assert E.calc_tax("2330", 100_000) == 300
    assert E.calc_tax("0050", 100_000) == 100


class Stub:
    """假的 Panel，用來精準測試成交規則。"""
    def __init__(self, table):
        self.t = table

    def val(self, field, day, code):
        return self.t.get((field, day, code), float("nan"))


def fresh_acct(cash=100_000):
    a = E.new_account(ALL_STRATEGIES[0])
    a["cash"] = float(cash)
    return a


def test_buy_fill_arithmetic_and_limit_rules():
    a = fresh_acct()
    s = Stub({("open", "d1", "X"): 500.0, ("prev_close", "d1", "X"): 495.0})
    a["pending"] = [{"side": "buy", "code": "X", "target_amount": 20_000.0, "reason": "t"}]
    E.execute_pending(a, s, "d1")
    pos = a["positions"]["X"]
    assert pos["shares"] == 39                          # 20000/(500*1.000855)=39.96 -> 39
    fee = E.calc_fee(39 * 500)
    assert abs(a["cash"] - (100_000 - 39 * 500 - fee)) < 0.01
    assert abs(pos["cost_basis"] - (39 * 500 + fee)) < 0.01

    # 賣出：成交價=開盤價，扣手續費+證交稅
    s2 = Stub({("open", "d2", "X"): 520.0, ("prev_close", "d2", "X"): 510.0})
    a["pending"] = [{"side": "sell", "code": "X", "reason": "t"}]
    cash0 = a["cash"]
    E.execute_pending(a, s2, "d2")
    amt = 39 * 520
    assert abs(a["cash"] - (cash0 + amt - E.calc_fee(amt) - E.calc_tax("X", amt))) < 0.01
    assert "X" not in a["positions"] and len(a["trades"]) == 1

    # 漲停開盤買不到
    b = fresh_acct()
    s3 = Stub({("open", "d", "Y"): 110.0, ("prev_close", "d", "Y"): 100.0})
    b["pending"] = [{"side": "buy", "code": "Y", "target_amount": 20_000.0, "reason": "t"}]
    E.execute_pending(b, s3, "d")
    assert not b["positions"] and b["skipped"][0]["why"].startswith("漲停")

    # 跌停開盤賣不掉
    c = fresh_acct()
    c["positions"]["Z"] = {"shares": 10, "entry_price": 100.0, "entry_date": "x", "cost_basis": 1001.0,
                           "days_held": 3, "entry_reason": "t", "last_price": 100.0}
    s4 = Stub({("open", "d", "Z"): 90.0, ("prev_close", "d", "Z"): 100.0})
    c["pending"] = [{"side": "sell", "code": "Z", "reason": "t"}]
    E.execute_pending(c, s4, "d")
    assert "Z" in c["positions"] and c["skipped"][0]["why"].startswith("跌停")

    # 資金不足
    d = fresh_acct(cash=100)
    s5 = Stub({("open", "d", "W"): 500.0, ("prev_close", "d", "W"): 500.0})
    d["pending"] = [{"side": "buy", "code": "W", "target_amount": 20_000.0, "reason": "t"}]
    E.execute_pending(d, s5, "d")
    assert not d["positions"] and d["skipped"][0]["why"] == "資金不足"


def test_invariants():
    codes, prices, inst, dates = synth.world()
    st, p = run_all(codes, prices, inst, start=dates[260])
    for key, a in st["accounts"].items():
        assert a["cash"] >= -1e-6, (key, a["cash"])
        for pos in a["positions"].values():
            assert isinstance(pos["shares"], int) and pos["shares"] > 0
        strat = next(s for s in ALL_STRATEGIES if s.key == key)
        for row in a["equity"]:
            assert row["n_pos"] <= strat.max_positions
        for t in a["trades"]:
            assert t["exit_date"] > t["entry_date"]
        # 帳面平衡：現金 + 持股 = 淨值
        last = a["equity"][-1]
        val = sum(pos["shares"] * pos["last_price"] for pos in a["positions"].values())
        assert abs(last["equity"] - (a["cash"] + val)) < 0.05
        # 手續費+稅 = 各筆成交加總
        assert abs(a["fees_total"] - sum(f["fee"] for f in a["fills"])) < 1e-6
        assert abs(a["tax_total"] - sum(f["tax"] for f in a["fills"])) < 1e-6
    # 確保策略 A 和 C 真的有被執行到（避免測試形同虛設：C 需要法人資料有效才會交易）
    assert len(st["accounts"]["A"]["fills"]) > 0, "模擬資料下策略 A 完全沒有成交"
    assert len(st["accounts"]["C"]["fills"]) > 0, "模擬資料下策略 C 完全沒有成交（法人資料門檻可能擋掉了）"


def test_fills_use_next_day_open_only():
    """每筆成交價必須等於『成交日當天開盤價』，且成交日在訊號日之後。"""
    codes, prices, inst, dates = synth.world(seed=5)
    st, p = run_all(codes, prices, inst, start=dates[260])
    open_px = {c: prices[c].set_index("date")["open"] for c in prices}
    n = 0
    for a in st["accounts"].values():
        for f in a["fills"]:
            assert abs(f["price"] - open_px[f["code"]][f["date"]]) < 1e-9
            n += 1
    assert n > 10


def test_benchmark_buys_on_first_open():
    codes, prices, inst, dates = synth.world()
    start = dates[260]
    st, p = run_all(codes, prices, inst, start=start)
    d = st["accounts"]["D"]
    assert len(d["fills"]) == 1 and d["fills"][0]["side"] == "buy"
    assert d["fills"][0]["date"] == start             # START 當天開盤成交
    assert d["equity"][0]["date"] == dates[259]       # 初始化日 = START 前一個交易日
    assert d["equity"][0]["equity"] == C.INITIAL_CASH
    assert d["cash"] < d["fills"][0]["price"] * 2     # 幾乎全額投入（只剩零頭）


def test_no_lookahead():
    """未來的價格/法人資料即使被亂改，過去的成交、淨值、隔天待成交單都不能變。"""
    codes, prices, inst, dates = synth.world(seed=7)
    start, cut = dates[260], dates[320]
    base, _ = run_all(codes, prices, inst, start=start, upto=cut)

    rng = np.random.default_rng(99)
    alt_prices = copy.deepcopy(prices)
    for c, df in alt_prices.items():
        m = df["date"] > cut
        for col in ("open", "close", "high", "low"):
            df.loc[m, col] = df.loc[m, col] * rng.uniform(0.3, 3.0, m.sum())
        df.loc[m, "volume"] = df.loc[m, "volume"] * 50
    alt_inst = inst.copy()
    m = alt_inst["date"] > cut
    alt_inst.loc[m, "fnet"] = rng.integers(-10**8, 10**8, m.sum())

    alt, _ = run_all(codes, alt_prices, alt_inst, start=start, upto=cut)
    assert snapshot(base) == snapshot(alt), "過去的結果被未來資料改變了 → 有偷看未來"


def test_incremental_equals_batch_and_idempotent():
    """每天只多一天資料、逐日補算，結果必須和一次跑完完全一致；重跑不會重複計算。"""
    codes, prices, inst, dates = synth.world(seed=11)
    start = dates[260]
    batch, _ = run_all(codes, prices, inst, start=start)

    st = E.new_state(start_date=start)
    for k in range(255, len(dates)):                  # 資料一天一天長出來
        cut = dates[k]
        pr = {c: df[df["date"] <= cut] for c, df in prices.items()}
        ins = inst[inst["date"] <= cut]
        p = E.Panel(pr, ins, universe=codes, benchmark=C.BENCHMARK)
        E.advance(st, p)
    assert snapshot(batch) == snapshot(st)

    p = E.Panel(prices, inst, universe=codes, benchmark=C.BENCHMARK)
    before = snapshot(st)
    assert E.advance(st, p) == []
    assert snapshot(st) == before


def test_catch_up_after_missed_days():
    """中間好幾天沒跑，之後一次補算，結果和每天都有跑一樣。"""
    codes, prices, inst, dates = synth.world(seed=13)
    start = dates[260]
    batch, _ = run_all(codes, prices, inst, start=start)

    st = E.new_state(start_date=start)
    for cut in (dates[262], dates[300], dates[-1]):   # 分三次，中間漏跑很多天
        pr = {c: df[df["date"] <= cut] for c, df in prices.items()}
        p = E.Panel(pr, inst[inst["date"] <= cut], universe=codes, benchmark=C.BENCHMARK)
        E.advance(st, p)
    assert snapshot(batch) == snapshot(st)


def test_missing_inst_data_means_no_c_trades_and_gap_logged():
    codes, prices, inst, dates = synth.world()
    st, _ = run_all(codes, prices, None, start=dates[260])
    c = st["accounts"]["C"]
    assert c["fills"] == [] and len(c["gaps"]) > 0
    assert len(st["accounts"]["A"]["equity"]) == len(c["equity"])


def test_partial_last_day_is_ignored():
    """最後一天只有少數股票有資料 → 視為尚未完整，不處理。"""
    codes, prices, inst, dates = synth.world()
    pr = copy.deepcopy(prices)
    for c in codes[2:]:                               # 只留 2 檔 + 0050 有最後一天
        pr[c] = pr[c].iloc[:-1]
    p = E.Panel(pr, inst, universe=codes, benchmark=C.BENCHMARK)
    assert p.calendar[-1] == dates[-2]


def test_stop_loss_logic():
    """A 策略：收盤價比成交價低 8% 以上 → 產生賣單，且次日才成交。"""
    a = fresh_acct()
    a["positions"]["X"] = {"shares": 10, "entry_price": 100.0, "entry_date": "d0", "cost_basis": 1001.0,
                           "days_held": 2, "entry_reason": "t", "last_price": 100.0}
    s = Stub({("close", "d1", "X"): 91.9, ("ma20", "d1", "X"): 50.0})
    ex = ALL_STRATEGIES[0].exits(a, s, "d1")
    assert ex and ex[0][1].startswith("停損")
    s = Stub({("close", "d1", "X"): 92.5, ("ma20", "d1", "X"): 50.0})
    assert ALL_STRATEGIES[0].exits(a, s, "d1") == []


# --------------------------------------------------------------------------
if __name__ == "__main__":
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"PASS  {name}")
        except Exception:
            failed += 1
            print(f"FAIL  {name}")
            traceback.print_exc()
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
