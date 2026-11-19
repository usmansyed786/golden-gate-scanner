import io
import warnings
from pathlib import Path
from datetime import datetime

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

from core import (
    MARKET_GROUPS, TIMEFRAME_MAP, INTRADAY_INTERVALS, RUSSELL_FILE, is_nse_market,
    run_scan, run_multi_timeframe_scan, get_chart_data,
    EMA_CONVERGENCE_PCT, PRICE_TO_EMA_PCT, BREAKOUT_LEVELS, GG_LOOKBACK_BARS, VOLUME_SPIKE_MULTIPLIER,
)

st.set_page_config(page_title="Golden Gate EMA Scanner", page_icon="🌉", layout="wide")

# --------------------------------------------------------------------------- #
#  Look & feel: fog-grey canvas, deep navy ink, one bridge-orange signal colour
# --------------------------------------------------------------------------- #
st.markdown(
    """
<style>
@import url('https://fonts.googleapis.com/css2?family=Sora:wght@500;600;700&family=Source+Sans+3:wght@400;500;600&display=swap');

:root {
  --fog: #eef1f4; --panel: #ffffff; --ink: #14213d; --ink-soft: #5b6678;
  --line: #dde3ea; --gate: #d9480f; --gate-soft: #fff0e8;
  --up: #0b7a55; --down: #be123c;
}
html, body, [class*="css"], .stApp { font-family: 'Source Sans 3', sans-serif; color: var(--ink); }
.stApp { background: var(--fog); }
.block-container { padding-top: 2rem; max-width: 1280px; }
h1, h2, h3, h4 { font-family: 'Sora', sans-serif !important; letter-spacing: -0.01em; color: var(--ink); }

/* Hero */
.hero {
  display: grid; grid-template-columns: 1.1fr 1fr; gap: 24px; align-items: center;
  background: var(--ink); color: #fff; border-radius: 20px; padding: 30px 36px; margin-bottom: 18px;
}
.hero h1 { font-size: 2.3rem; line-height: 1.1; margin: 0 0 10px; color: #fff !important; }
.hero p { color: #b8c3d6; font-size: 1.05rem; margin: 0; max-width: 46ch; line-height: 1.5; }
.hero svg { width: 100%; height: auto; display: block; }
@media (max-width: 800px) { .hero { grid-template-columns: 1fr; padding: 24px; } .hero h1 { font-size: 1.8rem; } }

/* Notice */
.notice {
  background: var(--panel); border: 1px solid var(--line); border-left: 4px solid var(--gate);
  border-radius: 10px; padding: 12px 16px; font-size: .92rem; color: var(--ink-soft); margin-bottom: 20px;
}
.notice b { color: var(--ink); }

/* Summary figures */
.kpis { display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 12px; margin: 14px 0 22px; }
.kpi { background: var(--panel); border: 1px solid var(--line); border-radius: 12px; padding: 14px 16px; }
.kpi .label { font-size: .85rem; color: var(--ink-soft); }
.kpi .value { font-family: 'Sora', sans-serif; font-size: 1.9rem; font-weight: 600; line-height: 1.2; }
.kpi.signal { background: var(--gate-soft); border-color: #f4c3a8; }
.kpi.signal .value { color: var(--gate); }
.kpi .sub { font-size: .8rem; color: var(--ink-soft); }

/* Sidebar */
[data-testid="stSidebar"] { background: var(--panel); border-right: 1px solid var(--line); }
[data-testid="stSidebar"] h2 { font-size: 1.15rem; }

/* Tabs */
.stTabs [data-baseweb="tab-list"] { gap: 4px; border-bottom: 1px solid var(--line); }
.stTabs [data-baseweb="tab"] { height: 44px; padding: 0 16px; color: var(--ink-soft); font-weight: 600; }
.stTabs [aria-selected="true"] { color: var(--gate) !important; }
.stTabs [data-baseweb="tab-highlight"] { background-color: var(--gate) !important; }

/* Primary button */
.stButton > button[kind="primary"], .stDownloadButton > button {
  background: var(--gate); border: 0; color: #fff; font-weight: 600; border-radius: 10px; padding: .55rem 1rem;
}
.stButton > button[kind="primary"]:hover, .stDownloadButton > button:hover { background: #b83c0a; color: #fff; }
.stButton > button[kind="primary"]:disabled { background: #e4c2b3; }

/* Tables */
[data-testid="stDataFrame"] { border: 1px solid var(--line); border-radius: 12px; overflow: hidden; }
.foot { color: var(--ink-soft); font-size: .82rem; line-height: 1.5; border-top: 1px solid var(--line); padding-top: 14px; margin-top: 28px; }
</style>

<div class="hero">
  <div>
    <h1>Golden Gate EMA Scanner</h1>
    <p>Find stocks, crypto and commodities where the 20, 50, 100 and 200 EMAs squeeze together
    around price, and see what happened after the gate opened.</p>
  </div>
  <svg viewBox="0 0 520 170" role="img" aria-label="Four moving averages converging into one point">
    <g fill="none" stroke-linecap="round" stroke-width="3">
      <path d="M0,150 C150,150 280,92 420,86 S500,90 520,90" stroke="#4a5d82"/>
      <path d="M0,108 C150,108 280,84 420,82 S500,82 520,82" stroke="#7d8fb3"/>
      <path d="M0,62 C150,62 280,78 420,79 S500,76 520,74" stroke="#aebbd6"/>
      <path d="M0,20 C150,20 280,74 420,76 S500,70 520,66" stroke="#ff7a3d"/>
    </g>
    <circle cx="420" cy="81" r="15" fill="none" stroke="#ff7a3d" stroke-width="2" opacity=".55"/>
    <circle cx="420" cy="81" r="4.5" fill="#ff7a3d"/>
  </svg>
</div>

<div class="notice">
  <b>Educational screening tool, not investment advice.</b> It is not registered with SEBI as a
  Research Analyst or Investment Adviser. Output is a mechanical calculation of EMA relationships
  and is not a recommendation to buy or sell anything. All trading and investment decisions, and
  the risks that come with them, are yours.
</div>
""",
    unsafe_allow_html=True,
)

# --------------------------------------------------------------------------- #
#  Sidebar
# --------------------------------------------------------------------------- #
with st.sidebar:
    st.header("Scan settings")

    group = st.selectbox("Market group", list(MARKET_GROUPS.keys()))
    index_name = st.selectbox("Index / basket", MARKET_GROUPS[group])

    exchange = "NSE"
    if is_nse_market(index_name):
        exchange = st.radio(
            "Listing to scan", ["NSE", "BSE"], horizontal=True,
            help="BSE maps each constituent to its BSE listing (.BO). A few stocks "
                 "have no matching BSE ticker on Yahoo and are skipped.",
        )

    timeframe_labels = st.multiselect(
        "Timeframes (1 to 3)", list(TIMEFRAME_MAP.keys()), default=["Daily"], max_selections=3,
    )
    if not timeframe_labels:
        st.caption("Select at least one timeframe.")

    with st.expander("Rule thresholds"):
        st.caption(
            "A Golden Gate needs both conditions at once, which is rare, so zero matches "
            "on many days is normal. Loosen the limits for more matches, or check the "
            "'Closest to forming' tab."
        )
        ema_pct = st.slider("EMA convergence (%)", 0.2, 5.0, EMA_CONVERGENCE_PCT, 0.1)
        price_pct = st.slider("Price distance from EMAs (%)", 0.2, 5.0, PRICE_TO_EMA_PCT, 0.1)
        lookback = st.slider("Lookback window (bars)", 10, 200, GG_LOOKBACK_BARS, 10)
        breakout_levels = st.multiselect(
            "Breakout levels (%)", [3, 5, 8, 10, 15, 20, 25], default=BREAKOUT_LEVELS,
        )
        volume_spike_mult = st.slider(
            "Volume spike (x average)", 1.1, 5.0, VOLUME_SPIKE_MULTIPLIER, 0.1,
            help="Latest bar's volume vs the average of the prior 20 bars.",
        )

    custom_tickers, custom_suffix = None, ".NS"
    with st.expander("Use my own stock list"):
        st.caption(
            "Upload a CSV with a 'Symbol' column, or type symbols below. Use this for any "
            "index not listed here (for example other BSE indices), or when the exchange "
            "blocks automated requests."
        )
        suffix_label = st.selectbox(
            "My symbols are listed on",
            ["NSE (.NS)", "BSE (.BO)", "US / global (as typed)"],
        )
        custom_suffix = {"NSE (.NS)": ".NS", "BSE (.BO)": ".BO"}.get(suffix_label, "")
        typed = st.text_area("Symbols (comma or line separated)", placeholder="RELIANCE, TCS, INFY", height=80)
        uploaded = st.file_uploader("or upload CSV", type=["csv"])

        symbols = [s for s in typed.replace("\n", ",").split(",") if s.strip()]
        raw_upload = None
        if uploaded is not None:
            try:
                raw_upload = uploaded.getvalue().decode("utf-8-sig")
                lines = raw_upload.splitlines()
                start = next(
                    (i for i, ln in enumerate(lines)
                     if ln.replace('"', "").strip().startswith(("Ticker,", "Symbol,"))), 0)
                up_df = pd.read_csv(io.StringIO("\n".join(lines[start:])))
                col = next((c for c in ("Symbol", "Ticker") if c in up_df.columns), up_df.columns[0])
                if "Asset Class" in up_df.columns:
                    up_df = up_df[up_df["Asset Class"].astype(str).str.strip() == "Equity"]
                symbols += [s.replace(".", "-") if not suffix_label.startswith(("NSE", "BSE")) else s
                            for s in up_df[col].astype(str).tolist()]
            except Exception as e:
                st.error(f"Couldn't read the file: {e}")
        if symbols:
            custom_tickers = symbols
            st.success(f"{len(symbols)} symbols loaded. They will be scanned instead of the index above.")
        if raw_upload and len(symbols) >= 500:
            if st.button("Save as Russell 2000 list", help="Lets the RUSSELL 2000 index in the US group use this file."):
                try:
                    RUSSELL_FILE.parent.mkdir(exist_ok=True)
                    RUSSELL_FILE.write_text(raw_upload, encoding="utf-8")
                    st.success("Saved. Pick RUSSELL 2000 in the US group and clear the upload to scan it.")
                except Exception as e:
                    st.error(f"Couldn't save the file: {e}")

    run_button = st.button("Run scan", type="primary", width="stretch", disabled=not timeframe_labels)


# --------------------------------------------------------------------------- #
#  Helpers
# --------------------------------------------------------------------------- #
def style_gg(dataframe):
    """Colour-codes signal, trend, price-action and volume columns."""
    def color_cell(val):
        if not isinstance(val, str):
            return ""
        v = val.strip()
        if v.startswith("Formed"):
            return "color: #d9480f; font-weight: 700; background-color: #fff0e8"
        if v in ("Uptrend", "Yes", "High") or v.startswith("Bullish"):
            return "color: #0b7a55; font-weight: 600"
        if v in ("Downtrend", "Low") or v.startswith("Bearish"):
            return "color: #be123c"
        if v in ("Not Formed", "Mixed", "No", "Unknown", "Medium"):
            return "color: #7b8798"
        if v == "No Data":
            return "color: #9aa5b4; font-style: italic"
        return ""

    prefixes = ("Golden Gate", "EMA Breakout Signal", "Trend", "Price Action", "Volume Spike", "Liquidity")
    subset = [c for c in dataframe.columns if c.startswith(prefixes)]
    styler = dataframe.style
    if hasattr(styler, "map"):
        return styler.map(color_cell, subset=subset)
    return styler.applymap(color_cell, subset=subset)


def kpi(label, value, sub="", signal=False):
    cls = "kpi signal" if signal else "kpi"
    return (f'<div class="{cls}"><div class="label">{label}</div>'
            f'<div class="value">{value}</div><div class="sub">{sub}</div></div>')


def show_table(d, height=480):
    if d.empty:
        st.info("Nothing matches yet. Loosen the thresholds in the sidebar or check another tab.")
        return
    st.dataframe(style_gg(d.drop(columns=["Ticker"], errors="ignore")),
                 width="stretch", height=height, hide_index=True)


@st.cache_data(ttl=900, show_spinner=False)
def load_chart(ticker, tf_label):
    return get_chart_data(ticker, tf_label)


def draw_chart(d, title, intraday):
    x = d.index.strftime("%d %b %H:%M") if intraday else d.index
    has_vol = "Volume" in d.columns and d["Volume"].fillna(0).sum() > 0
    fig = make_subplots(rows=2 if has_vol else 1, cols=1, shared_xaxes=True,
                        row_heights=[0.78, 0.22] if has_vol else [1], vertical_spacing=0.03)
    fig.add_trace(go.Candlestick(
        x=x, open=d["Open"], high=d["High"], low=d["Low"], close=d["Close"], name="Price",
        increasing_line_color="#0b7a55", decreasing_line_color="#be123c",
        increasing_fillcolor="#0b7a55", decreasing_fillcolor="#be123c"), row=1, col=1)
    for col, color in (("EMA20", "#ff7a3d"), ("EMA50", "#4a5d82"), ("EMA100", "#7d8fb3"), ("EMA200", "#14213d")):
        fig.add_trace(go.Scatter(x=x, y=d[col], name=col, line=dict(color=color, width=1.6)), row=1, col=1)
    if has_vol:
        fig.add_trace(go.Bar(x=x, y=d["Volume"], name="Volume", marker_color="#c5cfdd"), row=2, col=1)
    fig.update_layout(
        title=dict(text=title, font=dict(family="Sora", size=16)),
        height=620, margin=dict(l=10, r=10, t=50, b=10), paper_bgcolor="#ffffff",
        plot_bgcolor="#ffffff", xaxis_rangeslider_visible=False, legend=dict(orientation="h", y=1.06),
        font=dict(family="Source Sans 3", color="#14213d"),
    )
    fig.update_xaxes(gridcolor="#eef1f4")
    fig.update_yaxes(gridcolor="#eef1f4")
    if intraday:
        fig.update_xaxes(type="category", nticks=10)
    return fig


def chart_explorer(df, timeframes):
    # Streamlit runs every tab on each refresh, so the chart only loads on request;
    # otherwise every click would send a fresh request to Yahoo.
    if not st.toggle("Load chart", key="chart_on", help="Fetches fresh price data for one symbol."):
        st.caption("Turn on 'Load chart' to draw a candlestick chart with the four EMAs.")
        return
    c1, c2 = st.columns([2, 1])
    symbol = c1.selectbox("Symbol", df["Symbol"].tolist(), key="chart_symbol")
    tf = c2.selectbox("Timeframe", timeframes, key="chart_tf")
    ticker = dict(zip(df["Symbol"], df["Ticker"]))[symbol]
    try:
        with st.spinner("Loading chart..."):
            data = load_chart(ticker, tf)
    except Exception as e:
        st.warning(
            "Couldn't load this chart. Yahoo Finance is likely limiting requests from this "
            "server right now, so wait a minute and try again. "
            f"(Details: {type(e).__name__})"
        )
        return
    if data is None:
        st.info("No price data available for this symbol and timeframe.")
        return
    intraday = TIMEFRAME_MAP[tf][0] in INTRADAY_INTERVALS
    st.plotly_chart(draw_chart(data, f"{symbol} · {tf}", intraday), width="stretch")


# --------------------------------------------------------------------------- #
#  Run scan
# --------------------------------------------------------------------------- #
if "results_df" not in st.session_state:
    st.session_state.results_df = None
    st.session_state.last_scan_info = None

if run_button and timeframe_labels:
    progress_bar = st.progress(0, text="Fetching constituents...")
    status_text = st.empty()
    tf_display = " + ".join(timeframe_labels)

    def update_progress(i, total, symbol, result):
        status_text.text(f"[{i}/{total}] {symbol}")
        progress_bar.progress(i / total, text=f"Scanning {index_name} ({tf_display})... {i}/{total}")

    scan_args = dict(
        progress_callback=update_progress,
        custom_tickers=custom_tickers,
        ema_convergence_pct=ema_pct,
        price_to_ema_pct=price_pct,
        breakout_levels=breakout_levels,
        gg_lookback_bars=lookback,
        volume_spike_multiplier=volume_spike_mult,
        exchange=exchange,
        custom_suffix=custom_suffix,
    )
    try:
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            if len(timeframe_labels) == 1:
                df = run_scan(index_name, timeframe_labels[0], **scan_args)
            else:
                df = run_multi_timeframe_scan(index_name, timeframe_labels, **scan_args)
        for w in caught:
            st.info(f"ℹ️ {w.message}")
        label = "Custom list" if custom_tickers else index_name
        if exchange == "BSE" and not custom_tickers:
            label += " (BSE listings)"
        st.session_state.results_df = df
        st.session_state.last_scan_info = {
            "index": label, "timeframe": tf_display, "timeframes": list(timeframe_labels),
            "multi": len(timeframe_labels) > 1, "count": len(df),
            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
    except Exception as e:
        st.session_state.results_df = None
        st.error(
            f"Scan failed: {e}\n\nIf the exchange is blocking the request, use "
            "'Use my own stock list' in the sidebar as a workaround."
        )

    progress_bar.empty()
    status_text.empty()

# --------------------------------------------------------------------------- #
#  Results
# --------------------------------------------------------------------------- #
df = st.session_state.results_df

if df is not None and not df.empty:
    info = st.session_state.last_scan_info
    st.markdown(f"### {info['index']}")
    st.caption(f"{info['count']} symbols scanned on {info['timeframe']} at {info['time']}")

    f1, f2 = st.columns([2, 3])
    query = f1.text_input("Search symbol", placeholder="Type to filter, e.g. TATA", label_visibility="collapsed")
    trend_pick = []
    if not info["multi"]:
        trend_pick = f2.multiselect("Trend", ["Uptrend", "Mixed", "Downtrend"], placeholder="Filter by trend",
                                    label_visibility="collapsed")

    view = df
    if query.strip():
        view = view[view["Symbol"].str.contains(query.strip(), case=False, na=False, regex=False)]
    if trend_pick:
        view = view[view["Trend"].isin(trend_pick)]

    if info["multi"]:
        gg_any = view[view["Golden Gate - Any TF"] == "Formed"]
        ev_any = view[view["EMA Breakout Signal - Any TF"] == "Formed"]
        st.markdown(
            '<div class="kpis">'
            + kpi("Symbols shown", len(view), f"of {len(df)} scanned")
            + kpi("Golden Gate formed", len(gg_any), "on at least one timeframe", signal=True)
            + kpi("Breakout signal", len(ev_any), "on at least one timeframe", signal=True)
            + "</div>",
            unsafe_allow_html=True,
        )

        summary_cols = ["Golden Gate - Any TF", "EMA Breakout Signal - Any TF"]
        shown_cols = [c for c in view.columns if c not in summary_cols]

        tab1, tab2, tab3, tab4 = st.tabs(["All results", "Golden Gate", "Breakout signal", "Chart"])
        with tab1:
            st.caption("Each timeframe gets its own columns so you can compare them at a glance.")
            show_table(view[shown_cols], 550)
        with tab2:
            show_table(gg_any[shown_cols], 450)
        with tab3:
            show_table(ev_any[shown_cols], 450)
        with tab4:
            chart_explorer(view if not view.empty else df, info["timeframes"])
    else:
        gg_now = view[view["Golden Gate"] == "Formed"]
        ev_now = view[view["EMA Breakout Signal"].str.startswith("Formed")]
        spikes = view[view["Volume Spike"] == "Yes"]
        uptrends = view[view["Trend"] == "Uptrend"]
        st.markdown(
            '<div class="kpis">'
            + kpi("Symbols shown", len(view), f"of {len(df)} scanned")
            + kpi("Golden Gate formed", len(gg_now), "right now", signal=True)
            + kpi("Breakout signal", len(ev_now), "after a recent gate", signal=True)
            + kpi("Volume spikes", len(spikes), "latest bar")
            + kpi("In uptrend", len(uptrends), "EMA 20 > 50 > 100 > 200")
            + "</div>",
            unsafe_allow_html=True,
        )

        tab1, tab2, tab3, tab4, tab5 = st.tabs(
            ["All results", "Golden Gate", "Breakout signal", "Closest to forming", "Chart"]
        )
        with tab1:
            show_table(view, 520)
        with tab2:
            show_table(gg_now, 420)
        with tab3:
            show_table(ev_now, 420)
        with tab4:
            st.caption(
                "Ranked by EMA spread. The lowest numbers are the stocks whose EMAs are "
                "most tightly bunched right now, even if they haven't qualified yet."
            )
            closest = view.sort_values("EMA Spread %", ascending=True).head(20)
            show_table(closest[["Symbol", "Ticker", "Last Close", "EMA Spread %", "Price Dev %", "Golden Gate"]], 520)
        with tab5:
            chart_explorer(view if not view.empty else df, info["timeframes"])

    st.download_button(
        "Download results as CSV",
        data=df.to_csv(index=False).encode("utf-8"),
        file_name=f"golden_gate_scan_{info['index'].replace(' ', '_')}_{info['timeframe'].replace(' ', '_').replace('+', 'and')}.csv",
        mime="text/csv",
    )
elif df is not None and df.empty:
    st.warning("The scan returned no results. The exchange may have blocked the request, so try again shortly or use your own stock list.")
else:
    st.info("Pick a market group and index in the sidebar, choose up to three timeframes, then select **Run scan**.")

st.markdown(
    '<div class="foot">Disclaimer: this tool performs purely mechanical EMA calculations on public '
    "market data. It is provided for educational and informational purposes only and does not "
    "constitute investment advice under SEBI regulations. Trading and investing in securities "
    "markets carries risk of loss; past patterns do not guarantee future results. Consult a "
    "SEBI-registered adviser before making investment decisions.</div>",
    unsafe_allow_html=True,
)
