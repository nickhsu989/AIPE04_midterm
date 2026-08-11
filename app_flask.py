"""app_flask.py — Flask main app (presentation only).

Routes:
    GET /                  -> 3D market chart page, rendered from the
                              logic_layer "market_3d" metric envelope
    GET /api/config        -> main/streamlit URLs (used by index.html)

All data logic (SQL, filtering, decimation, column -> chart-channel
mapping, symbol/days defaults) lives in logic_layer; this file only turns
the canonical envelope into a Plotly figure and serves index.html.

Binary view (integrated on the main page): with source=sampled the Z
dropdown offers the computed channel `change_y_bin` (0/1 flag from
change_y > threshold, computed at query time by market_3d). When it is
selected a threshold slider (0..BINARY_MAX_THRESHOLD) is shown and the
query param ?threshold=N is sent.

URL semantics:
    days absent        -> UI default: last 30 days (explicit days=30)
    days= (empty)      -> "All history": days param omitted to the metric
    days=30..365       -> trailing window
    symbols absent     -> UI default: first symbol ticked
    symbols=           -> nothing ticked (empty chart)
    symbols=A,B        -> only those symbols
    threshold=N        -> binary threshold (only used when z=change_y_bin)

Run: flask --app app_flask run
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
    # (change_y_bin); Size/Color stay on the real metric columns.
    "sampled": {
        "z": logic_layer.SAMPLED_CHANNEL_COLUMNS,
        "size": logic_layer.SAMPLED_NUMERIC_COLUMNS,
        "color": logic_layer.SAMPLED_NUMERIC_COLUMNS,
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

    source = (request.args.get("source") or "connected").strip()
    if source not in logic_layer.SOURCES:
        source = "connected"

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
        fig = px.scatter_3d(
            df,
            x=chart["x"],
            y=chart["y"],
            z=chart["z"],
            size=chart.get("size"),
            hover_name=chart.get("hover"),
            title=chart.get("title", "Technical Market Structure & Momentum"),
            opacity=chart.get("opacity", 0.6),
        )
        # Continuous color mapped here (not via px) — px's color= path is ~100x
        # slower at these point counts (25s+ for 20k rows).
        fig.data[0].marker.color = df[chart["color"]].to_numpy()
        fig.data[0].marker.colorscale = chart.get("colorscale", "RdYlGn")
        fig.data[0].marker.showscale = True
        fig.data[0].marker.colorbar = dict(title=chart.get("colorbar_title", ""))
        scene = chart.get("scene") or {}
        fig.update_layout(
            scene=dict(
                xaxis_title=scene.get("x", "Symbol"),
                yaxis_title=scene.get("y", "Trade Date"),
                zaxis_title=scene.get("z", "Close ($)"),
            ),
            margin=dict(l=0, r=0, t=60, b=0),
        )
        html = fig.to_html(full_html=False, include_plotlyjs=False)

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
    if symbols is None:
        symbols = [universe[0]] if universe else []
    selected = set(symbols) if symbols_explicit else ({universe[0]} if universe else set())
    channels = _channel_args()
    # Fill empty channel selections with the per-source defaults so the
    # chart, the cache key, and the binary slider/summary all reflect the
    # effective channels (e.g. sampled Z defaults to change_y_bin).
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
