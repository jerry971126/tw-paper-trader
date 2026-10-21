"""每日更新：抓資料 → 補算所有新的交易日 → 存檔 → 更新頁面。

用法：
    python run_daily.py              # 正常每日更新
    python run_daily.py --selftest   # 診斷各資料來源（不改任何狀態）
"""
import argparse
import datetime as dt
import json
import os
import sys

import config as C
import data
import engine as E

HERE = os.path.dirname(os.path.abspath(__file__))


def load_state(path):
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return None


def save_state(state, path):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def check_params_unchanged(state):
    """實驗期間參數被改過就停下來，避免默默汙染結果。"""
    old, now = state["meta"]["params"], C.params_snapshot()
    diff = {k: (old.get(k), now[k]) for k in now if old.get(k) != now[k]}
    if diff:
        raise SystemExit(f"config.py 的參數和實驗開始時不一致：{diff}\n"
                         "實驗中途改參數會讓結果失去意義。要改請另開新實驗；"
                         "若確定要繼續，請把 config.py 改回原值。")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", default=os.path.join(HERE, "state", "state.json"))
    ap.add_argument("--inst-cache", default=os.path.join(HERE, "data", "inst_foreign.csv"))
    ap.add_argument("--out", default=os.path.join(HERE, "docs"))
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)
    token = os.environ.get("FINMIND_TOKEN") or None

    if args.selftest:
        return 0 if data.selftest(token) else 1

    state = load_state(args.state) or E.new_state()
    check_params_unchanged(state)
    start = state["meta"]["start_date"]
    fetch_from = (dt.date.fromisoformat(start) - dt.timedelta(days=C.HISTORY_DAYS)).isoformat()

    print(f"抓取價格（{len(C.UNIVERSE) + 1} 檔，自 {fetch_from}）…")
    prices, failed = data.fetch_prices(list(C.UNIVERSE) + [C.BENCHMARK], fetch_from, token)
    if C.BENCHMARK not in prices:
        print("抓不到 0050 價格，今天不更新（不改動任何狀態）。")
        return 1
    if len(failed) > 5:
        print(f"有 {len(failed)} 檔抓不到價格 {failed}，資料來源可能異常，今天不更新。")
        return 1
    if failed:
        print(f"警告：這些股票抓不到價格，從標的池排除：{failed}")

    # 先用價格建日曆，決定要處理哪些交易日，再抓需要的法人資料
    p0 = E.Panel(prices, None, universe=list(C.UNIVERSE), benchmark=C.BENCHMARK)
    todo = E.days_to_process(state, p0.calendar)
    print(f"資料最後交易日：{p0.calendar[-1] if p0.calendar else '無'}；待處理 {len(todo)} 天")

    inst = None
    if todo:
        i0 = max(0, p0.calendar.index(todo[0]) - 6)           # 連續買超需要往前看幾天
        need = p0.calendar[i0:]
        print("更新外資買賣超快取…")
        inst = data.update_inst_cache(need, list(C.UNIVERSE), args.inst_cache)
    elif os.path.exists(args.inst_cache):
        import pandas as pd
        inst = pd.read_csv(args.inst_cache, dtype={"code": str, "date": str})

    p = E.Panel(prices, inst, universe=list(C.UNIVERSE), benchmark=C.BENCHMARK)

    # 最新一天若證交所法人資料還沒出來，先不處理它（明天會一起補算），避免策略 C 漏掉那天的訊號
    upto = None
    if todo and todo[-1] == p.calendar[-1] and not p.inst_ok_at(todo[-1]):
        upto = p.calendar[-2] if len(p.calendar) > 1 else None
        print(f"{todo[-1]} 的法人資料尚未公布，這次先處理到 {upto}，下次執行會補上。")
        if upto is None:
            todo = []

    done = E.advance(state, p, upto=upto)
    print(f"已處理：{done if done else '（沒有新的交易日）'}")

    state["runs"].append({
        "at_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "processed": done, "missing_prices": failed,
    })
    state["runs"] = state["runs"][-60:]
    save_state(state, args.state)

    import render
    render.write_site(state, args.out)
    print("完成。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
