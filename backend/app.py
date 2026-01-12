import os
import requests
from datetime import datetime, timezone
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

TWELVEDATA_KEY = os.getenv("TWELVEDATA_KEY", "")
BASE_URL = "https://api.twelvedata.com/time_series"

# 20 enstrüman
SYMBOLS = [
    {"name":"EURUSD","symbol":"EUR/USD","type":"forex"},
    {"name":"USDJPY","symbol":"USD/JPY","type":"forex"},
    {"name":"GBPUSD","symbol":"GBP/USD","type":"forex"},
    {"name":"AUDUSD","symbol":"AUD/USD","type":"forex"},
    {"name":"USDCAD","symbol":"USD/CAD","type":"forex"},
    {"name":"USDCHF","symbol":"USD/CHF","type":"forex"},
    {"name":"EURJPY","symbol":"EUR/JPY","type":"forex"},
    {"name":"EURGBP","symbol":"EUR/GBP","type":"forex"},
    {"name":"GBPJPY","symbol":"GBP/JPY","type":"forex"},
    {"name":"AUDJPY","symbol":"AUD/JPY","type":"forex"},
    {"name":"XAUUSD","symbol":"XAU/USD","type":"metal"},
    {"name":"XAGUSD","symbol":"XAG/USD","type":"metal"},
    {"name":"USOIL","symbol":"WTI","type":"energy"},
    {"name":"US500","symbol":"SPX","type":"index"},
    {"name":"NAS100","symbol":"NDX","type":"index"},
    {"name":"US30","symbol":"DJI","type":"index"},
    {"name":"GER40","symbol":"DAX","type":"index"},
    {"name":"UK100","symbol":"FTSE","type":"index"},
    {"name":"JP225","symbol":"NIKKEI","type":"index"},
    {"name":"AUS200","symbol":"ASX200","type":"index"},
]

# 5 sayfa x 4
PAGES = [
    ["EURUSD","USDJPY","GBPUSD","AUDUSD"],
    ["USDCAD","USDCHF","EURJPY","EURGBP"],
    ["GBPJPY","AUDJPY","XAUUSD","XAGUSD"],
    ["USOIL","US500","NAS100","US30"],
    ["GER40","UK100","JP225","AUS200"],
]

def to_unix_seconds(date_str: str) -> int:
    # TwelveData date like "2026-01-12"
    dt = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    return int(dt.timestamp())

def ema_series(values, period: int):
    if len(values) < period:
        return [None] * len(values)
    k = 2 / (period + 1)
    out = [None] * len(values)
    # seed with SMA of first period
    sma = sum(values[:period]) / period
    out[period-1] = sma
    e = sma
    for i in range(period, len(values)):
        e = values[i] * k + e * (1 - k)
        out[i] = e
    return out

def macd_hist(values, fast=12, slow=26, signal=9):
    ema_fast = ema_series(values, fast)
    ema_slow = ema_series(values, slow)

    macd_line = [None]*len(values)
    for i in range(len(values)):
        if ema_fast[i] is None or ema_slow[i] is None:
            macd_line[i] = None
        else:
            macd_line[i] = ema_fast[i] - ema_slow[i]

    # signal EMA over macd_line (skip Nones)
    # build compact then map back
    macd_compact = []
    idx_map = []
    for i, v in enumerate(macd_line):
        if v is not None:
            macd_compact.append(v)
            idx_map.append(i)

    sig_compact = ema_series(macd_compact, signal)
    signal_line = [None]*len(values)
    for j, src_i in enumerate(idx_map):
        signal_line[src_i] = sig_compact[j]

    hist = [None]*len(values)
    for i in range(len(values)):
        if macd_line[i] is None or signal_line[i] is None:
            hist[i] = None
        else:
            hist[i] = macd_line[i] - signal_line[i]
    return hist

def fetch_daily_ohlc(symbol: str, bars: int = 240):
    if not TWELVEDATA_KEY:
        raise RuntimeError("TWELVEDATA_KEY is missing in environment variables.")

    params = {"symbol": symbol, "interval": "1day", "outputsize": bars, "apikey": TWELVEDATA_KEY}
    r = requests.get(BASE_URL, params=params, timeout=25)
    data = r.json()
    if "values" not in data:
        raise RuntimeError(str(data))

    values = list(reversed(data["values"]))  # oldest -> newest
    candles = []
    closes = []
    for v in values:
        t = to_unix_seconds(v["datetime"])
        o = float(v["open"])
        h = float(v["high"])
        l = float(v["low"])
        c = float(v["close"])
        candles.append({"time": t, "open": o, "high": h, "low": l, "close": c})
        closes.append(c)

    return candles, closes

def classify_sheet(e20, e50, e100, e200):
    long_sheet = (e20 > e50 > e100 > e200)
    short_sheet = (e20 < e50 < e100 < e200)
    if long_sheet:
        return "LONG"
    if short_sheet:
        return "SHORT"
    return "NONE"

def get_items_for_page(page_index: int):
    wanted = set(PAGES[page_index])
    return [it for it in SYMBOLS if it["name"] in wanted]

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"],
)

@app.get("/api/pages")
def pages():
    return {"pages": len(PAGES), "page_sizes": [len(p) for p in PAGES]}

@app.get("/api/watchlist/{page}")
def watchlist_page(page: int):
    max_page = len(PAGES)
    if page < 1 or page > max_page:
        raise HTTPException(status_code=400, detail=f"page must be 1..{max_page}")

    now = datetime.now(timezone.utc).isoformat()
    items = []

    for it in get_items_for_page(page - 1):
        row = {"name": it["name"], "symbol": it["symbol"], "type": it["type"], "updated_utc": now}
        try:
            candles, closes = fetch_daily_ohlc(it["symbol"], bars=260)

            # EMA çizgileri (tam seri)
            e20  = ema_series(closes, 20)
            e50  = ema_series(closes, 50)
            e100 = ema_series(closes, 100)
            e200 = ema_series(closes, 200)

            # son geçerli değerler
            last_close = closes[-1]
            last_e20  = next(x for x in reversed(e20) if x is not None)
            last_e50  = next(x for x in reversed(e50) if x is not None)
            last_e100 = next(x for x in reversed(e100) if x is not None)
            last_e200 = next(x for x in reversed(e200) if x is not None)

            sheet = classify_sheet(last_e20, last_e50, last_e100, last_e200)

            # MACD histogram (opsiyonel görsel için)
            hist = macd_hist(closes)

            # payload küçült: son 180 bar gönder
            n = 180
            candles2 = candles[-n:]
            def pack_line(arr):
                out = []
                for i in range(len(candles) - n, len(candles)):
                    if i < 0: 
                        continue
                    v = arr[i]
                    if v is None:
                        continue
                    out.append({"time": candles[i]["time"], "value": v})
                return out

            row.update({
                "ok": True,
                "last_close": last_close,
                "ema20": last_e20, "ema50": last_e50, "ema100": last_e100, "ema200": last_e200,
                "sheet": sheet,
                "candles": candles2,
                "ema_lines": {
                    "ema20": pack_line(e20),
                    "ema50": pack_line(e50),
                    "ema100": pack_line(e100),
                    "ema200": pack_line(e200),
                },
                "macd_hist": [
                    {"time": candles[i]["time"], "value": hist[i]}
                    for i in range(len(candles) - n, len(candles))
                    if i >= 0 and hist[i] is not None
                ],
            })
        except Exception as e:
            row["ok"] = False
            row["error"] = str(e)

        items.append(row)

    priority = {"LONG": 0, "SHORT": 1, "NONE": 2}
    items.sort(key=lambda x: priority.get(x.get("sheet", "NONE"), 9))

    return {"page": page, "updated_utc": now, "items": items}
