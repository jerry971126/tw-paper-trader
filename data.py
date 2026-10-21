"""資料抓取：價格（FinMind 為主、Yahoo 備援）與外資買賣超（證交所 T86）。

只在 GitHub Actions 或你自己的電腦上執行（需要對外網路）。
抓下來的資料只拿來算指標與成交價，不會拿來做任何「歷史績效評分」。
"""
import os
import time

import pandas as pd
import requests

UA = {"User-Agent": "Mozilla/5.0 (tw-paper-trader research script)"}
FINMIND_URL = "https://api.finmindtrade.com/api/v4/data"
T86_URL = "https://www.twse.com.tw/rwd/zh/fund/T86"
COLS = ["date", "open", "high", "low", "close", "volume"]


def _get_json(url, params=None, headers=None, retries=3, timeout=30):
    last = None
    for i in range(retries):
        try:
            r = requests.get(url, params=params, headers=headers or UA, timeout=timeout)
            r.raise_for_status()
            return r.json()
        except Exception as e:           # noqa: BLE001 - 重試後會原樣丟出
            last = e
            time.sleep(2 * (i + 1))
    raise last


def _clean(df):
    df = df[COLS].copy()
    df["date"] = df["date"].astype(str).str[:10]
    for c in COLS[1:]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df.drop_duplicates("date").sort_values("date").reset_index(drop=True)


# ------------------------------------------------------------------ 價格
def finmind_price(code, start, token=None):
    headers = dict(UA)
    if token:
        headers["Authorization"] = f"Bearer {token}"
    j = _get_json(FINMIND_URL, headers=headers,
                  params={"dataset": "TaiwanStockPrice", "data_id": code, "start_date": start})
    if j.get("status") != 200:
        raise RuntimeError(f"FinMind: {j.get('msg')}")
    df = pd.DataFrame(j.get("data", []))
    if df.empty:
        return df
    df = df.rename(columns={"max": "high", "min": "low", "Trading_Volume": "volume"})
    return _clean(df)


def yahoo_price(code, start):
    import yfinance as yf                # 只有備援時才需要
    for suffix in (".TW", ".TWO"):
        df = yf.download(code + suffix, start=start, auto_adjust=False, progress=False)
        if df is not None and len(df):
            break
    else:
        return pd.DataFrame(columns=COLS)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df.reset_index().rename(columns={
        "Date": "date", "Open": "open", "High": "high", "Low": "low",
        "Close": "close", "Volume": "volume"})
    return _clean(df)


def fetch_prices(codes, start, token=None, log=print):
    """回傳 ({代號: DataFrame}, [失敗的代號])。"""
    out, failed = {}, []
    for code in codes:
        df = None
        try:
            df = finmind_price(code, start, token)
        except Exception as e:           # noqa: BLE001
            log(f"  {code}: FinMind 失敗（{e}），改用 Yahoo")
        if df is None or df.empty:
            try:
                df = yahoo_price(code, start)
            except Exception as e:       # noqa: BLE001
                log(f"  {code}: Yahoo 也失敗（{e}）")
        if df is None or df.empty:
            failed.append(code)
            continue
        out[code] = df
        time.sleep(0.25)
    return out, failed


# ------------------------------------------------------------------ 外資買賣超
def _num(s):
    s = str(s).replace(",", "").strip()
    return int(s) if s not in ("", "--") else 0


def fetch_t86(day, codes):
    """回傳當天外資買賣超（股）DataFrame[date, code, fnet]；證交所尚無資料則回傳 None。

    外資 = 「外陸資買賣超股數(不含外資自營商)」+「外資自營商買賣超股數」。
    欄位用名稱比對，不寫死位置。
    """
    j = _get_json(T86_URL, params={"response": "json", "date": day.replace("-", ""),
                                   "selectType": "ALL"})
    if j.get("stat") != "OK" or not j.get("data"):
        return None
    idx = [i for i, f in enumerate(j["fields"]) if f.startswith("外") and "買賣超股數" in f]
    if not idx:
        raise RuntimeError(f"T86 找不到外資欄位：{j['fields']}")
    want = set(codes)
    rows = [(day, r[0].strip(), sum(_num(r[i]) for i in idx)) for r in j["data"]
            if r[0].strip() in want]
    return pd.DataFrame(rows, columns=["date", "code", "fnet"])


def update_inst_cache(days, codes, path, sleep=3.0, log=print):
    """補齊 days 裡快取沒有的日子；證交所有頻率限制，所以每次請求間隔幾秒。"""
    if os.path.exists(path):
        cache = pd.read_csv(path, dtype={"code": str, "date": str})
    else:
        cache = pd.DataFrame(columns=["date", "code", "fnet"])
    have = set(cache["date"])
    added = []
    for d in days:
        if d in have:
            continue
        try:
            df = fetch_t86(d, codes)
        except Exception as e:           # noqa: BLE001
            log(f"  T86 {d}: 失敗（{e}）")
            df = None
        time.sleep(sleep)
        if df is None:
            log(f"  T86 {d}: 目前沒有資料")
            continue
        added.append(df)
        log(f"  T86 {d}: 取得 {len(df)} 檔")
    if added:
        cache = pd.concat([cache] + added, ignore_index=True)
        cache = cache.drop_duplicates(["date", "code"]).sort_values(["date", "code"])
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        cache.to_csv(path, index=False)
    return cache


# ------------------------------------------------------------------ 診斷
def selftest(token=None):
    """在 GitHub Actions 手動執行，檢查每個資料來源通不通、格式對不對。"""
    import datetime as dt
    ok = {}
    start = (dt.date.today() - dt.timedelta(days=14)).isoformat()
    print("== 價格：FinMind 2330 / 0050")
    for code in ("2330", "0050"):
        try:
            df = finmind_price(code, start, token)
            print(df.tail(3).to_string(index=False))
            ok[f"finmind:{code}"] = len(df) > 0
        except Exception as e:           # noqa: BLE001
            print("  失敗：", e)
            ok[f"finmind:{code}"] = False
    print("== 價格：Yahoo 2330（備援）")
    try:
        df = yahoo_price("2330", start)
        print(df.tail(3).to_string(index=False))
        ok["yahoo:2330"] = len(df) > 0
    except Exception as e:               # noqa: BLE001
        print("  失敗：", e)
        ok["yahoo:2330"] = False
    print("== 法人：證交所 T86（往回找最近一個有資料的日子）")
    ok["twse_t86"] = False
    d = dt.date.today()
    for _ in range(10):
        if d.weekday() < 5:
            try:
                df = fetch_t86(d.isoformat(), ["2330", "2317", "2454"])
            except Exception as e:       # noqa: BLE001
                print(f"  {d} 失敗：", e)
                df = None
            time.sleep(3)
            if df is not None and len(df):
                print(df.to_string(index=False))
                ok["twse_t86"] = True
                break
        d -= dt.timedelta(days=1)
    print("\n== 結果 ==")
    for k, v in ok.items():
        print(f"  {'OK  ' if v else 'FAIL'} {k}")
    price_ok = ok["finmind:2330"] and ok["finmind:0050"] or ok["yahoo:2330"]
    print("\n價格來源：", "可用" if price_ok else "不可用 → 無法執行實驗")
    print("法人來源：", "可用" if ok["twse_t86"] else "不可用 → 策略 C 會因缺資料而當天不交易")
    return price_ok
