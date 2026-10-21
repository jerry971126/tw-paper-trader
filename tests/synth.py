"""產生模擬股價與法人資料，只用於測試邏輯，不代表任何真實行情。"""
import numpy as np
import pandas as pd

import config as C


def make_prices(codes, n_days=380, first="2025-06-02", seed=1, vol=0.018):
    rng = np.random.default_rng(seed)
    dates = [d.strftime("%Y-%m-%d") for d in pd.bdate_range(first, periods=n_days)]
    market = rng.normal(0.0004, 0.009, n_days)
    out = {}
    for i, code in enumerate(codes):
        base = rng.uniform(30, 900)
        regime = np.repeat(rng.normal(0.0, 0.002, n_days // 40 + 1), 40)[:n_days]
        r = 0.6 * market + rng.normal(0, vol, n_days) + regime
        close = base * np.exp(np.cumsum(r))
        opn = np.empty(n_days)
        opn[0] = close[0]
        opn[1:] = close[:-1] * (1 + rng.normal(0, 0.004, n_days - 1))
        out[code] = pd.DataFrame({
            "date": dates, "open": np.round(opn, 2), "high": np.round(np.maximum(opn, close) * 1.005, 2),
            "low": np.round(np.minimum(opn, close) * 0.995, 2), "close": np.round(close, 2),
            "volume": rng.lognormal(15, 0.3, n_days).astype(int),
        })
    return out


def make_inst(codes, dates, seed=2):
    rng = np.random.default_rng(seed)
    rows = []
    for code in codes:
        x = 0.0
        for d in dates:
            x = 0.55 * x + rng.normal(0, 1_500_000)
            rows.append((d, code, int(x)))
    return pd.DataFrame(rows, columns=["date", "code", "fnet"])


def world(n_codes=14, n_days=380, seed=1):
    codes = list(C.UNIVERSE)[:n_codes]
    prices = make_prices(codes + [C.BENCHMARK], n_days=n_days, seed=seed)
    dates = list(prices[C.BENCHMARK]["date"])
    inst = make_inst(codes, dates, seed=seed + 1)
    return codes, prices, inst, dates
