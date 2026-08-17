"""
core.py
Golden Gate EMA Scanner - core logic (shared by CLI script and web app)
"""

import io
import time
import warnings
from datetime import datetime

import pandas as pd
import requests
import yfinance as yf

# ============================== CONFIG ======================================

EMA_PERIODS = [20, 50, 100, 200]

EMA_CONVERGENCE_PCT = 1.0      # all 4 EMAs must be within this % of each other -> "closer"
PRICE_TO_EMA_PCT = 1.0         # price must be within this % of the EMA cluster (avg of 4 EMAs)

BREAKOUT_LEVELS = [5, 10, 15, 20]   # % breakout buckets to check/report (EMA Breakout Signal)
GG_LOOKBACK_BARS = 50               # how many bars back to look for a prior Golden Gate

VOLUME_AVG_PERIOD = 20              # bars used for the average-volume baseline
VOLUME_SPIKE_MULTIPLIER = 1.5       # latest volume >= this x average -> "Volume Spike"

REQUEST_DELAY_SEC = 0.2

INDEX_URLS = {
    "NIFTY 50": "https://nsearchives.nseindia.com/content/indices/ind_nifty50list.csv",
    "NIFTY 100": "https://nsearchives.nseindia.com/content/indices/ind_nifty100list.csv",
    "NIFTY 200": "https://nsearchives.nseindia.com/content/indices/ind_nifty200list.csv",
    "NIFTY 500": "https://nsearchives.nseindia.com/content/indices/ind_nifty500list.csv",
    "NIFTY MIDCAP 50": "https://nsearchives.nseindia.com/content/indices/ind_niftymidcap50list.csv",
    "NIFTY MIDCAP 100": "https://nsearchives.nseindia.com/content/indices/ind_niftymidcap100list.csv",
    "NIFTY MIDCAP 150": "https://nsearchives.nseindia.com/content/indices/ind_niftymidcap150list.csv",
    "NIFTY SMALLCAP 50": "https://nsearchives.nseindia.com/content/indices/ind_niftysmallcap50list.csv",
    "NIFTY SMALLCAP 100": "https://nsearchives.nseindia.com/content/indices/ind_niftysmallcap100list.csv",
    "NIFTY SMALLCAP 250": "https://nsearchives.nseindia.com/content/indices/ind_niftysmallcap250list.csv",
}

# Non-NSE markets: fixed baskets of instruments (yfinance-ready tickers, used as-is,
# no .NS suffix). Edit these lists to add/remove specific coins or metals.
STATIC_MARKETS = {
    "CRYPTO - MAJOR": [
        "BTC-USD", "ETH-USD", "BNB-USD", "SOL-USD", "XRP-USD",
        "ADA-USD", "DOGE-USD", "MATIC-USD", "DOT-USD", "LTC-USD",
        "AVAX-USD", "LINK-USD", "TRX-USD", "SHIB-USD", "ATOM-USD",
    ],
    "COMMODITIES - METALS": [
        "GC=F",   # Gold futures
        "SI=F",   # Silver futures
        "PL=F",   # Platinum futures
        "PA=F",   # Palladium futures
        "HG=F",   # Copper futures
    ],
}

# Human-readable display names for static market tickers (used instead of the
# raw yfinance symbol in the "Symbol" column)
STATIC_TICKER_LABELS = {
    "BTC-USD": "BITCOIN", "ETH-USD": "ETHEREUM", "BNB-USD": "BNB", "SOL-USD": "SOLANA",
    "XRP-USD": "XRP", "ADA-USD": "CARDANO", "DOGE-USD": "DOGECOIN", "MATIC-USD": "POLYGON",
    "DOT-USD": "POLKADOT", "LTC-USD": "LITECOIN", "AVAX-USD": "AVALANCHE", "LINK-USD": "CHAINLINK",
    "TRX-USD": "TRON", "SHIB-USD": "SHIBA INU", "ATOM-USD": "COSMOS",
    "GC=F": "GOLD", "SI=F": "SILVER", "PL=F": "PLATINUM", "PA=F": "PALLADIUM", "HG=F": "COPPER",
}

ALL_MARKETS = list(INDEX_URLS.keys()) + list(STATIC_MARKETS.keys())

# Bundled fallback constituent lists, used ONLY when the live NSE fetch fails
# (e.g. 403/503 from NSE blocking cloud server IPs). These are point-in-time
# snapshots and WILL drift out of date as NSE periodically rebalances index
# membership - update them occasionally by pasting a fresh list from NSE's
# website (or your own browser download of the CSV) into the file.
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
    "Bundled fallback list used because the live NSE fetch failed (NSE is likely "
    "blocking this server's requests). This is a point-in-time snapshot and may "
    "not reflect the very latest index rebalancing - verify against nseindia.com "
    "if precision matters."
)

# label -> (yfinance interval, yfinance period, resample_to)
# resample_to is None for intervals yfinance provides natively. "4h" isn't a
# native yfinance interval, so we fetch 1-hour bars and resample them ourselves.
TIMEFRAME_MAP = {
    "5 minute": ("5m", "60d", None),
    "15 minute": ("15m", "60d", None),
    "30 minute": ("30m", "60d", None),
    "1 hour": ("60m", "730d", None),
    "4 hour": ("60m", "730d", "4h"),
    "Daily": ("1d", "5y", None),
    "Weekly": ("1wk", "10y", None),
}

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


def get_index_symbols(url, max_retries=3):
    """Download the official NSE constituent list for an index from the
    static archive CSV. Retries with a fresh session/backoff since NSE
    occasionally 403/503s bot-like traffic, especially from cloud/datacenter IPs."""
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


def get_index_symbols_via_api(index_display_name, max_retries=2):
    """Fallback fetch method: NSE's own live JSON API (the one their website
    uses internally), which sometimes gets through even when the static
    archive CSV endpoint is blocked. index_display_name must match NSE's
    naming exactly, e.g. 'NIFTY 500', 'NIFTY MIDCAP 100'."""
    import urllib.parse
    url = f"https://www.nseindia.com/api/equity-stockIndices?index={urllib.parse.quote(index_display_name)}"
    last_error = None
    for attempt in range(1, max_retries + 1):
        try:
            session = requests.Session()
            session.headers.update(NSE_HEADERS)
            session.get("https://www.nseindia.com", timeout=10)
            time.sleep(1)
            resp = session.get(url, timeout=15)
            resp.raise_for_status()
            payload = resp.json()
            rows = payload.get("data", [])
            symbols = [
                str(row["symbol"]).strip() for row in rows
                if "symbol" in row and str(row["symbol"]).strip().upper() != index_display_name.upper()
            ]
            if not symbols:
                raise RuntimeError("API returned no constituent symbols")
            return [f"{s}.NS" for s in symbols]
        except Exception as e:
            last_error = e
            if attempt < max_retries:
                time.sleep(2 * attempt)
    raise RuntimeError(str(last_error))


def fetch_nse_index_symbols(market_name, url, max_retries=3):
    """Tries the static archive CSV first, then the live JSON API, before
    giving up. Returns tickers, or raises RuntimeError with both errors
    described if neither source works."""
    try:
        return get_index_symbols(url, max_retries=max_retries)
    except Exception as csv_error:
        try:
            return get_index_symbols_via_api(market_name)
        except Exception as api_error:
            raise RuntimeError(
                f"Could not fetch {market_name} list from NSE via either the archive CSV "
                f"({csv_error}) or the live API ({api_error}). NSE is likely blocking this "
                "server's requests entirely. This is more likely on a cloud host (e.g. Streamlit "
                "Cloud) than on your own PC in India. See the README for the upload fallback option."
            )


def get_tickers_for_market(market_name):
    """Returns a yfinance-ready ticker list for either an NSE index (fetched
    live) or a static market basket (crypto/commodities, fixed list).
    If the live NSE fetch fails and a bundled fallback list exists for this
    market, uses that instead (and raises a warning so the UI can inform the
    user the list may be slightly stale)."""
    if market_name in STATIC_MARKETS:
        return list(STATIC_MARKETS[market_name])
    if market_name in INDEX_URLS:
        try:
            return fetch_nse_index_symbols(market_name, INDEX_URLS[market_name])
        except Exception as e:
            if market_name in FALLBACK_INDEX_LISTS:
                warnings.warn(f"{FALLBACK_LIST_NOTE} (Live fetch error: {e})")
                return [f"{s}.NS" for s in FALLBACK_INDEX_LISTS[market_name]]
            raise
    raise ValueError(f"Unknown market: {market_name}")


def display_symbol(ticker):
    """Converts a yfinance ticker to a friendly display symbol."""
    if ticker in STATIC_TICKER_LABELS:
        return STATIC_TICKER_LABELS[ticker]
    return ticker.replace(".NS", "")


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


def golden_gate_metrics(row):
    """Returns (ema_spread_pct, price_dev_pct) for a row - always computed,
    regardless of thresholds, so callers can see how close a stock is even
    when it doesn't meet the strict Golden Gate condition."""
    emas = [row[f"EMA{p}"] for p in EMA_PERIODS]
    if any(pd.isna(e) for e in emas):
        return None, None
    max_e, min_e = max(emas), min(emas)
    if min_e <= 0:
        return None, None
    ema_spread_pct = (max_e - min_e) / min_e * 100
    cluster_price = sum(emas) / len(emas)
    price = row["Close"]
    price_dev_pct = abs(price - cluster_price) / cluster_price * 100
    return ema_spread_pct, price_dev_pct


def is_golden_gate(row, ema_convergence_pct=EMA_CONVERGENCE_PCT, price_to_ema_pct=PRICE_TO_EMA_PCT):
    ema_spread_pct, price_dev_pct = golden_gate_metrics(row)
    if ema_spread_pct is None:
        return False
    if ema_spread_pct > ema_convergence_pct:
        return False
    return price_dev_pct <= price_to_ema_pct


def compute_trend(latest):
    """Trend from EMA stacking order: Uptrend if 20>50>100>200, Downtrend if
    reversed, Mixed otherwise."""
    e20, e50, e100, e200 = latest["EMA20"], latest["EMA50"], latest["EMA100"], latest["EMA200"]
    if any(pd.isna(v) for v in [e20, e50, e100, e200]):
        return "Unknown"
    if e20 > e50 > e100 > e200:
        return "Uptrend"
    if e20 < e50 < e100 < e200:
        return "Downtrend"
    return "Mixed"


def compute_price_action(df):
    """Bullish/Bearish tag for the latest candle plus % change vs the
    previous candle's close."""
    if len(df) < 2:
        return "Unknown", None
    latest = df.iloc[-1]
    prev_close = df.iloc[-2]["Close"]
    direction = "Bullish" if latest["Close"] >= latest["Open"] else "Bearish"
    pct_change = (latest["Close"] - prev_close) / prev_close * 100 if prev_close else None
    return direction, round(pct_change, 2) if pct_change is not None else None


def compute_volume_metrics(df, volume_avg_period=VOLUME_AVG_PERIOD, spike_multiplier=VOLUME_SPIKE_MULTIPLIER):
    """Returns (latest_volume, avg_volume, is_spike). None values if volume
    data isn't available (some yfinance feeds omit it, e.g. certain futures)."""
    if "Volume" not in df.columns or df["Volume"].isna().all():
        return None, None, False
    latest_volume = df["Volume"].iloc[-1]
    avg_volume = df["Volume"].tail(volume_avg_period + 1).iloc[:-1].mean()  # excludes latest bar
    if pd.isna(avg_volume) or avg_volume <= 0:
        return (float(latest_volume) if not pd.isna(latest_volume) else None), None, False
    is_spike = latest_volume >= spike_multiplier * avg_volume
    return float(latest_volume), float(avg_volume), bool(is_spike)


def analyze_stock(ticker, interval, period, resample_to=None, ema_convergence_pct=EMA_CONVERGENCE_PCT,
                   price_to_ema_pct=PRICE_TO_EMA_PCT, breakout_levels=BREAKOUT_LEVELS,
                   gg_lookback_bars=GG_LOOKBACK_BARS, volume_spike_multiplier=VOLUME_SPIKE_MULTIPLIER):
    df = fetch_price_data(ticker, interval, period, resample_to=resample_to)
    if df is None or len(df) < max(EMA_PERIODS) + 5:
        return None

    df = compute_emas(df)
    df["GG"] = df.apply(lambda r: is_golden_gate(r, ema_convergence_pct, price_to_ema_pct), axis=1)

    latest = df.iloc[-1]
    gg_formed_now = bool(latest["GG"])
    latest_ema_spread, latest_price_dev = golden_gate_metrics(latest)

    gg_indices = df.index[df["GG"]].tolist()
    prior_gg_indices = [i for i in gg_indices if i != df.index[-1]]

    event_formed = False
    event_pct = None
    zone_date = None
    closest_move_pct = None

    if prior_gg_indices:
        recent_prior = [i for i in prior_gg_indices if i in df.index[-gg_lookback_bars:]]
        if recent_prior:
            last_gg_idx = recent_prior[-1]
            zone_price = df.loc[last_gg_idx, "EMA_avg"]
            latest_close = latest["Close"]
            pct_move = (latest_close - zone_price) / zone_price * 100
            closest_move_pct = round(pct_move, 2)
            achieved_levels = [lvl for lvl in breakout_levels if pct_move >= lvl]
            if achieved_levels:
                event_formed = True
                event_pct = max(achieved_levels)
                zone_date = last_gg_idx

    trend = compute_trend(latest)
    price_action, price_action_pct = compute_price_action(df)
    latest_volume, avg_volume, volume_spike = compute_volume_metrics(
        df, spike_multiplier=volume_spike_multiplier
    )
    avg_turnover = (avg_volume * float(latest["Close"])) if avg_volume else None

    return {
        "Symbol": display_symbol(ticker),
        "Last Close": round(float(latest["Close"]), 2),
        "EMA20": round(float(latest["EMA20"]), 2),
        "EMA50": round(float(latest["EMA50"]), 2),
        "EMA100": round(float(latest["EMA100"]), 2),
        "EMA200": round(float(latest["EMA200"]), 2),
        "EMA Spread %": round(latest_ema_spread, 2) if latest_ema_spread is not None else None,
        "Price Dev %": round(latest_price_dev, 2) if latest_price_dev is not None else None,
        "Golden Gate": "Formed" if gg_formed_now else "Not Formed",
        "EMA Breakout Signal": f"Formed (+{event_pct}% breakout)" if event_formed else "Not Formed",
        "Move from last GG zone %": closest_move_pct if closest_move_pct is not None else "-",
        "GG Zone Date": zone_date.strftime("%Y-%m-%d %H:%M") if zone_date is not None else "-",
        "Trend": trend,
        "Price Action": f"{price_action} ({price_action_pct:+.2f}%)" if price_action_pct is not None else price_action,
        "Volume": round(latest_volume) if latest_volume is not None else "-",
        "Avg Volume": round(avg_volume) if avg_volume is not None else "-",
        "Volume Spike": "Yes" if volume_spike else "No",
        "_avg_turnover": avg_turnover,  # internal - used to rank Liquidity after the full scan
    }


def add_liquidity_ranking(df):
    """Ranks stocks into High/Medium/Low Liquidity based on avg turnover
    (price x avg volume) RELATIVE TO OTHERS IN THIS SCAN - so it works
    sensibly whether you're scanning NSE stocks, crypto, or commodities,
    which have very different absolute turnover scales."""
    if df.empty or "_avg_turnover" not in df.columns:
        df["Liquidity"] = "-"
        return df

    valid = df["_avg_turnover"].notna()
    if valid.sum() < 3:
        df["Liquidity"] = df["_avg_turnover"].apply(lambda v: "-" if pd.isna(v) else "N/A (too few stocks to rank)")
        return df.drop(columns=["_avg_turnover"])

    ranks = df.loc[valid, "_avg_turnover"].rank(pct=True)

    def bucket(pct_rank):
        if pct_rank >= 0.67:
            return "High"
        if pct_rank >= 0.33:
            return "Medium"
        return "Low"

    df.loc[valid, "Liquidity"] = ranks.apply(bucket)
    df.loc[~valid, "Liquidity"] = "-"
    return df.drop(columns=["_avg_turnover"])


def run_scan(index_name, timeframe_label, progress_callback=None, custom_tickers=None,
             ema_convergence_pct=EMA_CONVERGENCE_PCT, price_to_ema_pct=PRICE_TO_EMA_PCT,
             breakout_levels=BREAKOUT_LEVELS, gg_lookback_bars=GG_LOOKBACK_BARS,
             volume_spike_multiplier=VOLUME_SPIKE_MULTIPLIER):
    """
    Runs a single-timeframe scan. progress_callback(i, total, ticker, result_or_None)
    is called after each stock so a UI can show live progress.
    If custom_tickers is given (list of symbols without .NS), NSE fetch is skipped
    and these are scanned instead - useful as a fallback if NSE blocks the request.
    Returns a pandas DataFrame of results.
    """
    interval, period, resample_to = TIMEFRAME_MAP[timeframe_label]

    if custom_tickers:
        tickers = [f"{s.strip().upper()}.NS" for s in custom_tickers if s.strip()]
    else:
        tickers = get_tickers_for_market(index_name)

    results = []

    for i, ticker in enumerate(tickers, 1):
        try:
            res = analyze_stock(
                ticker, interval, period, resample_to=resample_to,
                ema_convergence_pct=ema_convergence_pct,
                price_to_ema_pct=price_to_ema_pct,
                breakout_levels=breakout_levels,
                gg_lookback_bars=gg_lookback_bars,
                volume_spike_multiplier=volume_spike_multiplier,
            )
        except Exception:
            res = None
        if res:
            results.append(res)
        if progress_callback:
            progress_callback(i, len(tickers), ticker, res)
        time.sleep(REQUEST_DELAY_SEC)

    if not results:
        return pd.DataFrame()

    out_df = pd.DataFrame(results)
    out_df = add_liquidity_ranking(out_df)
    out_df = out_df.sort_values(by=["EMA Spread %"], ascending=[True])
    return out_df.reset_index(drop=True)


def run_multi_timeframe_scan(index_name, timeframe_labels, progress_callback=None, custom_tickers=None,
                              ema_convergence_pct=EMA_CONVERGENCE_PCT, price_to_ema_pct=PRICE_TO_EMA_PCT,
                              breakout_levels=BREAKOUT_LEVELS, gg_lookback_bars=GG_LOOKBACK_BARS,
                              volume_spike_multiplier=VOLUME_SPIKE_MULTIPLIER):
    """
    Scans each stock across MULTIPLE timeframes (1-3) and returns one row per
    stock with each timeframe's results side by side, e.g. columns like
    "Golden Gate (Daily)", "Trend (Daily)", "Golden Gate (Weekly)", etc.

    progress_callback(i, total, ticker, timeframe_label) is called after each
    (stock, timeframe) combination so a UI can show live progress.

    Note: Liquidity ranking is per-scan by design (see add_liquidity_ranking),
    which doesn't translate cleanly to a side-by-side multi-timeframe table,
    so Liquidity isn't included here - use a single-timeframe scan for that.
    """
    if not timeframe_labels:
        raise ValueError("Select at least one timeframe.")

    if custom_tickers:
        tickers = [f"{s.strip().upper()}.NS" for s in custom_tickers if s.strip()]
    else:
        tickers = get_tickers_for_market(index_name)

    total_steps = len(tickers) * len(timeframe_labels)
    step = 0
    rows = []

    for ticker in tickers:
        row = {"Symbol": display_symbol(ticker)}
        any_data = False

        for tf_label in timeframe_labels:
            interval, period, resample_to = TIMEFRAME_MAP[tf_label]
            try:
                res = analyze_stock(
                    ticker, interval, period, resample_to=resample_to,
                    ema_convergence_pct=ema_convergence_pct,
                    price_to_ema_pct=price_to_ema_pct,
                    breakout_levels=breakout_levels,
                    gg_lookback_bars=gg_lookback_bars,
                    volume_spike_multiplier=volume_spike_multiplier,
                )
            except Exception:
                res = None

            step += 1
            if progress_callback:
                progress_callback(step, total_steps, ticker, tf_label)
            time.sleep(REQUEST_DELAY_SEC)

            if res:
                any_data = True
                row[f"Last Close ({tf_label})"] = res["Last Close"]
                row[f"EMA Spread % ({tf_label})"] = res["EMA Spread %"]
                row[f"Golden Gate ({tf_label})"] = res["Golden Gate"]
                row[f"EMA Breakout Signal ({tf_label})"] = res["EMA Breakout Signal"]
                row[f"Trend ({tf_label})"] = res["Trend"]
                row[f"Price Action ({tf_label})"] = res["Price Action"]
                row[f"Volume Spike ({tf_label})"] = res["Volume Spike"]
            else:
                row[f"Last Close ({tf_label})"] = None
                row[f"EMA Spread % ({tf_label})"] = None
                row[f"Golden Gate ({tf_label})"] = "No Data"
                row[f"EMA Breakout Signal ({tf_label})"] = "No Data"
                row[f"Trend ({tf_label})"] = "No Data"
                row[f"Price Action ({tf_label})"] = "No Data"
                row[f"Volume Spike ({tf_label})"] = "No Data"

        if any_data:
            rows.append(row)

    if not rows:
        return pd.DataFrame()

    out_df = pd.DataFrame(rows)

    gg_cols = [c for c in out_df.columns if c.startswith("Golden Gate (")]
    event_cols = [c for c in out_df.columns if c.startswith("EMA Breakout Signal (")]

    out_df["Golden Gate - Any TF"] = out_df[gg_cols].apply(
        lambda r: "Formed" if any(v == "Formed" for v in r) else "Not Formed", axis=1
    )
    out_df["EMA Breakout Signal - Any TF"] = out_df[event_cols].apply(
        lambda r: "Formed" if any(str(v).startswith("Formed") for v in r) else "Not Formed", axis=1
    )

    out_df = out_df.sort_values(
        by=["Golden Gate - Any TF", "EMA Breakout Signal - Any TF"], ascending=[False, False]
    )
    return out_df.reset_index(drop=True)
