"""從帳戶紀錄算績效指標。"""


def max_drawdown(equity):
    peak, mdd = float("-inf"), 0.0
    for e in equity:
        peak = max(peak, e)
        if peak > 0:
            mdd = min(mdd, e / peak - 1)
    return mdd


def account_metrics(acct, initial):
    eq = [r["equity"] for r in acct["equity"]]
    last = eq[-1] if eq else initial
    trades = acct["trades"]
    wins = [t for t in trades if t["pnl"] > 0]
    losses = [t for t in trades if t["pnl"] <= 0]
    cost = acct["fees_total"] + acct["tax_total"]
    open_pos = []
    for code, pos in acct["positions"].items():
        value = pos["shares"] * pos["last_price"]
        open_pos.append({
            "code": code, "shares": pos["shares"], "entry_date": pos["entry_date"],
            "entry_price": pos["entry_price"], "last_price": pos["last_price"],
            "value": round(value, 2), "pnl": round(value - pos["cost_basis"], 2),
            "pnl_pct": round(value / pos["cost_basis"] - 1, 4), "days_held": pos["days_held"],
            "entry_reason": pos["entry_reason"],
        })
    return {
        "equity": last,
        "ret": last / initial - 1,
        "max_dd": max_drawdown(eq),
        "n_closed": len(trades),
        "win_rate": (len(wins) / len(trades)) if trades else None,
        "avg_win": (sum(t["pnl_pct"] for t in wins) / len(wins)) if wins else None,
        "avg_loss": (sum(t["pnl_pct"] for t in losses) / len(losses)) if losses else None,
        "avg_hold": (sum(t["hold_days"] for t in trades) / len(trades)) if trades else None,
        "fees": acct["fees_total"], "tax": acct["tax_total"], "cost": cost,
        "cost_pct": cost / initial,
        "n_open": len(open_pos), "open": open_pos,
        "cash": acct["cash"],
    }
