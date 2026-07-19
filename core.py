"""
core.py
Golden Gate EMA Scanner - core logic (shared by CLI script and web app)
"""

import io
import time
from datetime import datetime

import pandas as pd
import requests
import yfinance as yf

# ============================== CONFIG ======================================

EMA_PERIODS = [20, 50, 100, 200]

EMA_CONVERGENCE_PCT = 1.0      # all 4 EMAs must be within this % of each other -> "closer"
PRICE_TO_EMA_PCT = 1.0         # price must be within this % of the EMA cluster (avg of 4 EMAs)

BREAKOUT_LEVELS = [5, 10, 15, 20]   # % breakout buckets to check/report (Golden Gate Event)
GG_LOOKBACK_BARS = 50               # how many bars back to look for a prior Golden Gate

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

# label -> (yfinance interval, yfinance period)
TIMEFRAME_MAP = {
    "5 minute": ("5m", "60d"),
    "15 minute": ("15m", "60d"),
    "30 minute": ("30m", "60d"),
    "1 hour": ("60m", "730d"),
    "Daily": ("1d", "5y"),
    "Weekly": ("1wk", "10y"),
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
    """Download the official NSE constituent list for an index.
    Retries with a fresh session/backoff since NSE occasionally 503s bot-like
    traffic, especially from cloud/datacenter IPs."""
    last_error = None
    for attempt in range(1, max_retries + 1):
        try:
            session = requests.Session()
            session.headers.update(NSE_HEADERS)
            # Warm-up request sets the cookies NSE expects before allowing archives access
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
                time.sleep(2 * attempt)  # backoff: 2s, 4s, ...
    raise RuntimeError(
        f"Could not fetch index list from NSE after {max_retries} attempts ({last_error}). "
        "NSE sometimes blocks automated/cloud-hosted requests (503) even with correct URLs and "
        "headers. This is more likely to happen when the app runs on a cloud server (e.g. "
        "Streamlit Cloud) than on your own PC in India. See the README for a fallback option."
    )


def fetch_price_data(ticker, interval, period):
    data = yf.Ticker(ticker).history(period=period, interval=interval, auto_adjust=True)
    if data is None or data.empty:
        return None
    return data.dropna(how="all")


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


def analyze_stock(ticker, interval, period, ema_convergence_pct=EMA_CONVERGENCE_PCT,
                   price_to_ema_pct=PRICE_TO_EMA_PCT, breakout_levels=BREAKOUT_LEVELS,
                   gg_lookback_bars=GG_LOOKBACK_BARS):
    df = fetch_price_data(ticker, interval, period)
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
    closest_move_pct = None  # how far price has moved from the most recent GG zone, even if below threshold

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

    return {
        "Symbol": ticker.replace(".NS", ""),
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
    }


def run_scan(index_name, timeframe_label, progress_callback=None, custom_tickers=None,
             ema_convergence_pct=EMA_CONVERGENCE_PCT, price_to_ema_pct=PRICE_TO_EMA_PCT,
             breakout_levels=BREAKOUT_LEVELS, gg_lookback_bars=GG_LOOKBACK_BARS):
    """
    Runs the full scan. progress_callback(i, total, ticker, result_or_None) is called
    after each stock so a UI can show live progress.
    If custom_tickers is given (list of symbols without .NS), NSE fetch is skipped
    and these are scanned instead - useful as a fallback if NSE blocks the request.
    Threshold params let a caller (e.g. the Streamlit sidebar) override the defaults
    without editing this file.
    Returns a pandas DataFrame of results.
    """
    interval, period = TIMEFRAME_MAP[timeframe_label]

    if custom_tickers:
        tickers = [f"{s.strip().upper()}.NS" for s in custom_tickers if s.strip()]
    else:
        url = INDEX_URLS[index_name]
        tickers = get_index_symbols(url)

    results = []

    for i, ticker in enumerate(tickers, 1):
        try:
            res = analyze_stock(
                ticker, interval, period,
                ema_convergence_pct=ema_convergence_pct,
                price_to_ema_pct=price_to_ema_pct,
                breakout_levels=breakout_levels,
                gg_lookback_bars=gg_lookback_bars,
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
    out_df = out_df.sort_values(by=["EMA Spread %"], ascending=[True])
    return out_df.reset_index(drop=True)


def analyze_stock_multi_tf(ticker, timeframe_labels, ema_convergence_pct=EMA_CONVERGENCE_PCT,
                            price_to_ema_pct=PRICE_TO_EMA_PCT, breakout_levels=BREAKOUT_LEVELS,
                            gg_lookback_bars=GG_LOOKBACK_BARS):
    """Analyzes one stock across multiple timeframes and returns one row with
    each timeframe's results as separate, labeled columns (side by side)."""
    row = {"Symbol": ticker.replace(".NS", "")}
    any_success = False

    for tf_label in timeframe_labels:
        interval, period = TIMEFRAME_MAP[tf_label]
        res = analyze_stock(
            ticker, interval, period,
            ema_convergence_pct=ema_convergence_pct,
            price_to_ema_pct=price_to_ema_pct,
            breakout_levels=breakout_levels,
            gg_lookback_bars=gg_lookback_bars,
        )
        if res:
            any_success = True
            row[f"Last Close ({tf_label})"] = res["Last Close"]
            row[f"Golden Gate ({tf_label})"] = res["Golden Gate"]
            row[f"EMA Breakout Signal ({tf_label})"] = res["EMA Breakout Signal"]
            row[f"EMA Spread % ({tf_label})"] = res["EMA Spread %"]
        else:
            row[f"Last Close ({tf_label})"] = None
            row[f"Golden Gate ({tf_label})"] = "No Data"
            row[f"EMA Breakout Signal ({tf_label})"] = "No Data"
            row[f"EMA Spread % ({tf_label})"] = None

    return row if any_success else None


def run_multi_tf_scan(index_name, timeframe_labels, progress_callback=None, custom_tickers=None,
                       ema_convergence_pct=EMA_CONVERGENCE_PCT, price_to_ema_pct=PRICE_TO_EMA_PCT,
                       breakout_levels=BREAKOUT_LEVELS, gg_lookback_bars=GG_LOOKBACK_BARS):
    """
    Scans an index across up to 3 timeframes at once, returning one row per
    stock with each timeframe's Golden Gate / EMA Breakout Signal side by side,
    plus 'Golden Gate - Any TF' / 'EMA Breakout Signal - Any TF' summary columns
    (Formed if the pattern is present on at least one of the selected timeframes).
    """
    if custom_tickers:
        tickers = [f"{s.strip().upper()}.NS" for s in custom_tickers if s.strip()]
    else:
        url = INDEX_URLS[index_name]
        tickers = get_index_symbols(url)

    results = []

    for i, ticker in enumerate(tickers, 1):
        try:
            res = analyze_stock_multi_tf(
                ticker, timeframe_labels,
                ema_convergence_pct=ema_convergence_pct,
                price_to_ema_pct=price_to_ema_pct,
                breakout_levels=breakout_levels,
                gg_lookback_bars=gg_lookback_bars,
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
