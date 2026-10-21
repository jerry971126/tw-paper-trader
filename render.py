"""把 state 轉成網頁（docs/index.html，資料直接內嵌，單一檔案、不需要伺服器）。"""
import datetime as dt
import json
import os

import config as C
import metrics

HERE = os.path.dirname(os.path.abspath(__file__))


def build_payload(state):
    meta = state["meta"]
    initial = meta["initial_cash"]
    accts = []
    for key in ("A", "B", "C", "D"):
        a = state["accounts"][key]
        accts.append({
            "key": key, "name": a["name"], "desc": a["desc"],
            "metrics": metrics.account_metrics(a, initial),
            "equity": [[r["date"], r["equity"]] for r in a["equity"]],
            "fills": a["fills"][-400:],
            "trades": a["trades"][-400:],
            "pending": a["pending"],
            "skipped": a["skipped"][-40:],
            "gaps": a["gaps"][-40:],
            "n_gaps": len(a["gaps"]), "n_skipped": len(a["skipped"]),
        })
    bench = next(x for x in accts if x["key"] == "D")["metrics"]["ret"]
    for x in accts:
        x["metrics"]["excess"] = x["metrics"]["ret"] - bench
    p = meta["params"]
    return {
        "meta": {
            "start_date": meta["start_date"], "last_processed": meta["last_processed"],
            "generated": dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).strftime("%Y-%m-%d %H:%M"),
            "initial": initial, "max_positions": p["MAX_POSITIONS"],
            "fee_rate": p["FEE_RATE"], "fee_discount": p["FEE_DISCOUNT"], "min_fee": p["MIN_FEE"],
            "tax_stock": p["TAX_STOCK"], "tax_etf": p["TAX_ETF"], "n_universe": len(C.UNIVERSE),
        },
        "names": C.NAMES,
        "accounts": accts,
    }


def write_site(state, out_dir):
    payload = build_payload(state)
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(HERE, "web", "template.html"), encoding="utf-8") as f:
        html = f.read()
    blob = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")
    with open(os.path.join(out_dir, "index.html"), "w", encoding="utf-8") as f:
        f.write(html.replace("__DATA__", blob))
    with open(os.path.join(out_dir, "data.json"), "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)
    open(os.path.join(out_dir, ".nojekyll"), "a").close()
