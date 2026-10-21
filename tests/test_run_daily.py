"""run_daily.main() 的端到端測試：用假的資料來源取代網路。
用法：python tests/test_run_daily.py
"""
import json
import os
import sys
import tempfile
import traceback

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config as C          # noqa: E402
import data                 # noqa: E402
import run_daily            # noqa: E402
import synth                # noqa: E402

codes, PRICES, INST, DATES = synth.world(n_codes=14, n_days=300, seed=3)
C.START_DATE = DATES[260]          # 讓模擬資料涵蓋起始日（必須在建立 state 前設定）


def fake_sources(last_idx, inst_last_idx=None, drop_bench=False):
    """資料只到 DATES[last_idx]；法人資料只到 inst_last_idx（預設同價格）。"""
    inst_last_idx = last_idx if inst_last_idx is None else inst_last_idx
    pr = {c: df[df["date"] <= DATES[last_idx]].copy() for c, df in PRICES.items()
          if not (drop_bench and c == C.BENCHMARK)}
    ins = INST[INST["date"] <= DATES[inst_last_idx]].copy()
    data.fetch_prices = lambda cs, start, token=None, log=print: (pr, [])
    data.update_inst_cache = lambda days, cs, path, sleep=3.0, log=print: ins


def run(tmp, **kw):
    args = ["--state", f"{tmp}/state.json", "--inst-cache", f"{tmp}/inst.csv", "--out", f"{tmp}/docs"]
    return run_daily.main(args)


def load(tmp):
    with open(f"{tmp}/state.json", encoding="utf-8") as f:
        return json.load(f)


def test_before_start_does_nothing_then_starts():
    with tempfile.TemporaryDirectory() as tmp:
        fake_sources(255)                                   # 資料還沒到起始日
        assert run(tmp) == 0
        st = load(tmp)
        assert st["meta"]["last_processed"] is None and st["accounts"]["D"]["equity"] == []
        fake_sources(261)                                   # 起始日已過 → 一次補算
        assert run(tmp) == 0
        st = load(tmp)
        assert st["meta"]["last_processed"] == DATES[261]
        assert st["accounts"]["D"]["equity"][0]["date"] == DATES[259]
        assert st["accounts"]["D"]["fills"][0]["date"] == DATES[260]
        assert os.path.exists(f"{tmp}/docs/index.html") and os.path.exists(f"{tmp}/docs/data.json")


def test_rerun_is_idempotent():
    with tempfile.TemporaryDirectory() as tmp:
        fake_sources(270)
        run(tmp)
        a = load(tmp)["accounts"]
        run(tmp)
        assert load(tmp)["accounts"] == a


def test_defers_latest_day_without_inst_then_catches_up():
    with tempfile.TemporaryDirectory() as tmp:
        fake_sources(270, inst_last_idx=269)                # 最新一天法人資料還沒出來
        run(tmp)
        assert load(tmp)["meta"]["last_processed"] == DATES[269]
        fake_sources(270)                                   # 隔天資料補上
        run(tmp)
        assert load(tmp)["meta"]["last_processed"] == DATES[270]
        full = load(tmp)["accounts"]
    with tempfile.TemporaryDirectory() as tmp2:             # 與一次跑完結果相同
        fake_sources(270)
        run(tmp2)
        assert load(tmp2)["accounts"] == full


def test_missing_benchmark_aborts_without_touching_state():
    with tempfile.TemporaryDirectory() as tmp:
        fake_sources(270)
        run(tmp)
        before = open(f"{tmp}/state.json", encoding="utf-8").read()
        fake_sources(275, drop_bench=True)
        assert run(tmp) == 1
        assert open(f"{tmp}/state.json", encoding="utf-8").read() == before


def test_changed_params_abort():
    with tempfile.TemporaryDirectory() as tmp:
        fake_sources(270)
        run(tmp)
        old = C.TREND_STOP
        C.TREND_STOP = 0.05
        try:
            try:
                run(tmp)
                raise AssertionError("參數被改了卻沒有停下來")
            except SystemExit as e:
                assert "不一致" in str(e)
        finally:
            C.TREND_STOP = old


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
