"""
core.py
Golden Gate EMA Scanner - core logic (shared by CLI script and web app)
"""

import io
import time
import urllib.parse
import warnings
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd
import requests
import yfinance as yf

# ============================== CONFIG ======================================

EMA_PERIODS = [20, 50, 100, 200]

EMA_CONVERGENCE_PCT = 1.0      # all 4 EMAs must be within this % of each other
PRICE_TO_EMA_PCT = 1.0         # price must be within this % of the EMA cluster (avg of 4 EMAs)

BREAKOUT_LEVELS = [5, 10, 15, 20]
GG_LOOKBACK_BARS = 50

VOLUME_AVG_PERIOD = 20
VOLUME_SPIKE_MULTIPLIER = 1.5

MAX_WORKERS = 5                # parallel yfinance downloads (raise carefully - Yahoo rate-limits)

NSE_ARCHIVE = "https://nsearchives.nseindia.com/content/indices/"

# ------------------------------ NSE indices ----------------------------------
# display name -> (archive CSV file name, NSE live-API index name)
# The archive file names follow NSE's convention; if one ever changes, the
# scanner automatically falls back to NSE's live JSON API using the API name.
_NSE = {
    "🇮🇳 NSE · Broad Market": {
        "NIFTY 50": ("ind_nifty50list.csv", "NIFTY 50"),
        "NIFTY NEXT 50": ("ind_niftynext50list.csv", "NIFTY NEXT 50"),
        "NIFTY 100": ("ind_nifty100list.csv", "NIFTY 100"),
        "NIFTY 200": ("ind_nifty200list.csv", "NIFTY 200"),
        "NIFTY 500": ("ind_nifty500list.csv", "NIFTY 500"),
        "NIFTY TOTAL MARKET": ("ind_niftytotalmarket_list.csv", "NIFTY TOTAL MARKET"),
        "NIFTY LARGEMIDCAP 250": ("ind_niftylargemidcap250list.csv", "NIFTY LARGEMIDCAP 250"),
        "NIFTY MIDCAP 50": ("ind_niftymidcap50list.csv", "NIFTY MIDCAP 50"),
        "NIFTY MIDCAP 100": ("ind_niftymidcap100list.csv", "NIFTY MIDCAP 100"),
        "NIFTY MIDCAP 150": ("ind_niftymidcap150list.csv", "NIFTY MIDCAP 150"),
        "NIFTY MIDSMALLCAP 400": ("ind_niftymidsmallcap400list.csv", "NIFTY MIDSMALLCAP 400"),
        "NIFTY SMALLCAP 50": ("ind_niftysmallcap50list.csv", "NIFTY SMALLCAP 50"),
        "NIFTY SMALLCAP 100": ("ind_niftysmallcap100list.csv", "NIFTY SMALLCAP 100"),
        "NIFTY SMALLCAP 250": ("ind_niftysmallcap250list.csv", "NIFTY SMALLCAP 250"),
        "NIFTY MICROCAP 250": ("ind_niftymicrocap250_list.csv", "NIFTY MICROCAP 250"),
    },
    "🇮🇳 NSE · Sectoral": {
        "NIFTY BANK": ("ind_niftybanklist.csv", "NIFTY BANK"),
        "NIFTY PRIVATE BANK": ("ind_niftyprivatebanklist.csv", "NIFTY PRIVATE BANK"),
        "NIFTY PSU BANK": ("ind_niftypsubanklist.csv", "NIFTY PSU BANK"),
        "NIFTY FINANCIAL SERVICES": ("ind_niftyfinancelist.csv", "NIFTY FINANCIAL SERVICES"),
        "NIFTY IT": ("ind_niftyitlist.csv", "NIFTY IT"),
        "NIFTY FMCG": ("ind_niftyfmcglist.csv", "NIFTY FMCG"),
        "NIFTY PHARMA": ("ind_niftypharmalist.csv", "NIFTY PHARMA"),
        "NIFTY HEALTHCARE": ("ind_niftyhealthcarelist.csv", "NIFTY HEALTHCARE INDEX"),
        "NIFTY AUTO": ("ind_niftyautolist.csv", "NIFTY AUTO"),
        "NIFTY METAL": ("ind_niftymetallist.csv", "NIFTY METAL"),
        "NIFTY ENERGY": ("ind_niftyenergylist.csv", "NIFTY ENERGY"),
        "NIFTY OIL & GAS": ("ind_niftyoilgaslist.csv", "NIFTY OIL & GAS"),
        "NIFTY REALTY": ("ind_niftyrealtylist.csv", "NIFTY REALTY"),
        "NIFTY MEDIA": ("ind_niftymedialist.csv", "NIFTY MEDIA"),
        "NIFTY CONSUMER DURABLES": ("ind_niftyconsumerdurableslist.csv", "NIFTY CONSUMER DURABLES"),
    },
    "🇮🇳 NSE · Thematic": {
        "NIFTY INFRASTRUCTURE": ("ind_niftyinfralist.csv", "NIFTY INFRASTRUCTURE"),
        "NIFTY COMMODITIES": ("ind_niftycommoditieslist.csv", "NIFTY COMMODITIES"),
        "NIFTY INDIA CONSUMPTION": ("ind_niftyconsumptionlist.csv", "NIFTY INDIA CONSUMPTION"),
        "NIFTY MNC": ("ind_niftymnclist.csv", "NIFTY MNC"),
        "NIFTY PSE": ("ind_niftypselist.csv", "NIFTY PSE"),
        "NIFTY SERVICES SECTOR": ("ind_niftyservicelist.csv", "NIFTY SERVICES SECTOR"),
    },
}

# ------------------------------ BSE ------------------------------------------
# BSE does not publish a stable machine-readable constituent file, so BSE
# indices here are bundled snapshots (Yahoo tickers end in .BO). For other BSE
# indices, use the CSV upload in the sidebar with the "BSE" exchange option.
BSE_SENSEX_30 = [
    "ADANIPORTS", "ASIANPAINT", "AXISBANK", "BAJFINANCE", "BAJAJFINSV",
    "BEL", "BHARTIARTL", "ETERNAL", "HCLTECH", "HDFCBANK",
    "HINDUNILVR", "ICICIBANK", "INFY", "ITC", "KOTAKBANK",
    "LT", "M&M", "MARUTI", "NTPC", "POWERGRID",
    "RELIANCE", "SBIN", "SUNPHARMA", "TATAMOTORS", "TATASTEEL",
    "TCS", "TECHM", "TITAN", "TRENT", "ULTRACEMCO",
]

# ------------------------------ US indices -----------------------------------
# Constituents are read live from Wikipedia's index pages (no API key needed).
_US = {
    "S&P 500": "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
    "NASDAQ 100": "https://en.wikipedia.org/wiki/Nasdaq-100",
    "DOW JONES 30": "https://en.wikipedia.org/wiki/Dow_Jones_Industrial_Average",
    "S&P MIDCAP 400": "https://en.wikipedia.org/wiki/List_of_S%26P_400_companies",
    "S&P SMALLCAP 600": "https://en.wikipedia.org/wiki/List_of_S%26P_600_companies",
}
US_FALLBACK = {
    "DOW JONES 30": [
        "AAPL", "AMGN", "AMZN", "AXP", "BA", "CAT", "CRM", "CSCO", "CVX", "DIS",
        "GS", "HD", "HON", "IBM", "JNJ", "JPM", "KO", "MCD", "MMM", "MRK",
        "MSFT", "NKE", "NVDA", "PG", "SHW", "TRV", "UNH", "V", "VZ", "WMT",
    ],
}

# ------------------------------ Crypto & commodities -------------------------
_STATIC = {
    "₿ Crypto": {
        "CRYPTO - MAJOR": [
            "BTC-USD", "ETH-USD", "BNB-USD", "SOL-USD", "XRP-USD",
            "ADA-USD", "DOGE-USD", "POL-USD", "DOT-USD", "LTC-USD",
            "AVAX-USD", "LINK-USD", "TRX-USD", "SHIB-USD", "ATOM-USD",
        ],
    },
    "🛢️ Commodities": {
        "COMMODITIES - METALS": ["GC=F", "SI=F", "PL=F", "PA=F", "HG=F"],
        "COMMODITIES - ENERGY": ["CL=F", "BZ=F", "NG=F", "RB=F", "HO=F"],
        "COMMODITIES - AGRICULTURE": ["ZC=F", "ZW=F", "ZS=F", "KC=F", "SB=F", "CT=F", "CC=F"],
    },
}

STATIC_TICKER_LABELS = {
    "BTC-USD": "BITCOIN", "ETH-USD": "ETHEREUM", "BNB-USD": "BNB", "SOL-USD": "SOLANA",
    "XRP-USD": "XRP", "ADA-USD": "CARDANO", "DOGE-USD": "DOGECOIN", "POL-USD": "POLYGON",
    "DOT-USD": "POLKADOT", "LTC-USD": "LITECOIN", "AVAX-USD": "AVALANCHE", "LINK-USD": "CHAINLINK",
    "TRX-USD": "TRON", "SHIB-USD": "SHIBA INU", "ATOM-USD": "COSMOS",
    "GC=F": "GOLD", "SI=F": "SILVER", "PL=F": "PLATINUM", "PA=F": "PALLADIUM", "HG=F": "COPPER",
    "CL=F": "CRUDE OIL (WTI)", "BZ=F": "BRENT CRUDE", "NG=F": "NATURAL GAS",
    "RB=F": "GASOLINE", "HO=F": "HEATING OIL",
    "ZC=F": "CORN", "ZW=F": "WHEAT", "ZS=F": "SOYBEANS", "KC=F": "COFFEE",
    "SB=F": "SUGAR", "CT=F": "COTTON", "CC=F": "COCOA",
}

# ------------------------------ Market registry ------------------------------
MARKETS = {}          # name -> spec dict
MARKET_GROUPS = {}    # group label -> [market names]

for _group, _items in _NSE.items():
    MARKET_GROUPS[_group] = list(_items)
    for _name, (_file, _api) in _items.items():
        MARKETS[_name] = {"kind": "nse", "url": NSE_ARCHIVE + _file, "api": _api}

MARKET_GROUPS["🇮🇳 BSE"] = ["BSE SENSEX 30"]
MARKETS["BSE SENSEX 30"] = {"kind": "bse", "symbols": BSE_SENSEX_30}

MARKET_GROUPS["🇺🇸 US Indices"] = list(_US)
for _name, _url in _US.items():
    MARKETS[_name] = {"kind": "us", "url": _url}

for _group, _items in _STATIC.items():
    MARKET_GROUPS[_group] = list(_items)
    for _name, _tickers in _items.items():
        MARKETS[_name] = {"kind": "static", "tickers": _tickers}

ALL_MARKETS = list(MARKETS)

FALLBACK_INDEX_LISTS = {
    "NIFTY 50": [
        "ADANIENT", "ADANIPORTS", "APOLLOHOSP", "ASIANPAINT", "AXISBANK",
        "BAJAJ-AUTO", "BAJFINANCE", "BAJAJFINSV", "BEL", "BHARTIARTL",
        "CIPLA", "COALINDIA", "DIVISLAB", "DRREDDY", "EICHERMOT",
        "GRASIM", "HCLTECH", "HDFCBANK", "HDFCLIFE", "HEROMOTOCO",
        "HINDALCO", "HINDUNILVR", "ICICIBANK", "ITC", "INDUSINDBK",
        "INFY", "JSWSTEEL", "KOTAKBANK", "LT", "LTIM",
        "M&M", "MARUTI", "NESTLEIND", "NTPC", "ONGC",
        "POWERGRID", "RELIANCE", "SBILIFE", "SHRIRAMFIN", "SBIN",
        "SUNPHARMA", "TATACONSUM", "TATAMOTORS", "TATASTEEL", "TCS",
        "TECHM", "TITAN", "TRENT", "ULTRACEMCO", "WIPRO",
    ],
}
FALLBACK_LIST_NOTE = (
    "Bundled fallback list used because the live fetch failed. This is a "
    "point-in-time snapshot and may not reflect the latest index rebalancing - "
    "verify against the exchange website if precision matters."
)

# label -> (yfinance interval, yfinance period, resample_to)
TIMEFRAME_MAP = {
    "5 minute": ("5m", "60d", None),
    "15 minute": ("15m", "60d", None),
    "30 minute": ("30m", "60d", None),
    "1 hour": ("60m", "730d", None),
    "4 hour": ("60m", "730d", "4h"),
    "Daily": ("1d", "5y", None),
    "Weekly": ("1wk", "10y", None),
}
INTRADAY_INTERVALS = ("5m", "15m", "30m", "60m")

# =============================================================================

NSE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/market-data/live-equity-market",
    "Connection": "keep-alive",
}
WIKI_HEADERS = {"User-Agent": NSE_HEADERS["User-Agent"]}


def is_nse_market(name):
    return MARKETS.get(name, {}).get("kind") == "nse"


# ----------------------------- constituent fetchers --------------------------

def get_index_symbols(url, max_retries=3):
    """Official NSE archive CSV -> list of 'SYMBOL.NS'."""
    last_error = None
    for attempt in range(1, max_retries + 1):
        try:
            session = requests.Session()
            session.headers.update(NSE_HEADERS)
            session.get("https://www.nseindia.com", timeout=10)
            time.sleep(1)
            resp = session.get(url, timeout=15)
            resp.raise_for_status()
            df = pd.read_csv(io.StringIO(resp.text))
            symbols = df["Symbol"].astype(str).str.strip().tolist()
            return [f"{s}.NS" for s in symbols]
        except Exception as e:
            last_error = e
            if attempt < max_retries:
                time.sleep(2 * attempt)
    raise RuntimeError(str(last_error))


def get_index_symbols_via_api(index_api_name, max_retries=2):
    """NSE's live JSON API (fallback when the archive CSV is blocked/moved)."""
    url = f"https://www.nseindia.com/api/equity-stockIndices?index={urllib.parse.quote(index_api_name)}"
    last_error = None
    for attempt in range(1, max_retries + 1):
        try:
            session = requests.Session()
            session.headers.update(NSE_HEADERS)
            session.get("https://www.nseindia.com", timeout=10)
            time.sleep(1)
            resp = session.get(url, timeout=15)
            resp.raise_for_status()
            rows = resp.json().get("data", [])
            symbols = [
                str(r["symbol"]).strip() for r in rows
                if "symbol" in r and str(r["symbol"]).strip().upper() != index_api_name.upper()
            ]
            if not symbols:
                raise RuntimeError("API returned no constituent symbols")
            return [f"{s}.NS" for s in symbols]
        except Exception as e:
            last_error = e
            if attempt < max_retries:
                time.sleep(2 * attempt)
    raise RuntimeError(str(last_error))


def fetch_nse_index_symbols(market_name, spec):
    try:
        return get_index_symbols(spec["url"])
    except Exception as csv_error:
        try:
            return get_index_symbols_via_api(spec["api"])
        except Exception as api_error:
            raise RuntimeError(
                f"Could not fetch {market_name} from NSE via the archive CSV "
                f"({csv_error}) or the live API ({api_error}). NSE often blocks cloud "
                "servers; it usually works from a home connection in India. "
                "You can also upload a stock list in the sidebar."
            )


def fetch_us_symbols(url):
    """Reads a Wikipedia index page and returns the constituent tickers
    (Yahoo style: BRK.B -> BRK-B)."""
    resp = requests.get(url, headers=WIKI_HEADERS, timeout=20)
    resp.raise_for_status()
    for table in pd.read_html(io.StringIO(resp.text)):
        if isinstance(table.columns, pd.MultiIndex):
            table.columns = [c[-1] for c in table.columns]
        for col in ("Symbol", "Ticker"):
            if col in table.columns and len(table) >= 20:
                s = (
                    table[col].astype(str)
                    .str.replace(r"\[.*?\]", "", regex=True)
                    .str.split(":").str[-1]
                    .str.strip()
                    .str.replace(".", "-", regex=False)
                )
                symbols = [x for x in s if x and x.lower() != "nan"]
                if len(symbols) >= 20:
                    return symbols
    raise RuntimeError("No constituent table found on the page")


def get_tickers_for_market(market_name, exchange="NSE"):
    """Returns a yfinance-ready ticker list. For NSE indices, exchange='BSE'
    maps every constituent to its BSE listing (.BO) instead of NSE (.NS)."""
    spec = MARKETS.get(market_name)
    if spec is None:
        raise ValueError(f"Unknown market: {market_name}")
    kind = spec["kind"]

    if kind == "static":
        return list(spec["tickers"])

    if kind == "bse":
        return [f"{s}.BO" for s in spec["symbols"]]

    if kind == "us":
        try:
            return fetch_us_symbols(spec["url"])
        except Exception as e:
            if market_name in US_FALLBACK:
                warnings.warn(f"{FALLBACK_LIST_NOTE} (Live fetch error: {e})")
                return list(US_FALLBACK[market_name])
            raise RuntimeError(
                f"Could not read the {market_name} constituent list ({e}). "
                "Upload a stock list in the sidebar instead."
            )

    # NSE
    try:
        tickers = fetch_nse_index_symbols(market_name, spec)
    except Exception as e:
        if market_name in FALLBACK_INDEX_LISTS:
            warnings.warn(f"{FALLBACK_LIST_NOTE} (Live fetch error: {e})")
            tickers = [f"{s}.NS" for s in FALLBACK_INDEX_LISTS[market_name]]
        else:
            raise
    if exchange == "BSE":
        tickers = [t[:-3] + ".BO" for t in tickers]
    return tickers


def build_custom_tickers(symbols, suffix):
    """suffix: '.NS', '.BO' or '' (global tickers used as typed)."""
    out = []
    for s in symbols:
        s = str(s).strip().upper()
        if not s or s == "NAN":
            continue
        out.append(s if (not suffix or s.endswith(suffix) or "." in s or "=" in s or "-" in s) else s + suffix)
    return list(dict.fromkeys(out))


def display_symbol(ticker):
    if ticker in STATIC_TICKER_LABELS:
        return STATIC_TICKER_LABELS[ticker]
    for suf in (".NS", ".BO"):
        if ticker.endswith(suf):
            return ticker[: -len(suf)]
    return ticker


# ----------------------------- price data & indicators -----------------------

def fetch_price_data(ticker, interval, period, resample_to=None):
    data = yf.Ticker(ticker).history(period=period, interval=interval, auto_adjust=True)
    if data is None or data.empty:
        return None
    data = data.dropna(how="all")

    if resample_to:
        agg = {"Open": "first", "High": "max", "Low": "min", "Close": "last"}
        if "Volume" in data.columns:
            agg["Volume"] = "sum"
        data = data.resample(resample_to, label="right", closed="right").agg(agg)
        data = data.dropna(subset=["Open", "High", "Low", "Close"])
    return data


def compute_emas(df):
    for p in EMA_PERIODS:
        df[f"EMA{p}"] = df["Close"].ewm(span=p, adjust=False).mean()
    df["EMA_avg"] = df[[f"EMA{p}" for p in EMA_PERIODS]].mean(axis=1)
    return df


def get_chart_data(ticker, timeframe_label, bars=250):
    """Price + EMA data for the chart explorer."""
    interval, period, resample_to = TIMEFRAME_MAP[timeframe_label]
    df = fetch_price_data(ticker, interval, period, resample_to=resample_to)
    if df is None or df.empty:
        return None
    return compute_emas(df).tail(bars)


def golden_gate_metrics(row):
    emas = [row[f"EMA{p}"] for p in EMA_PERIODS]
    if any(pd.isna(e) for e in emas):
        return None, None
    max_e, min_e = max(emas), min(emas)
    if min_e <= 0:
        return None, None
    spread = (max_e - min_e) / min_e * 100
    cluster = sum(emas) / len(emas)
    return spread, abs(row["Close"] - cluster) / cluster * 100


def is_golden_gate(row, ema_convergence_pct=EMA_CONVERGENCE_PCT, price_to_ema_pct=PRICE_TO_EMA_PCT):
    spread, dev = golden_gate_metrics(row)
    if spread is None:
        return False
    return spread <= ema_convergence_pct and dev <= price_to_ema_pct


def compute_trend(latest):
    e20, e50, e100, e200 = latest["EMA20"], latest["EMA50"], latest["EMA100"], latest["EMA200"]
    if any(pd.isna(v) for v in [e20, e50, e100, e200]):
        return "Unknown"
    if e20 > e50 > e100 > e200:
        return "Uptrend"
    if e20 < e50 < e100 < e200:
        return "Downtrend"
    return "Mixed"


def compute_price_action(df):
    if len(df) < 2:
        return "Unknown", None
    latest = df.iloc[-1]
    prev_close = df.iloc[-2]["Close"]
    direction = "Bullish" if latest["Close"] >= latest["Open"] else "Bearish"
    pct = (latest["Close"] - prev_close) / prev_close * 100 if prev_close else None
    return direction, round(pct, 2) if pct is not None else None


def compute_volume_metrics(df, volume_avg_period=VOLUME_AVG_PERIOD, spike_multiplier=VOLUME_SPIKE_MULTIPLIER):
    if "Volume" not in df.columns or df["Volume"].isna().all():
        return None, None, False
    latest_volume = df["Volume"].iloc[-1]
    avg_volume = df["Volume"].tail(volume_avg_period + 1).iloc[:-1].mean()
    if pd.isna(avg_volume) or avg_volume <= 0:
        return (float(latest_volume) if not pd.isna(latest_volume) else None), None, False
    return float(latest_volume), float(avg_volume), bool(latest_volume >= spike_multiplier * avg_volume)


def analyze_stock(ticker, interval, period, resample_to=None, ema_convergence_pct=EMA_CONVERGENCE_PCT,
                  price_to_ema_pct=PRICE_TO_EMA_PCT, breakout_levels=BREAKOUT_LEVELS,
                  gg_lookback_bars=GG_LOOKBACK_BARS, volume_spike_multiplier=VOLUME_SPIKE_MULTIPLIER):
    df = fetch_price_data(ticker, interval, period, resample_to=resample_to)
    if df is None or len(df) < max(EMA_PERIODS) + 5:
        return None

    df = compute_emas(df)

    # Vectorised Golden Gate test over every bar
    emas = df[[f"EMA{p}" for p in EMA_PERIODS]]
    mx, mn = emas.max(axis=1), emas.min(axis=1)
    df["EMA_spread"] = (mx - mn) / mn.where(mn > 0) * 100
    df["Price_dev"] = (df["Close"] - df["EMA_avg"]).abs() / df["EMA_avg"].where(df["EMA_avg"] > 0) * 100
    df["GG"] = (df["EMA_spread"] <= ema_convergence_pct) & (df["Price_dev"] <= price_to_ema_pct)

    latest = df.iloc[-1]
    gg_formed_now = bool(latest["GG"])
    latest_spread = None if pd.isna(latest["EMA_spread"]) else float(latest["EMA_spread"])
    latest_dev = None if pd.isna(latest["Price_dev"]) else float(latest["Price_dev"])

    event_formed, event_pct, zone_date, move_pct = False, None, None, None

    window = df.iloc[-gg_lookback_bars:-1] if gg_lookback_bars > 1 else df.iloc[0:0]
    prior = window[window["GG"]]
    if not prior.empty:
        last_gg_idx = prior.index[-1]
        zone_price = df.loc[last_gg_idx, "EMA_avg"]
        pct_move = (latest["Close"] - zone_price) / zone_price * 100
        move_pct = round(float(pct_move), 2)
        achieved = [lvl for lvl in breakout_levels if pct_move >= lvl]
        if achieved:
            event_formed, event_pct, zone_date = True, max(achieved), last_gg_idx

    trend = compute_trend(latest)
    price_action, price_action_pct = compute_price_action(df)
    latest_volume, avg_volume, volume_spike = compute_volume_metrics(
        df, spike_multiplier=volume_spike_multiplier
    )
    avg_turnover = (avg_volume * float(latest["Close"])) if avg_volume else None

    return {
        "Symbol": display_symbol(ticker),
        "Ticker": ticker,
        "Last Close": round(float(latest["Close"]), 2),
        "EMA20": round(float(latest["EMA20"]), 2),
        "EMA50": round(float(latest["EMA50"]), 2),
        "EMA100": round(float(latest["EMA100"]), 2),
        "EMA200": round(float(latest["EMA200"]), 2),
        "EMA Spread %": round(latest_spread, 2) if latest_spread is not None else None,
        "Price Dev %": round(latest_dev, 2) if latest_dev is not None else None,
        "Golden Gate": "Formed" if gg_formed_now else "Not Formed",
        "EMA Breakout Signal": f"Formed (+{event_pct}% breakout)" if event_formed else "Not Formed",
        "Move from last GG zone %": move_pct if move_pct is not None else "-",
        "GG Zone Date": zone_date.strftime("%Y-%m-%d %H:%M") if zone_date is not None else "-",
        "Trend": trend,
        "Price Action": f"{price_action} ({price_action_pct:+.2f}%)" if price_action_pct is not None else price_action,
        "Volume": round(latest_volume) if latest_volume is not None else "-",
        "Avg Volume": round(avg_volume) if avg_volume is not None else "-",
        "Volume Spike": "Yes" if volume_spike else "No",
        "_avg_turnover": avg_turnover,
    }


def _safe_analyze(ticker, tf_label, params):
    interval, period, resample_to = TIMEFRAME_MAP[tf_label]
    try:
        return analyze_stock(ticker, interval, period, resample_to=resample_to, **params)
    except Exception:
        return None


def add_liquidity_ranking(df):
    """High/Medium/Low liquidity, ranked relative to the other symbols in this scan."""
    if df.empty or "_avg_turnover" not in df.columns:
        df["Liquidity"] = "-"
        return df

    valid = df["_avg_turnover"].notna()
    if valid.sum() < 3:
        df["Liquidity"] = df["_avg_turnover"].apply(lambda v: "-" if pd.isna(v) else "N/A (too few to rank)")
        return df.drop(columns=["_avg_turnover"])

    ranks = df.loc[valid, "_avg_turnover"].rank(pct=True)
    df.loc[valid, "Liquidity"] = ranks.apply(lambda p: "High" if p >= 0.67 else "Medium" if p >= 0.33 else "Low")
    df.loc[~valid, "Liquidity"] = "-"
    return df.drop(columns=["_avg_turnover"])


def _resolve_tickers(index_name, custom_tickers, exchange, custom_suffix):
    if custom_tickers:
        return build_custom_tickers(custom_tickers, custom_suffix)
    return get_tickers_for_market(index_name, exchange=exchange)


def run_scan(index_name, timeframe_label, progress_callback=None, custom_tickers=None,
             ema_convergence_pct=EMA_CONVERGENCE_PCT, price_to_ema_pct=PRICE_TO_EMA_PCT,
             breakout_levels=BREAKOUT_LEVELS, gg_lookback_bars=GG_LOOKBACK_BARS,
             volume_spike_multiplier=VOLUME_SPIKE_MULTIPLIER,
             exchange="NSE", custom_suffix=".NS", max_workers=MAX_WORKERS):
    """Single-timeframe scan, downloaded in parallel.
    progress_callback(i, total, symbol, result_or_None) runs on the calling thread."""
    tickers = _resolve_tickers(index_name, custom_tickers, exchange, custom_suffix)
    params = dict(
        ema_convergence_pct=ema_convergence_pct, price_to_ema_pct=price_to_ema_pct,
        breakout_levels=breakout_levels, gg_lookback_bars=gg_lookback_bars,
        volume_spike_multiplier=volume_spike_multiplier,
    )

    results, done = [], 0
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = {ex.submit(_safe_analyze, t, timeframe_label, params): t for t in tickers}
        for fut in as_completed(futures):
            res = fut.result()
            done += 1
            if res:
                results.append(res)
            if progress_callback:
                progress_callback(done, len(tickers), display_symbol(futures[fut]), res)

    if not results:
        return pd.DataFrame()

    out_df = add_liquidity_ranking(pd.DataFrame(results))
    return out_df.sort_values(by=["EMA Spread %"], ascending=True).reset_index(drop=True)


def run_multi_timeframe_scan(index_name, timeframe_labels, progress_callback=None, custom_tickers=None,
                             ema_convergence_pct=EMA_CONVERGENCE_PCT, price_to_ema_pct=PRICE_TO_EMA_PCT,
                             breakout_levels=BREAKOUT_LEVELS, gg_lookback_bars=GG_LOOKBACK_BARS,
                             volume_spike_multiplier=VOLUME_SPIKE_MULTIPLIER,
                             exchange="NSE", custom_suffix=".NS", max_workers=MAX_WORKERS):
    """Scans every stock on 1-3 timeframes; one row per stock, timeframes side by side."""
    if not timeframe_labels:
        raise ValueError("Select at least one timeframe.")

    tickers = _resolve_tickers(index_name, custom_tickers, exchange, custom_suffix)
    params = dict(
        ema_convergence_pct=ema_convergence_pct, price_to_ema_pct=price_to_ema_pct,
        breakout_levels=breakout_levels, gg_lookback_bars=gg_lookback_bars,
        volume_spike_multiplier=volume_spike_multiplier,
    )

    total, done = len(tickers) * len(timeframe_labels), 0
    results = {}
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = {
            ex.submit(_safe_analyze, t, tf, params): (t, tf)
            for t in tickers for tf in timeframe_labels
        }
        for fut in as_completed(futures):
            t, tf = futures[fut]
            results[(t, tf)] = fut.result()
            done += 1
            if progress_callback:
                progress_callback(done, total, display_symbol(t), tf)

    rows = []
    for ticker in tickers:
        row, any_data = {"Symbol": display_symbol(ticker), "Ticker": ticker}, False
        for tf in timeframe_labels:
            res = results.get((ticker, tf))
            if res:
                any_data = True
                row[f"Last Close ({tf})"] = res["Last Close"]
                row[f"EMA Spread % ({tf})"] = res["EMA Spread %"]
                row[f"Golden Gate ({tf})"] = res["Golden Gate"]
                row[f"EMA Breakout Signal ({tf})"] = res["EMA Breakout Signal"]
                row[f"Trend ({tf})"] = res["Trend"]
                row[f"Price Action ({tf})"] = res["Price Action"]
                row[f"Volume Spike ({tf})"] = res["Volume Spike"]
            else:
                row[f"Last Close ({tf})"] = None
                row[f"EMA Spread % ({tf})"] = None
                for k in ("Golden Gate", "EMA Breakout Signal", "Trend", "Price Action", "Volume Spike"):
                    row[f"{k} ({tf})"] = "No Data"
        if any_data:
            rows.append(row)

    if not rows:
        return pd.DataFrame()

    out_df = pd.DataFrame(rows)
    gg_cols = [c for c in out_df.columns if c.startswith("Golden Gate (")]
    ev_cols = [c for c in out_df.columns if c.startswith("EMA Breakout Signal (")]

    out_df["Golden Gate - Any TF"] = out_df[gg_cols].apply(
        lambda r: "Formed" if any(v == "Formed" for v in r) else "Not Formed", axis=1)
    out_df["EMA Breakout Signal - Any TF"] = out_df[ev_cols].apply(
        lambda r: "Formed" if any(str(v).startswith("Formed") for v in r) else "Not Formed", axis=1)

    # "Formed" sorts before "Not Formed" alphabetically, so ascending puts matches first
    out_df = out_df.sort_values(by=["Golden Gate - Any TF", "EMA Breakout Signal - Any TF"],
                                ascending=[True, True])
    return out_df.reset_index(drop=True)
