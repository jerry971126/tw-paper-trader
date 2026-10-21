"""紙上交易引擎（純計算，不碰網路）。

核心規則（避免偷看未來）：
  1. 第 D 天收盤後，只用到 D 以前的資料算訊號，產生「待成交單」。
  2. 待成交單在下一個交易日的「開盤價」成交（同一天先賣後買），並扣手續費與證交稅。
  3. 漲停開盤買不到、跌停開盤賣不掉、停牌 → 當天不成交（訊號若仍成立，隔天會重新產生）。
  4. 已處理過的交易日不會重算；每次執行會自動補算所有新的交易日。
"""
import datetime as dt

import numpy as np
import pandas as pd

import config as C
from strategies import ALL_STRATEGIES

NAN = float("nan")


# ---------------------------------------------------------------- 成本
def calc_fee(amount):
    return max(C.MIN_FEE, int(round(amount * C.FEE_RATE * C.FEE_DISCOUNT)))


def calc_tax(code, amount):
    rate = C.TAX_ETF if code.startswith("00") else C.TAX_STOCK
    return int(amount * rate)


# ---------------------------------------------------------------- 指標面板
def _rsi(close, n=14):
    d = close.diff()
    gain = d.clip(lower=0)
    loss = (-d).clip(lower=0)
    ag = gain.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    al = loss.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    out = 100 - 100 / (1 + ag / al)
    return out.where(ag.notna() & al.notna())


class Panel:
    """把各檔股票的日線整理成「日期 × 代號」的寬表，並算好指標。

    所有指標都只用到「當天以前（含當天）」的資料（rolling / ewm / shift(正數)），
    所以整張表一次算完不會偷看未來。tests 會驗證這點。
    """

    def __init__(self, prices, inst, universe=None, benchmark=None):
        universe = list(universe if universe is not None else C.UNIVERSE)
        self.benchmark = benchmark or C.BENCHMARK
        avail = [c for c in universe + [self.benchmark] if c in prices and len(prices[c])]
        self.universe = [c for c in universe if c in avail]

        def wide(col):
            return pd.DataFrame({c: prices[c].set_index("date")[col] for c in avail}).sort_index()

        o, cl, v = wide("open"), wide("close"), wide("volume")
        have = (cl > 0).astype(float)
        cov = have[self.universe].sum(axis=1) / max(1, len(self.universe))
        bench_ok = have[self.benchmark] > 0 if self.benchmark in have else cov > 9
        cal = cov.index[(cov >= C.MIN_COVERAGE) & bench_ok]
        self.calendar = [str(d) for d in cal]

        self.close = cl.where(cl > 0).reindex(cal).ffill()
        self.open = o.where(o > 0).reindex(cal)
        self.volume = v.reindex(cal)
        self.prev_close = self.close.shift(1)

        self.ma20 = self.close.rolling(20, min_periods=20).mean()
        self.ma60 = self.close.rolling(60, min_periods=60).mean()
        self.ma200 = self.close.rolling(200, min_periods=200).mean()
        self.ret20 = self.close / self.close.shift(20) - 1
        self.rsi14 = _rsi(self.close)

        # 外資買賣超（股）。缺資料的日子保持 NaN，C 策略當天不交易。
        cols = list(self.close.columns)
        if inst is not None and len(inst):
            f = inst.pivot_table(index="date", columns="code", values="fnet", aggfunc="sum")
            f = f.reindex(columns=cols)
            # 當天至少要有一定檔數的資料才算有效（門檻隨標的池大小縮放，標的池 50 檔時 = 20 檔）
            min_rows = min(C.INST_MIN_ROWS, max(1, int(0.4 * len(self.universe))))
            day_has = f.notna().sum(axis=1) >= min_rows
            f = f.reindex(cal)
            day_has = day_has.reindex(cal).fillna(False).astype(bool)
            f.loc[day_has] = f.loc[day_has].fillna(0.0)   # 當天有資料、但這檔沒出現 = 沒有買賣
            self.fnet = f
            self.inst_ok = day_has
        else:
            self.fnet = pd.DataFrame(np.nan, index=cal, columns=cols)
            self.inst_ok = pd.Series(False, index=cal)

        n_buy, n_sell = C.FLOW_BUY_DAYS, C.FLOW_SELL_DAYS
        self.buy_streak = self._streak(self.fnet > 0, n_buy)
        self.sell_streak = self._streak(self.fnet < 0, n_sell)
        self.flow3 = self.fnet.rolling(n_buy, min_periods=n_buy).sum() / \
            self.volume.rolling(20, min_periods=20).mean()

    @staticmethod
    def _streak(flag, n):
        out = flag.copy()
        for k in range(1, n):
            out = out & flag.shift(k, fill_value=False)
        return out.astype(bool)

    def val(self, field, day, code):
        try:
            return float(getattr(self, field).at[day, code])
        except (KeyError, ValueError, AttributeError):
            return NAN

    def inst_ok_at(self, day):
        try:
            return bool(self.inst_ok.at[day])
        except KeyError:
            return False


# ---------------------------------------------------------------- 帳戶/狀態
def new_account(strat):
    return {
        "key": strat.key, "name": strat.name, "desc": strat.desc,
        "cash": float(C.INITIAL_CASH), "positions": {}, "pending": [],
        "fills": [], "trades": [], "equity": [], "skipped": [], "gaps": [],
        "fees_total": 0.0, "tax_total": 0.0,
    }


def new_state(start_date=None):
    return {
        "meta": {
            "start_date": start_date or C.START_DATE,
            "last_processed": None,
            "initial_cash": C.INITIAL_CASH,
            "created_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
            "params": C.params_snapshot(),
            "universe": C.UNIVERSE,
        },
        "accounts": {s.key: new_account(s) for s in ALL_STRATEGIES},
        "runs": [],
    }


# ---------------------------------------------------------------- 單日處理
def _skip(acct, day, code, side, why):
    acct["skipped"].append({"date": day, "code": code, "side": side, "why": why})


def execute_pending(acct, p, day):
    pend, acct["pending"] = acct["pending"], []
    sells = [o for o in pend if o["side"] == "sell"]
    buys = [o for o in pend if o["side"] == "buy"]

    for o in sells:
        code = o["code"]
        pos = acct["positions"].get(code)
        if not pos:
            continue
        op, pc = p.val("open", day, code), p.val("prev_close", day, code)
        if not op > 0:
            _skip(acct, day, code, "sell", "當日無開盤價（停牌）")
            continue
        if pc > 0 and op <= pc * (1 - C.LIMIT_PCT * 0.99):
            _skip(acct, day, code, "sell", "跌停開盤，賣不掉")
            continue
        amt = pos["shares"] * op
        fee, tax = calc_fee(amt), calc_tax(code, amt)
        net = amt - fee - tax
        acct["cash"] = round(acct["cash"] + net, 2)
        acct["fees_total"] += fee
        acct["tax_total"] += tax
        pnl = net - pos["cost_basis"]
        acct["trades"].append({
            "code": code, "entry_date": pos["entry_date"], "exit_date": day,
            "shares": pos["shares"], "entry_price": pos["entry_price"], "exit_price": op,
            "pnl": round(pnl, 2), "pnl_pct": round(pnl / pos["cost_basis"], 4),
            "hold_days": pos["days_held"], "entry_reason": pos["entry_reason"],
            "exit_reason": o["reason"],
        })
        acct["fills"].append({
            "date": day, "code": code, "side": "sell", "price": op, "shares": pos["shares"],
            "amount": round(amt, 2), "fee": fee, "tax": tax, "reason": o["reason"],
        })
        del acct["positions"][code]

    for o in buys:
        code = o["code"]
        if code in acct["positions"]:
            continue
        op, pc = p.val("open", day, code), p.val("prev_close", day, code)
        if not op > 0:
            _skip(acct, day, code, "buy", "當日無開盤價（停牌）")
            continue
        if pc > 0 and op >= pc * (1 + C.LIMIT_PCT * 0.99):
            _skip(acct, day, code, "buy", "漲停開盤，買不到")
            continue
        budget = min(o["target_amount"], acct["cash"])
        n = int(budget // (op * (1 + C.FEE_RATE * C.FEE_DISCOUNT)))
        while n > 0 and n * op + calc_fee(n * op) > acct["cash"] + 1e-9:
            n -= 1
        if n < 1:
            _skip(acct, day, code, "buy", "資金不足")
            continue
        amt = n * op
        fee = calc_fee(amt)
        acct["cash"] = round(acct["cash"] - amt - fee, 2)
        acct["fees_total"] += fee
        acct["positions"][code] = {
            "shares": n, "entry_price": op, "entry_date": day,
            "cost_basis": round(amt + fee, 2), "days_held": 0,
            "entry_reason": o["reason"], "last_price": op,
        }
        acct["fills"].append({
            "date": day, "code": code, "side": "buy", "price": op, "shares": n,
            "amount": round(amt, 2), "fee": fee, "tax": 0, "reason": o["reason"],
        })


def mark_to_market(acct, p, day):
    value = 0.0
    for code, pos in acct["positions"].items():
        c = p.val("close", day, code)
        if c > 0:
            pos["last_price"] = c
        value += pos["shares"] * pos["last_price"]
    acct["equity"].append({
        "date": day, "equity": round(acct["cash"] + value, 2),
        "cash": round(acct["cash"], 2), "n_pos": len(acct["positions"]),
    })


def make_orders(acct, strat, p, day):
    if strat.needs_inst and not p.inst_ok_at(day):
        acct["gaps"].append({"date": day, "what": "缺少法人資料，當日不產生訊號"})
        return []
    equity = acct["equity"][-1]["equity"]
    sells = strat.exits(acct, p, day)
    sell_codes = {c for c, _ in sells}
    held_after = set(acct["positions"]) - sell_codes
    slots = strat.max_positions - len(held_after)
    orders = [{"side": "sell", "code": c, "reason": why, "signal_date": day} for c, why in sells]
    if slots > 0:
        target = round(equity / strat.max_positions, 2)
        for code, score, why in strat.entries(acct, p, day):
            if slots <= 0:
                break
            if code in held_after or code in sell_codes:
                continue
            orders.append({"side": "buy", "code": code, "target_amount": target,
                           "reason": why, "score": round(score, 4), "signal_date": day})
            slots -= 1
    return orders


def process_day(acct, strat, p, day):
    execute_pending(acct, p, day)
    mark_to_market(acct, p, day)
    for pos in acct["positions"].values():
        pos["days_held"] += 1
    acct["pending"] = make_orders(acct, strat, p, day)


# ---------------------------------------------------------------- 多日推進
def days_to_process(state, calendar):
    last, start = state["meta"]["last_processed"], state["meta"]["start_date"]
    if last is None:
        # 要等到 START_DATE（或之後）的資料出現才開始；初始化日 = START 之前最後一個交易日。
        # 如果資料還沒到 START，我們無法確定「START 前最後一個交易日」是哪天，所以先不動。
        if not any(d >= start for d in calendar):
            return []
        before = [d for d in calendar if d < start]
        if not before:
            raise RuntimeError("歷史資料沒有涵蓋 START_DATE 之前的交易日，無法建立初始訊號")
        return [d for d in calendar if d >= before[-1]]
    return [d for d in calendar if d > last]


def advance(state, p, upto=None, strategies=None):
    strategies = strategies or ALL_STRATEGIES
    done = []
    for d in days_to_process(state, p.calendar):
        if upto is not None and d > upto:
            break
        for s in strategies:
            process_day(state["accounts"][s.key], s, p, d)
        state["meta"]["last_processed"] = d
        done.append(d)
    return done
