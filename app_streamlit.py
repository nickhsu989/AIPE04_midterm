"""app_streamlit.py — barebones Streamlit dashboard.

Minimal display page: pick an ingested symbol from MySQL, auto-render its
price history chart within a chosen trailing window (default: last 30
days). Uses the SAME logic_layer registry as the Flask main app.
"""
import pandas as pd
import streamlit as st

import logic_layer
from app_presenter import envelope_to_figure
from config import CFG

HEADERS = {
    "trade_date": "Trade Date", "open": "Open", "high": "High", "low": "Low",
    "close": "Close", "adj_close": "Adj Close", "volume": "Volume",
}

WINDOW_OPTIONS = {
    "Last 30 days": 30,
    "Last 60 days": 60,
    "Last 90 days": 90,
    "Last 365 days": 365,
    "All": 0,
}

st.set_page_config(page_title="Analytics Dashboard", layout="wide")
st.title("Analytics Dashboard")

st.link_button("Return to Main Page", CFG["FTE_MAIN_URL"])

symbols = logic_layer.symbol_list()
if not symbols:
    st.warning("No data ingested yet. Run: python ingest_api.py --symbol AAPL --period 1y")
    st.stop()

symbol = st.selectbox("Symbol", symbols)

window = st.radio("Window", list(WINDOW_OPTIONS), index=0, horizontal=True,
                  help="Default: last 30 days; All = full history")

params = {"symbol": symbol, "limit": 0}
if WINDOW_OPTIONS[window]:
    params["days"] = WINDOW_OPTIONS[window]

envelope = logic_layer.handle_request("history", params)
if envelope["status"] == "error":
    st.error(" | ".join(envelope["meta"].get("errors", [])))
elif envelope["status"] == "empty":
    st.warning(f"No price data for {symbol}. Ingest it first (python ingest_api.py --symbol {symbol}).")
else:
    st.subheader(envelope["title"])
    st.plotly_chart(envelope_to_figure(envelope), width="stretch")
    if envelope["rows"]:
        df = pd.DataFrame(envelope["rows"], columns=[HEADERS.get(c, c) for c in envelope["columns"]])
        st.dataframe(df, hide_index=True)
