"""用模擬資料跑完整流程並輸出頁面，只用來檢查頁面長相，不代表真實行情。
用法：python tests/make_demo.py <輸出資料夾> [模擬幾個交易日，預設 60]
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config as C      # noqa: E402
import engine as E      # noqa: E402
import render           # noqa: E402
import synth            # noqa: E402

out = sys.argv[1]
days = int(sys.argv[2]) if len(sys.argv) > 2 else 60
codes, prices, inst, dates = synth.world(n_codes=30, n_days=260 + days + 1, seed=21)
p = E.Panel(prices, inst, universe=codes, benchmark=C.BENCHMARK)
st = E.new_state(start_date=dates[260])
E.advance(st, p)
render.write_site(st, out)
for k, a in st["accounts"].items():
    print(k, a["name"], "淨值", a["equity"][-1]["equity"], "成交", len(a["fills"]), "平倉", len(a["trades"]))
