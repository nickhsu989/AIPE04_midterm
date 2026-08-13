"""app_streamlit.py — barebones Streamlit dashboard.

Minimal display page: pick an ingested symbol from MySQL, auto-render its
price history chart within a chosen trailing window (default: last 30
days). Uses the SAME logic_layer registry as the Flask main app.
"""
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import logic_layer

CHART_TYPES = ("line", "bar", "scatter", "candlestick", "table")


def envelope_to_figure(envelope):
    """envelope -> plotly.graph_objects.Figure (or None if no chart data).

    2D dashboard presenter (folded in from the former app_presenter.py):
    maps a Logic Layer envelope to a Plotly figure. Generic by design —
    chart types are limited to {line, bar, scatter, candlestick, table},
    so no new metric ever requires UI changes.
    """
    if envelope.get("status") != "ok" or not envelope.get("rows"):
        return None
    chart = envelope.get("chart") or {}
    ctype = chart.get("type", "line")
    columns = envelope["columns"]
    rows = envelope["rows"]
    if not columns:
        return None
    df = pd.DataFrame(rows, columns=columns)
    for col in df.columns:
        try:
            df[col] = pd.to_numeric(df[col])
        except (ValueError, TypeError):
            pass

    color_col = chart.get("color")
    groups = df.groupby(color_col) if color_col and color_col in df.columns else [(None, df)]

    fig = go.Figure()
    for key, group in groups:
        name = str(key) if key is not None else ""
        if ctype == "candlestick":
            fig.add_trace(go.Candlestick(
                x=group[chart["x"]],
                open=group["open"], high=group["high"],
                low=group["low"], close=group["close"], name=name,
            ))
        elif ctype == "bar":
            fig.add_trace(go.Bar(x=group[chart["x"]], y=group[chart["y"]], name=name))
        elif ctype == "scatter":
            fig.add_trace(go.Scatter(x=group[chart["x"]], y=group[chart["y"]],
                                     mode="markers", name=name))
        else:  # line (default)
            fig.add_trace(go.Scatter(x=group[chart["x"]], y=group[chart["y"]],
                                     mode="lines", name=name))
    fig.update_layout(
        title=envelope.get("title") or chart.get("title") or "",
        template="plotly_dark",
        autotypenumbers="convert types",
        xaxis_title=chart.get("x", ""),
        yaxis_title=chart.get("y", ""),
        margin={"t": 60, "b": 40, "l": 40, "r": 20},
    )
    return fig

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

st.set_page_config(page_title="2D Plot", layout="wide")

st.markdown(
    """
    <style>
    [data-testid="stMainMenu"],
    #MainMenu { visibility: hidden; }
    .stAppHeader,
    [data-testid="stAppHeader"] { display: none; }
    .block-container { padding-top: 0.2rem; }
    </style>
    """,
    unsafe_allow_html=True,
)

st.markdown(
    f"""
    <div style="display:flex; align-items:center; gap:1rem; flex-wrap:wrap;">
      <h1 style="margin:0;">2D Plot</h1>
      <a href="{logic_layer.get_urls()['main_url']}" target="_blank" rel="noopener"
         style="text-decoration:none; margin-left:auto; padding:0.4rem 0.9rem;
                border:1px solid rgba(250,250,250,0.2); border-radius:0.5rem;">
        Go to 5D Plot &#10148;
      </a>
    </div>
    """,
    unsafe_allow_html=True,
)

symbols = logic_layer.symbol_list()
if not symbols:
    st.warning("No data ingested yet. Run: python ingest.py, python load_staging.py, python load_close_open_ratio.py")
    st.stop()

sym_col, win_col = st.columns([1, 4], vertical_alignment="center")
with sym_col:
    default_idx = symbols.index("AAPL") if "AAPL" in symbols else 0
    symbol = st.selectbox("Symbol", symbols, index=default_idx)
with win_col:
    window = st.radio("Window", list(WINDOW_OPTIONS), index=0, horizontal=True,
                      help="Default: last 30 days; All = full history")

params = {"symbol": symbol, "limit": 0}
if WINDOW_OPTIONS[window]:
    params["days"] = WINDOW_OPTIONS[window]

envelope = logic_layer.handle_request("history", params)
if envelope["status"] == "error":
    st.error(" | ".join(envelope["meta"].get("errors", [])))
elif envelope["status"] == "empty":
    st.warning(f"No price data for {symbol} in price_history. Ingest via python ingest.py then python load_staging.py")
else:
    st.plotly_chart(envelope_to_figure(envelope), width="stretch")
    if envelope["rows"]:
        df = pd.DataFrame(envelope["rows"], columns=[HEADERS.get(c, c) for c in envelope["columns"]])
        st.dataframe(df, hide_index=True)
