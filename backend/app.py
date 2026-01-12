import os
import requests
from datetime import datetime, timezone
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

TWELVEDATA_KEY = os.getenv("TWELVEDATA_KEY", "")

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

# ✅ 20 enstrüman → 5 sayfa x 4
PAGES = [
    ["EURUSD","USDJPY","GBPUSD","AUDUSD"],
    ["USDCAD","USDCHF","EURJPY","EURGBP"],
    ["GBPJPY","AUDJPY","XAUUSD","XAGUSD"],
    ["USOIL","US500","NAS100","US30"],
    ["GER40","UK100","JP225","AUS200"],
]

BASE_URL = "https://api.twelvedata.com/time_series"

def ema(series, period):
    k = 2 / (period + 1)
    e = series[0]
    for price in series[1:]:
        e = price * k + e * (1 - k)
    return e

def fetch_daily_closes(symbol, bars=260):
    if not TWELVEDATA_KEY:
        raise RuntimeError("TWELVEDATA_KEY is missing in environment variables.")

    params = {"symbol": symbol, "interval": "1day", "outputsize": bars, "apikey": TWELVEDATA_KEY}
    r = requests.get(BASE_URL, params=params, timeout=25)
    data = r.json()
    if "values" not in data:
        raise RuntimeError(str(data))

    values = list(reversed(data["values"]))  # oldest -> newest
    closes = [float(v["close"]) for v in values]
    return closes

def classify(closes):
    if len(closes) < 210:
        return {"ok": False, "reason": "not_enough_data"}

    e20  = ema(closes[-210:], 20)
    e50  = ema(closes[-210:], 50)
    e100 = ema(closes[-210:], 100)
    e200 = ema(closes[-210:], 200)

    long_sheet  = (e20 > e50 > e100 > e200)
    short_sheet = (e20 < e50 < e100 < e200)

    sheet = "LONG" if long_sheet else ("SHORT" if short_sheet else "NONE")

    return {
        "ok": True,
        "ema20": e20, "ema50": e50, "ema100": e100, "ema200": e200,
        "long": long_sheet, "short": short_sheet, "sheet": sheet
    }

def get_items_for_page(page_index: int):
    wanted = set(PAGES[page_index])
    return [it for it in SYMBOLS if it["name"] in wanted]

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"],
)

@app.get("/api/watchlist/{page}")
def watchlist_page(page: int):
    max_page = len(PAGES)
    if page < 1 or page > max_page:
        raise HTTPException(status_code=400, detail=f"page must be 1..{max_page}")

    page_index = page - 1
    now = datetime.now(timezone.utc).isoformat()
    items = []

    for it in get_items_for_page(page_index):
        row = {"name": it["name"], "symbol": it["symbol"], "type": it["type"], "updated_utc": now}
        try:
            closes = fetch_daily_closes(it["symbol"])
            row["last_close"] = closes[-1]
            row.update(classify(closes))
        except Exception as e:
            row["ok"] = False
            row["error"] = str(e)
        items.append(row)

    priority = {"LONG": 0, "SHORT": 1, "NONE": 2}
    items.sort(key=lambda x: priority.get(x.get("sheet", "NONE"), 9))

    return {"page": page, "updated_utc": now, "items": items}
