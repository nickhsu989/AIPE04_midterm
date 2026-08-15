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
            fig.add_trace(go.Bar(x=group[chart["x"]], y=group[chart["y"]],
                                 marker_color="#636efa", name=name))
        elif ctype == "scatter":
            fig.add_trace(go.Scatter(x=group[chart["x"]], y=group[chart["y"]],
                                     mode="markers", marker_color="#636efa",
                                     name=name))
        else:  # line (default)
            fig.add_trace(go.Scatter(x=group[chart["x"]], y=group[chart["y"]],
                                     mode="lines", line_color="#636efa",
                                     name=name))
    fig.update_layout(
        title=envelope.get("title") or chart.get("title") or "",
        template="plotly_white",
        paper_bgcolor="#ffffff",
        plot_bgcolor="#e9ebee",
        autotypenumbers="convert types",
        xaxis_title=chart.get("x", ""),
        yaxis_title=chart.get("y", ""),
        margin={"t": 60, "b": 40, "l": 40, "r": 20},
    )
    return fig

HEADERS = {
    "trade_date": "Trade Date", "open": "Open", "high": "High", "low": "Low",
    "close": "Close", "adj_close": "Adj Close", "volume_yf": "Volume (yf)",
    "market_cap": "Market Cap", "52w_low": "52W Low", "prev_close": "Prev Close",
    "price": "Price", "volume_fin": "Volume (fin)", "52w_high": "52W High",
    "perf_ytd": "Perf YTD", "perf_year": "Perf 1Y", "sma200": "SMA 200",
    "perf_half_y": "Perf 6M", "avg_volume": "Avg Volume", "perf_quarter": "Perf 3M",
    "sma50": "SMA 50", "perf_month": "Perf 1M", "sma20": "SMA 20",
    "atr": "ATR", "rsi_14": "RSI 14", "perf_week": "Perf 1W",
    "rel_volume": "Rel Volume", "change": "Change (%)",
}

WINDOW_OPTIONS = {
    "Last 30 days": 30,
    "Last 60 days": 60,
    "Last 90 days": 90,
    "Last 180 days": 180,
    "Last 365 days": 365,
    "All history": 0,
}

st.set_page_config(page_title="2D Plot streamlit 8501", layout="wide")

st.markdown(
    """
    <style>
    [data-testid="stMainMenu"],
    #MainMenu { visibility: hidden; }
    .stAppHeader,
    [data-testid="stAppHeader"] { display: none; }
    [data-testid="stTooltipHoverTarget"] { display: none !important; }
    header[data-testid="stHeader"] { display: none !important; }
    [data-testid="stMainBlockContainer"] {
      padding-top: 0.25rem !important;
      margin-top: -2.5rem !important;
    }
    .stSelectbox { width: 50%; }
    div[data-testid="stSelectbox"] > div:has(input),
    div[data-testid="stSelectbox"] > div:has(input) > div {
      background-color: #ffffff !important;
    }
    div[data-testid="stColumn"]:last-child .stSelectbox { width: 100%; margin-left: auto; }
    div[data-testid="stColumn"]:nth-child(2) .stSelectbox { margin-left: auto; }
    .st-key-header_box {
      background: #f5f6fa;
      border-bottom: 1px solid #d8dbe5;
      padding: 0.7rem 1rem 0.85rem;
      width: calc(100% + 2rem) !important;
      max-width: none !important;
      margin-left: -1rem !important;
      margin-right: -1rem !important;
      border-radius: 0;
    }
    .st-key-header_box [data-testid="stVerticalBlock"] {
      gap: 0 !important;
    }
    .st-key-header_box h1 {
      padding-bottom: 0 !important;
    }
    @media (min-width: 864px) {
      .block-container {
        padding-left: 5rem !important;
        padding-right: 5rem !important;
      }
      .st-key-header_box {
        width: calc(100% + 10rem) !important;
        max-width: none !important;
        margin-left: -5rem !important;
        margin-right: -5rem !important;
      }
    }
    </style>
    """,
    unsafe_allow_html=True,
)

with st.container(key="header_box", border=False):
    st.markdown(
        f"""
        <div style="display:flex; align-items:center; gap:1rem; flex-wrap:wrap;">
          <h1 style="margin:0;">2D Plot</h1>
          <a href="{logic_layer.get_urls()['main_url']}" target="_blank" rel="noopener"
             style="text-decoration:none; margin-left:auto; padding:0.4rem 0.9rem;
                    background:#ffffff; color:#1a1a2e;
                    border:1px solid #d8dbe5; border-radius:0.5rem;">
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

    _, col_col, sym_col, _, win_col = st.columns([1, 2, 2, 2, 1], vertical_alignment="center")
    with col_col:
        y_options = list(logic_layer.UNIFIED_NUMERIC_COLUMNS)
        y_column = st.selectbox(
            "Y (Date column)",
            y_options,
            index=y_options.index("close"),
            format_func=lambda c: logic_layer.CHANNEL_LABELS.get(c, c),
        )
    with sym_col:
        default_idx = symbols.index("AMD") if "AMD" in symbols else 0
        symbol = st.selectbox("Symbol", symbols, index=default_idx)
    with win_col:
        window = st.selectbox("X (Time range)", list(WINDOW_OPTIONS), index=0,
                              help="Default: last 30 days; All history = full history")

params = {"symbol": symbol, "y": y_column, "limit": 0}
if WINDOW_OPTIONS[window]:
    params["days"] = WINDOW_OPTIONS[window]

envelope = logic_layer.handle_request("history", params)
if envelope["status"] == "error":
    st.error(" | ".join(envelope["meta"].get("errors", [])))
elif envelope["status"] == "empty":
    st.warning(f"No price data for {symbol} in the unified dataset. Ingest via python ingest.py, python load_staging.py, python unify_databases.py")
else:
    st.plotly_chart(envelope_to_figure(envelope), width="stretch")
    if envelope["rows"]:
        df = pd.DataFrame(envelope["rows"], columns=[HEADERS.get(c, c) for c in envelope["columns"]])
        st.dataframe(df, hide_index=True)
