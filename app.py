import streamlit as st
import pandas as pd
import warnings
from datetime import datetime

from core import (
    INDEX_URLS, STATIC_MARKETS, ALL_MARKETS, TIMEFRAME_MAP, run_scan, run_multi_timeframe_scan,
    EMA_CONVERGENCE_PCT, PRICE_TO_EMA_PCT, BREAKOUT_LEVELS, GG_LOOKBACK_BARS, VOLUME_SPIKE_MULTIPLIER,
)

st.set_page_config(page_title="Golden Gate EMA Scanner", layout="wide")

st.title("🟡 Golden Gate EMA Scanner")
st.caption("A technical-analysis screening tool for EMA 20/50/100/200 convergence and breakout patterns on NSE stocks.")

st.warning(
    "⚠️ **This is an educational technical-analysis screening tool, not investment "
    "advice.** It is not registered with SEBI as a Research Analyst or Investment "
    "Adviser. All output is purely a mechanical calculation of EMA relationships - "
    "it is not a recommendation to buy or sell anything. All trading and investment "
    "decisions, and all associated risks, are entirely your own.",
    icon="⚠️",
)

with st.sidebar:
    st.header("Scan Settings")
    index_name = st.selectbox(
        "Market / Index",
        ALL_MARKETS,
        help="NSE stock indices, plus Crypto and Commodities baskets.",
    )

    timeframe_labels = st.multiselect(
        "Timeframe(s) - choose 1 to 3",
        list(TIMEFRAME_MAP.keys()),
        default=["Daily"],
        max_selections=3,
    )
    if len(timeframe_labels) == 0:
        st.caption("⚠️ Select at least one timeframe.")

    with st.expander("⚙️ Adjust rule thresholds", expanded=False):
        st.caption(
            "Golden Gate requires BOTH conditions below to be true at once - this is a "
            "genuinely rare setup, so it's normal to see 0 matches on many days. Loosen "
            "these to see more matches, or use the 'closest' ranking below to spot "
            "stocks that are near-forming."
        )
        ema_pct = st.slider("EMA convergence threshold (%)", 0.2, 5.0, EMA_CONVERGENCE_PCT, 0.1)
        price_pct = st.slider("Price-to-EMA threshold (%)", 0.2, 5.0, PRICE_TO_EMA_PCT, 0.1)
        lookback = st.slider("Golden Gate lookback window (bars)", 10, 200, GG_LOOKBACK_BARS, 10)
        breakout_levels = st.multiselect(
            "Breakout levels to flag as EMA Breakout Signal (%)",
            [3, 5, 8, 10, 15, 20, 25],
            default=BREAKOUT_LEVELS,
        )
        volume_spike_mult = st.slider(
            "Volume Spike threshold (x average volume)", 1.1, 5.0, VOLUME_SPIKE_MULTIPLIER, 0.1,
            help="Latest bar's volume must be at least this many times the average of the prior 20 bars to be flagged as a spike.",
        )

    custom_tickers = None
    with st.expander("⚠️ NSE blocking requests? Upload a stock list instead"):
        st.caption(
            "NSE sometimes blocks automated requests from cloud servers. If the scan "
            "fails, upload a CSV with a 'Symbol' column (NSE trading symbols, no .NS "
            "suffix) and the scanner will use that list instead of fetching from NSE."
        )
        uploaded = st.file_uploader("Upload CSV", type=["csv"])
        if uploaded is not None:
            try:
                up_df = pd.read_csv(uploaded)
                col = "Symbol" if "Symbol" in up_df.columns else up_df.columns[0]
                custom_tickers = up_df[col].astype(str).tolist()
                st.success(f"Loaded {len(custom_tickers)} symbols from file.")
            except Exception as e:
                st.error(f"Couldn't read file: {e}")

    run_button = st.button(
        "🔍 Run Scan", type="primary", use_container_width=True,
        disabled=len(timeframe_labels) == 0,
    )


def style_gg(dataframe):
    """Color-codes Golden Gate / EMA Breakout Signal / Trend / Price Action /
    Volume Spike columns (including per-timeframe ones like 'Trend (Daily)'):
    green for bullish/positive states, red for bearish/negative, grey for
    neutral/no-data."""
    def color_cell(val):
        if not isinstance(val, str):
            return ""
        v = val.strip()
        if v.startswith("Formed") or v == "Uptrend" or v.startswith("Bullish") or v == "Yes":
            return "color: #0a7d2c; font-weight: 600"
        if v == "Not Formed" or v == "Downtrend" or v.startswith("Bearish"):
            return "color: #c0392b"
        if v in ("Mixed", "No", "Unknown"):
            return "color: #888888"
        if v in ("No Data",):
            return "color: #888888; font-style: italic"
        return ""

    prefixes = ("Golden Gate", "EMA Breakout Signal", "Trend", "Price Action", "Volume Spike")
    subset = [c for c in dataframe.columns if c.startswith(prefixes)]
    styler = dataframe.style
    if hasattr(styler, "map"):  # pandas >= 2.1 renamed applymap -> map
        return styler.map(color_cell, subset=subset)
    return styler.applymap(color_cell, subset=subset)


if "results_df" not in st.session_state:
    st.session_state.results_df = None
    st.session_state.last_scan_info = None

if run_button and len(timeframe_labels) > 0:
    progress_bar = st.progress(0, text="Fetching index constituents...")
    status_text = st.empty()
    tf_display = " + ".join(timeframe_labels)

    def update_progress(i, total, ticker, result):
        status = "✅" if result else "⚠️ skipped"
        status_text.text(f"[{i}/{total}] {ticker} {status}")
        progress_bar.progress(i / total, text=f"Scanning {index_name} ({tf_display})... {i}/{total}")

    try:
        with st.spinner("Running scan..."), warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            if len(timeframe_labels) == 1:
                df = run_scan(
                    index_name,
                    timeframe_labels[0],
                    progress_callback=update_progress,
                    custom_tickers=custom_tickers,
                    ema_convergence_pct=ema_pct,
                    price_to_ema_pct=price_pct,
                    breakout_levels=breakout_levels,
                    gg_lookback_bars=lookback,
                    volume_spike_multiplier=volume_spike_mult,
                )
            else:
                df = run_multi_timeframe_scan(
                    index_name,
                    timeframe_labels,
                    progress_callback=update_progress,
                    custom_tickers=custom_tickers,
                    ema_convergence_pct=ema_pct,
                    price_to_ema_pct=price_pct,
                    breakout_levels=breakout_levels,
                    gg_lookback_bars=lookback,
                    volume_spike_multiplier=volume_spike_mult,
                )
        for w in caught:
            st.info(f"ℹ️ {w.message}")
        st.session_state.results_df = df
        st.session_state.last_scan_info = {
            "index": index_name if not custom_tickers else "Custom uploaded list",
            "timeframe": tf_display,
            "multi": len(timeframe_labels) > 1,
            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "count": len(df),
        }
    except Exception as e:
        st.session_state.results_df = None
        st.error(
            f"Scan failed: {e}\n\n"
            "If this is an NSE 503/blocking error, try the 'Upload a stock list' "
            "option in the sidebar as a workaround."
        )

    progress_bar.empty()
    status_text.empty()

df = st.session_state.results_df

if df is not None and not df.empty:
    info = st.session_state.last_scan_info
    st.success(
        f"Scanned {info['count']} stocks from {info['index']} on {info['timeframe']} "
        f"timeframe(s) at {info['time']}"
    )

    if info.get("multi"):
        tab1, tab2, tab3 = st.tabs(
            ["All Results (side by side)", "Golden Gate - Any TF", "EMA Breakout Signal - Any TF"]
        )

        summary_cols = ["Golden Gate - Any TF", "EMA Breakout Signal - Any TF"]
        display_cols = [c for c in df.columns if c not in summary_cols]

        with tab1:
            st.caption(
                "Each selected timeframe's Golden Gate / EMA Breakout Signal status is "
                "shown as its own column, so you can compare across timeframes at a glance."
            )
            st.dataframe(style_gg(df[display_cols]), use_container_width=True, height=550)

        with tab2:
            gg_any = df[df["Golden Gate - Any TF"] == "Formed"]
            st.metric("Stocks with Golden Gate formed on at least one selected timeframe", len(gg_any))
            st.dataframe(style_gg(gg_any[display_cols]), use_container_width=True, height=450)

        with tab3:
            event_any = df[df["EMA Breakout Signal - Any TF"] == "Formed"]
            st.metric("Stocks with EMA Breakout Signal on at least one selected timeframe", len(event_any))
            st.dataframe(style_gg(event_any[display_cols]), use_container_width=True, height=450)

    else:
        tab1, tab2, tab3, tab4 = st.tabs(
            ["All Results", "Golden Gate Formed", "EMA Breakout Signal", "Closest to Forming"]
        )

        with tab1:
            st.dataframe(style_gg(df), use_container_width=True, height=500)

        with tab2:
            gg_now = df[df["Golden Gate"] == "Formed"]
            st.metric("Stocks with Golden Gate formed", len(gg_now))
            st.dataframe(style_gg(gg_now), use_container_width=True, height=400)

        with tab3:
            gg_event = df[df["EMA Breakout Signal"].str.startswith("Formed")]
            st.metric("Stocks with EMA Breakout Signal", len(gg_event))
            st.dataframe(style_gg(gg_event), use_container_width=True, height=400)

        with tab4:
            st.caption(
                "Ranked by EMA Spread % (lowest = EMAs most tightly bunched together right "
                "now), so you can see which stocks are closest to a Golden Gate even if "
                "they haven't technically qualified yet."
            )
            closest = df.sort_values("EMA Spread %", ascending=True).head(20)
            st.dataframe(
                style_gg(closest[["Symbol", "Last Close", "EMA Spread %", "Price Dev %", "Golden Gate"]]),
                use_container_width=True,
                height=500,
            )

    csv = df.to_csv(index=False).encode("utf-8")
    st.download_button(
        "⬇️ Download results as CSV",
        data=csv,
        file_name=f"golden_gate_scan_{info['index'].replace(' ', '_')}_{info['timeframe'].replace(' ', '_').replace('+', 'and')}.csv",
        mime="text/csv",
    )
elif df is not None and df.empty:
    st.warning("No results returned. NSE may have blocked the request temporarily - try again shortly.")
else:
    st.info("Choose an index and 1-3 timeframes in the sidebar, then click **Run Scan**.")

st.divider()
st.caption(
    "Disclaimer: this tool performs purely mechanical EMA calculations on public "
    "market data. It is provided for educational and informational purposes only "
    "and does not constitute investment advice under SEBI regulations. Trading and "
    "investing in securities markets carries risk of loss; past patterns do not "
    "guarantee future results. Consult a SEBI-registered adviser before making "
    "investment decisions."
)
