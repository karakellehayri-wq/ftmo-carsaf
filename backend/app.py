import os
import requests
from datetime import datetime, timezone
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

TWELVEDATA_KEY = os.getenv("TWELVEDATA_KEY", "")
BASE_URL = "https://api.twelvedata.com/time_series"

# LuxAlgo default input'ları
LEFT_BARS = int(os.getenv("SR_LEFT_BARS", "15"))
RIGHT_BARS = int(os.getenv("SR_RIGHT_BARS", "15"))
VOLUME_THRESH = float(os.getenv("SR_VOLUME_THRESH", "20"))

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
    dt = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    return int(dt.timestamp())

def ema_series(values, period: int):
    if len(values) < period:
        return [None] * len(values)
    k = 2 / (period + 1)
    out = [None] * len(values)
    sma = sum(values[:period]) / period
    out[period - 1] = sma
    e = sma
    for i in range(period, len(values)):
        e = values[i] * k + e * (1 - k)
        out[i] = e
    return out

def macd_hist(values, fast=12, slow=26, signal=9):
    ema_fast = ema_series(values, fast)
    ema_slow = ema_series(values, slow)

    macd_line = [None] * len(values)
    for i in range(len(values)):
        if ema_fast[i] is None or ema_slow[i] is None:
            macd_line[i] = None
        else:
            macd_line[i] = ema_fast[i] - ema_slow[i]

    macd_compact = []
    idx_map = []
    for i, v in enumerate(macd_line):
        if v is not None:
            macd_compact.append(v)
            idx_map.append(i)

    sig_compact = ema_series(macd_compact, signal)
    signal_line = [None] * len(values)
    for j, src_i in enumerate(idx_map):
        signal_line[src_i] = sig_compact[j]

    hist = [None] * len(values)
    for i in range(len(values)):
        if macd_line[i] is None or signal_line[i] is None:
            hist[i] = None
        else:
            hist[i] = macd_line[i] - signal_line[i]
    return hist

def fetch_daily_ohlc(symbol: str, bars: int = 900):
    if not TWELVEDATA_KEY:
        raise RuntimeError("TWELVEDATA_KEY is missing in environment variables.")

    params = {"symbol": symbol, "interval": "1day", "outputsize": bars, "apikey": TWELVEDATA_KEY}
    r = requests.get(BASE_URL, params=params, timeout=25)
    data = r.json()
    if "values" not in data:
        raise RuntimeError(str(data))

    values = list(reversed(data["values"]))  # oldest -> newest

    candles = []
    opens, highs, lows, closes, volumes = [], [], [], [], []

    for v in values:
        t = to_unix_seconds(v["datetime"])
        o = float(v["open"])
        h = float(v["high"])
        l = float(v["low"])
        c = float(v["close"])
        vol = v.get("volume", None)
        try:
            vol_f = float(vol) if vol is not None else None
        except:
            vol_f = None

        candles.append({"time": t, "open": o, "high": h, "low": l, "close": c})
        opens.append(o); highs.append(h); lows.append(l); closes.append(c); volumes.append(vol_f)

    return candles, opens, highs, lows, closes, volumes

def classify_sheet(e20, e50, e100, e200):
    long_sheet = (e20 > e50 > e100 > e200)
    short_sheet = (e20 < e50 < e100 < e200)
    if long_sheet:
        return "LONG"
    if short_sheet:
        return "SHORT"
    return "NONE"

def pivot_high(highs, left, right):
    n = len(highs)
    out = [None] * n
    for i in range(left, n - right):
        v = highs[i]
        ok = True
        for k in range(1, left + 1):
            if highs[i - k] >= v:
                ok = False
                break
        if not ok:
            continue
        for k in range(1, right + 1):
            if highs[i + k] > v:
                ok = False
                break
        if ok:
            out[i] = v
    return out

def pivot_low(lows, left, right):
    n = len(lows)
    out = [None] * n
    for i in range(left, n - right):
        v = lows[i]
        ok = True
        for k in range(1, left + 1):
            if lows[i - k] <= v:
                ok = False
                break
        if not ok:
            continue
        for k in range(1, right + 1):
            if lows[i + k] < v:
                ok = False
                break
        if ok:
            out[i] = v
    return out

def forward_fill(series):
    last = None
    out = []
    for v in series:
        if v is not None:
            last = v
        out.append(last)
    return out

def compute_volume_osc(volumes):
    # LuxAlgo: short=ema(volume,5), long=ema(volume,10), osc=100*(short-long)/long
    # Volume yoksa (forex) -> osc 0
    vals = [v if v is not None else 0.0 for v in volumes]
    short = ema_series(vals, 5)
    long = ema_series(vals, 10)
    osc = [0.0] * len(vals)
    for i in range(len(vals)):
        if short[i] is None or long[i] is None or long[i] == 0:
            osc[i] = 0.0
        else:
            osc[i] = 100.0 * (short[i] - long[i]) / long[i]
    return osc

def crossunder(prev_a, a, prev_b, b):
    if prev_a is None or a is None or prev_b is None or b is None:
        return False
    return prev_a >= prev_b and a < b

def crossover(prev_a, a, prev_b, b):
    if prev_a is None or a is None or prev_b is None or b is None:
        return False
    return prev_a <= prev_b and a > b

def sr_segments(candles, pivot_vals, kind, max_segments=25):
    """
    Pine plot: change(pivot) ? na : color  ==> segment segment çiz
    Biz: pivot değiştikçe yeni segment başlatıyoruz.
    """
    times = [c["time"] for c in candles]
    segs = []
    current = None
    start_t = None

    for i, v in enumerate(pivot_vals):
        if v is None:
            continue
        if current is None:
            current = v
            start_t = times[i]
        elif v != current:
            segs.append({"t1": start_t, "t2": times[i], "price": current, "kind": kind})
            current = v
            start_t = times[i]

    if current is not None and start_t is not None:
        segs.append({"t1": start_t, "t2": times[-1], "price": current, "kind": kind})

    # son segmentler
    return segs[-max_segments:]

def compute_break_markers(candles, opens, highs, lows, closes, sup_ff, res_ff, osc, vol_thresh):
    """
    LuxAlgo Pine:
    plotshape(toggleBreaks and crossunder(close,lowUsePivot) and not (open - close < high - open) and osc > volumeThresh, text='B', red labeldown abovebar)
    plotshape(toggleBreaks and crossover(close,highUsePivot ) and not(open - low > close - open) and osc > volumeThresh, text='B', green labelup belowbar)

    Bull Wick:
    crossover(close,highUsePivot) and open - low > close - open  => green labelup belowbar text 'Bull Wick'
    Bear Wick:
    crossunder(close,lowUsePivot) and open - close < high - open => red labeldown abovebar text 'Bear Wick'
    """
    markers = []
    for i in range(1, len(candles)):
        t = candles[i]["time"]

        prev_close = closes[i - 1]
        close = closes[i]
        prev_sup = sup_ff[i - 1]
        sup = sup_ff[i]
        prev_res = res_ff[i - 1]
        res = res_ff[i]

        o = opens[i]
        h = highs[i]
        l = lows[i]

        # osc filtresi
        ok_vol = osc[i] > vol_thresh

        # Break down
        cond_cross_dn = crossunder(prev_close, close, prev_sup, sup)
        cond_not_wick = not ((o - close) < (h - o))  # not(open-close < high-open)
        if cond_cross_dn and cond_not_wick and ok_vol:
            markers.append({
                "time": t,
                "position": "aboveBar",
                "color": "red",
                "text": "B",
                "shape": "arrowDown"
            })

        # Break up
        cond_cross_up = crossover(prev_close, close, prev_res, res)
        cond_not_wick2 = not ((o - l) > (close - o))  # not(open-low > close-open)
        if cond_cross_up and cond_not_wick2 and ok_vol:
            markers.append({
                "time": t,
                "position": "belowBar",
                "color": "green",
                "text": "B",
                "shape": "arrowUp"
            })

        # Bull wick
        if cond_cross_up and ((o - l) > (close - o)):
            markers.append({
                "time": t,
                "position": "belowBar",
                "color": "green",
                "text": "Bull Wick",
                "shape": "circle"
            })

        # Bear wick
        if cond_cross_dn and ((o - close) < (h - o)):
            markers.append({
                "time": t,
                "position": "aboveBar",
                "color": "red",
                "text": "Bear Wick",
                "shape": "circle"
            })

    # son 150 marker ile sınırla
    return markers[-150:]

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
            candles, opens, highs, lows, closes, volumes = fetch_daily_ohlc(it["symbol"], bars=900)

            # EMA çizgileri
            e20  = ema_series(closes, 20)
            e50  = ema_series(closes, 50)
            e100 = ema_series(closes, 100)
            e200 = ema_series(closes, 200)

            last_close = closes[-1]
            last_e20  = next(x for x in reversed(e20) if x is not None)
            last_e50  = next(x for x in reversed(e50) if x is not None)
            last_e100 = next(x for x in reversed(e100) if x is not None)
            last_e200 = next(x for x in reversed(e200) if x is not None)
            sheet = classify_sheet(last_e20, last_e50, last_e100, last_e200)

            # MACD hist
            hist = macd_hist(closes)

            # SR (LuxAlgo pivot)
            ph = pivot_high(highs, LEFT_BARS, RIGHT_BARS)
            pl = pivot_low(lows, LEFT_BARS, RIGHT_BARS)

            # LuxAlgo: fixnan(pivot[1]) gibi son pivotu taşır
            # Biz: pivot değerlerini forward-fill ederek seviyeyi sürekli tutuyoruz
            res_ff = forward_fill(ph)
            sup_ff = forward_fill(pl)

            osc = compute_volume_osc(volumes)

            # son N bar
            n = 220
            candles2 = candles[-n:]

            def pack_line(arr):
                out = []
                start = len(candles) - n
                for i in range(start, len(candles)):
                    if i < 0:
                        continue
                    v = arr[i]
                    if v is None:
                        continue
                    out.append({"time": candles[i]["time"], "value": v})
                return out

            # SR segmentleri (son 25'er tane)
            # segmentleri de son n bar'a kırp (t1/t2 clamp)
            seg_res = sr_segments(candles, res_ff, "resistance", max_segments=30)
            seg_sup = sr_segments(candles, sup_ff, "support", max_segments=30)

            # segmentleri son n bar içine alın
            min_t = candles2[0]["time"]
            max_t = candles2[-1]["time"]
            def clamp_segs(segs):
                out = []
                for s in segs:
                    t1 = max(s["t1"], min_t)
                    t2 = min(s["t2"], max_t)
                    if t2 > t1:
                        out.append({"t1": t1, "t2": t2, "price": s["price"], "kind": s["kind"]})
                return out

            seg_res2 = clamp_segs(seg_res)
            seg_sup2 = clamp_segs(seg_sup)

            markers = compute_break_markers(
                candles[-n:],  # sadece son n
                opens[-n:], highs[-n:], lows[-n:], closes[-n:],
                sup_ff[-n:], res_ff[-n:],
                osc[-n:], VOLUME_THRESH
            )

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

                # LuxAlgo SR
                "sr": {
                    "leftBars": LEFT_BARS,
                    "rightBars": RIGHT_BARS,
                    "volumeThresh": VOLUME_THRESH,
                    "segments": seg_res2 + seg_sup2,  # birlikte
                    "markers": markers
                }
            })
        except Exception as e:
            row["ok"] = False
            row["error"] = str(e)

        items.append(row)

    priority = {"LONG": 0, "SHORT": 1, "NONE": 2}
    items.sort(key=lambda x: priority.get(x.get("sheet", "NONE"), 9))
    return {"page": page, "updated_utc": now, "items": items}
