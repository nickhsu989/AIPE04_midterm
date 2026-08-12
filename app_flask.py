"""app_flask.py — Flask main app (presentation only).

Routes:
    GET /                  -> 3D market chart page, rendered from the
                              logic_layer "market_3d" metric envelope
    GET /api/config        -> main/streamlit URLs (used by index.html)

All data logic (SQL, filtering, decimation, column -> chart-channel
mapping, symbol/days defaults) lives in logic_layer; this file only turns
the canonical envelope into a Plotly figure and serves index.html.

Binary view (integrated on the main page): with source=sampled the Z
dropdown offers the computed channel `change_bin` (0/1 flag from
change >= threshold, computed at query time by market_3d). When it is
selected a threshold slider (0..BINARY_MAX_THRESHOLD) is shown and the
query param ?threshold=N is sent.

URL semantics:
    days absent        -> UI default: last 30 days (explicit days=30)
    days= (empty)      -> "All history": days param omitted to the metric
    days=30..365       -> trailing window
    symbols absent     -> UI default: first symbol ticked (AAPL preferred)
    symbols=           -> nothing ticked (empty chart)
    symbols=A,B        -> only those symbols
    threshold=N        -> binary threshold (only used when z=change_bin)

Run: source venv/bin/activate && python app_flask.py   # binds FTE_BIND_HOST from .env
"""
import os
import time

import pandas as pd
import plotly.express as px
from flask import Flask, jsonify, request

import logic_layer
from config import CFG

app = Flask(__name__, static_folder="static", static_url_path="/static")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

_cache = {"key": None, "html": None, "meta": {}, "ts": 0}
_CACHE_TTL = 300  # seconds

CHANNELS = ("z", "size", "color")
BINARY_MAX_THRESHOLD = 100  # slider bound; the metric itself accepts any int >= 0

_CHANNEL_COLUMNS = {
    "connected": {name: logic_layer.NUMERIC_COLUMNS for name in CHANNELS},
    # sampled: Z additionally offers the computed binary channel
    # (change_bin); Size/Color stay on the real metric columns.
    "sampled": {
        "z": logic_layer.SAMPLED_CHANNEL_COLUMNS,
        "size": logic_layer.SAMPLED_SIZE_COLUMNS,
        "color": logic_layer.SAMPLED_COLOR_COLUMNS,
    },
}
_CHANNEL_DEFAULTS = {
    "connected": logic_layer.DEFAULT_CHANNELS,
    "sampled": logic_layer.SAMPLED_DEFAULT_CHANNELS,
}


def _channel_args():
    """z/size/color channel selections from the query string (empty = default)."""
    return {name: (request.args.get(name) or "").strip() for name in CHANNELS}


def _request_view():
    """Resolve the URL's days + symbols into explicit values.

    Returns (days, symbols, symbols_explicit, source, threshold):
        days          int 1..365, or None for "all history" (omit param)
        symbols       list[str] of ticked symbols (None when URL had no
                      symbols param -> caller applies the first-symbol default)
        symbols_explicit  True when the URL carried a symbols param
        source        "connected" (price_history) or "sampled"
                      (sampled_market_data)
        threshold     int 0..BINARY_MAX_THRESHOLD (binary Z channel only)
    """
    raw_days = request.args.get("days")
    if raw_days is None:
        days = 30
    else:
        raw_days = raw_days.strip()
        if raw_days == "":
            days = None
        else:
            try:
                days = int(raw_days)
            except (TypeError, ValueError):
                days = 30
            days = min(max(days, 1), 365)

    raw_symbols = request.args.get("symbols")
    if raw_symbols is None:
        symbols = None
        symbols_explicit = False
    else:
        symbols = [s.strip().upper() for s in raw_symbols.split(",") if s.strip()]
        symbols_explicit = True

    source = (request.args.get("source") or "sampled").strip()
    if source not in logic_layer.SOURCES:
        source = "sampled"

    try:
        threshold = int((request.args.get("threshold") or "0").strip())
    except (TypeError, ValueError):
        threshold = 0
    threshold = min(max(threshold, 0), BINARY_MAX_THRESHOLD)

    return days, symbols, symbols_explicit, source, threshold


def _channel_options(source, name):
    """Server-rendered <option> list for one channel select, per source."""
    columns = _CHANNEL_COLUMNS[source][name]
    defaults = _CHANNEL_DEFAULTS[source]
    selected = (request.args.get(name) or "").strip().lower() or defaults[name]
    if selected not in columns:
        selected = defaults[name]
    return "\n".join(
        f'<option value="{col}"{" selected" if col == selected else ""}>{col}</option>'
        for col in columns
    )


def _symbol_options(universe, selected):
    """Checkbox labels; `selected` is the set of ticked symbols."""
    sel = set(selected)
    return "\n".join(
        f'<label class="sym-item"><input type="checkbox" value="{s}"'
        f'{" checked" if s in sel else ""}><span>{s}</span></label>'
        for s in universe
    )


def _chart_html(days, symbols, channels, source, threshold):
    """Build the 3D scatter figure HTML fragment from the market_3d
    envelope for the given (days, symbols, channels, source, threshold).
    TTL-cached per combo. Returns (html, meta) — meta carries the binary
    above/total counts when the binary Z channel is selected.

    days None = all history; symbols is always an explicit list here.
    """
    key = (source, days) + tuple(channels[name] for name in CHANNELS) + (tuple(symbols), threshold)
    now = time.time()
    if _cache["key"] == key and _cache["html"] is not None and now - _cache["ts"] < _CACHE_TTL:
        return _cache["html"], _cache["meta"]

    params = {"symbols": ",".join(symbols), "source": source}
    if days is not None:
        params["days"] = days
    params.update({name: channels[name] for name in CHANNELS if channels[name]})
    if channels["z"] == logic_layer.BINARY_CHANNEL:
        params["threshold"] = threshold
    envelope = logic_layer.handle_request("market_3d", params)

    meta = envelope.get("meta", {})
    if envelope["status"] == "error":
        html = f"<p>{' | '.join(envelope['meta'].get('errors', []))}</p>"
    elif envelope["status"] == "empty":
        html = f"<p>{envelope['meta'].get('message', 'No rows to display.')}</p>"
    else:
        chart = envelope["chart"]
        df = pd.DataFrame(envelope["rows"], columns=envelope["columns"])
        for col in df.columns:
            try:
                df[col] = pd.to_numeric(df[col])
            except (ValueError, TypeError):
                pass
        # Fixed-stage 3D (static/index.html rotates the DATA, not the camera):
        # string channels (date/symbol) are converted to category-index
        # columns here, at build time — the scene keeps fixed linear axes
        # with tickvals/ticktext labels, and the client rotation engine only
        # moves points, never axis types (a runtime category->linear switch
        # on a live gl3d scene blanks the plot).
        scene_titles = {"x": chart.get("scene", {}).get("x", "Symbol"),
                        "y": chart.get("scene", {}).get("y", "Trade Date"),
                        "z": chart.get("scene", {}).get("z", "Close ($)")}
        axis_updates = {}
        customdata_cols = []
        for name in ("x", "y", "z"):
            col = chart[name]
            customdata_cols.append(df[col].tolist())
            if pd.api.types.is_numeric_dtype(df[col]):
                continue
            unique = sorted(df[col].dropna().unique().tolist())
            df[col] = pd.Categorical(df[col], categories=unique).codes
            n = len(unique)
            # tick budget: dates (~10, readable) vs symbols (~40); always
            # include both endpoints
            is_dates = all(
                isinstance(v, str) and len(v) == 10 and v[4] == "-" and v[7] == "-"
                and v[:4].isdigit() and v[5:7].isdigit() and v[8:10].isdigit()
                for v in unique
            ) if unique else False
            budget = 10 if is_dates else 40
            stride = 1 if n <= budget else (n + budget - 1) // budget
            tickvals = list(range(0, n, stride))
            ticktext = [unique[i] for i in tickvals]
            if tickvals[-1] != n - 1:
                tickvals.append(n - 1)
                ticktext.append(unique[n - 1])
            axis_updates[f"{name}axis"] = dict(
                type="linear", tickmode="array",
                tickvals=tickvals, ticktext=ticktext,
            )
        fig = px.scatter_3d(
            df,
            x=chart["x"],
            y=chart["y"],
            z=chart["z"],
            size=chart.get("size"),
            hover_name=chart.get("hover"),
            opacity=chart.get("opacity", 0.6),
        )
        # Continuous color mapped here (not via px) — px's color= path is ~100x
        # slower at these point counts (25s+ for 20k rows).
        fig.data[0].marker.color = df[chart["color"]].to_numpy()
        fig.data[0].marker.colorscale = chart.get("colorscale", "RdYlGn")
        fig.data[0].marker.showscale = True
        fig.data[0].marker.colorbar = dict(title=chart.get("colorbar_title", ""))
        # hover: original labels per point (x/y/z/channel values survive the
        # index conversion above)
        fig.data[0].customdata = [[a, b, c] for a, b, c in zip(*customdata_cols)]
        fig.data[0].hovertemplate = (
            f"{scene_titles['x']}: %{{customdata[0]}}<br>"
            f"{scene_titles['y']}: %{{customdata[1]}}<br>"
            f"{scene_titles['z']}: %{{customdata[2]}}<extra></extra>"
        )
        scene = chart.get("scene") or {}
        fig.update_layout(
            scene=dict(
                xaxis_title=scene_titles["x"],
                yaxis_title=scene_titles["y"],
                zaxis_title=scene_titles["z"],
                camera=dict(eye=dict(x=0, y=0, z=2.5), up=dict(x=0, y=1, z=0)),
                aspectmode="manual",
                aspectratio=dict(x=2, y=1, z=1),
                **axis_updates,
            ),
            margin=dict(l=0, r=0, t=0, b=0),
        )
        html = fig.to_html(full_html=False, include_plotlyjs=False,
                           config={"displayModeBar": False})

    _cache["key"], _cache["html"], _cache["meta"], _cache["ts"] = key, html, meta, now
    return html, meta


def _binary_summary(meta):
    """One-line summary from the market_3d binary meta counts."""
    if "above" not in meta or "total" not in meta:
        return ""
    above = meta.get("above", 0)
    total = meta.get("total", 0)
    pct = f"{above / total * 100:.1f}%" if total else "0.0%"
    return f"{total:,} rows · {above:,} above threshold ({pct})"


@app.route("/")
def index():
    days, symbols, symbols_explicit, source, threshold = _request_view()
    universe = logic_layer.symbol_list(source)
    default_ticked = {"AAPL"} if "AAPL" in universe else ({universe[0]} if universe else set())
    if symbols is None:
        symbols = sorted(default_ticked)
    selected = set(symbols) if symbols_explicit else default_ticked
    channels = _channel_args()
    # Fill empty channel selections with the per-source defaults so the
    # chart, the cache key, and the binary slider/summary all reflect the
    # effective channels (e.g. sampled Z defaults to change_bin).
    for name in CHANNELS:
        if not channels[name]:
            channels[name] = _CHANNEL_DEFAULTS[source][name]
    with open(os.path.join(BASE_DIR, "static", "index.html"), encoding="utf-8") as fh:
        page = fh.read()
    page = page.replace("<!-- SYMBOLS -->", _symbol_options(universe, selected))
    for name in CHANNELS:
        page = page.replace(f"<!-- CHANNEL_{name.upper()} -->",
                            _channel_options(source, name))
    html, meta = _chart_html(days, symbols, channels, source, threshold)
    binary = channels["z"] == logic_layer.BINARY_CHANNEL
    page = page.replace("<!-- THRESHOLD_CLASS -->", "" if binary else " hidden")
    page = page.replace("<!-- THRESHOLD_VALUE -->", str(threshold))
    page = page.replace("<!-- SUMMARY -->", _binary_summary(meta) if binary else "")
    return page.replace("<!-- FIGURE -->", html)


@app.route("/api/config")
def api_config():
    return jsonify({
        "main_url": CFG["FTE_MAIN_URL"],
        "streamlit_url": CFG["FTE_STREAMLIT_URL"],
    })


if __name__ == "__main__":
    app.run(host=CFG["FTE_BIND_HOST"], port=5000, debug=False)
