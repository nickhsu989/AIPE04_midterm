"""logic_layer.py — THE Logic Layer (the brain).

Every registered metric produces a canonical envelope (docs/spec.md §6.2):
{
  "metric", "status", "title",
  "meta": {...},
  "chart": {"type", "x", "y", "z", "size", "hover", "color", ...},
  "columns": [...],
  "rows": [...]
}

`registered_metrics` holds exactly what the apps display today:
  - history     -> Streamlit dashboard (:8501)
  - market_3d   -> Flask main page 3D chart (:5000), NOT exposed as a
                   user-selectable metric; its `chart` metadata declares
                   how the returned MySQL columns map to the 3D scene.
                   With source=sampled, Z may be the computed binary
                   channel `change_bin` (0/1 from the `threshold` param).
  - change_binary -> standalone metric retained for direct calls; the
                   main page now exposes the same binary view via
                   `market_3d`'s `change_bin` Z channel (sampled source).

Window semantics (both metrics): `days` is ABSENCE-BASED — an absent,
non-positive or unparseable `days` means NO trailing window (full
history); a positive `days` means a trailing N-day window. The apps always
send an explicit UI default (30 days) except for "All history", which
omits `days`. `symbols` (market_3d only) is similarly absent = all
symbols; present-but-empty = no symbols.
"""
from datetime import datetime, timezone

import pandas as pd

import db

from config import CFG

# Cap the number of plotted points (logic-layer concern, not the UI's).
MAX_POINTS = 250_000

# Columns choosable as 3D chart channels on the main page.
#   x (symbol) and y (trade_date) are FIXED; z/size/color are numeric-only.
NUMERIC_COLUMNS = ("open", "high", "low", "close", "adj_close", "volume")
DEFAULT_CHANNELS = {"z": "close", "size": "volume", "color": "adj_close"}

# Binary Z channel (sampled mode only): not a real column — a computed
# 0/1 flag (change >= threshold) at query time, derived directly from
# sampled_market_data.change (the mirror table change_y_binary no longer
# exists; the sampled table is keyed by (symbol, date)).
BINARY_CHANNEL = "change_bin"

# Sampled-dataset mode (:5000 toggle `source=sampled`): the snapshot table
# sampled_market_data is keyed by (symbol, date); x = date, z = symbol.
# The Y axis carries the selectable channel and defaults to the binary
# channel (change_bin).
SAMPLED_NUMERIC_COLUMNS = (
    "market_cap", "52w_low", "prev_close", "price", "volume", "52w_high",
    "perf_ytd", "perf_year", "sma200", "perf_half_y", "avg_volume",
    "perf_quarter", "sma50", "perf_month", "sma20", "atr", "rsi_14",
    "perf_week", "rel_volume", "change",
)
SAMPLED_DEFAULT_CHANNELS = {"z": BINARY_CHANNEL, "size": "market_cap", "color": "perf_year"}
SOURCES = ("connected", "sampled")

# Whitelist config accessors. Only these keys are exposed to the apps — DB
# credentials (DB_*) never cross this boundary; they stay with db.py.
def get_urls():
    """Return the inter-app URLs (main + streamlit) for the UIs' nav links."""
    return {"main_url": CFG.get("FTE_MAIN_URL"), "streamlit_url": CFG.get("FTE_STREAMLIT_URL")}


def get_bind_host():
    """Return the host the Flask app should bind to."""
    return CFG.get("FTE_BIND_HOST", "127.0.0.1")

# Z-dropdown candidates for sampled mode: the real metric columns plus the
# computed binary channel. Size/Color stay on SAMPLED_NUMERIC_COLUMNS only.
SAMPLED_CHANNEL_COLUMNS = SAMPLED_NUMERIC_COLUMNS + (BINARY_CHANNEL,)

# Sampled Size/Color dropdown candidates (subsets of SAMPLED_NUMERIC_COLUMNS):
# Size = price/volume family — always non-negative (Plotly marker size
# requires values >= 0); Color = performance/trend family — may be negative
# (color scales handle negatives fine).
SAMPLED_SIZE_COLUMNS = (
    "market_cap", "volume", "avg_volume", "prev_close", "price",
    "52w_low", "52w_high", "atr", "rsi_14",
)
SAMPLED_COLOR_COLUMNS = (
    "perf_ytd", "perf_week", "perf_month", "perf_quarter", "perf_half_y",
    "perf_year", "sma20", "sma50", "sma200", "rel_volume", "change",
)

# ----------------------------------------------------------------------
# Registry
# ----------------------------------------------------------------------
registered_metrics = {}


def register(fn):
    """Decorator: registers a metric function under its slug."""
    registered_metrics[fn.__name__] = fn
    return fn


def symbol_list(source="connected"):
    """Every ingested symbol (instruments table), alphabetical — the
    symbol pool for the main-page tick/untick listbox.

    source="sampled" lists the sampled_market_data symbols instead
    (x-axis identity for the sampled dataset mode).
    """
    if source == "sampled":
        return [str(r["symbol"])
                for r in db.query("SELECT DISTINCT symbol "
                                  "FROM sampled_market_data "
                                  "ORDER BY symbol")]
    return [r["symbol"] for r in db.query("SELECT symbol FROM instruments ORDER BY symbol")]


# ----------------------------------------------------------------------
# Metric functions — pure (params) -> DataFrame. SQL only, no network.
# ----------------------------------------------------------------------
def _limit_clause(limit):
    """Return (sql_fragment, extra_params) — omit LIMIT when limit <= 0 (all rows)."""
    if limit and limit > 0:
        return "LIMIT %s", (limit,)
    return "", ()


def _window_clause(symbol, params):
    """Trailing-window filter fragment for a symbol from `days`
    (absence-based).

    Returns (sql_fragment, extra_params): a positive `days` yields the
    fragment + params (symbol, days); anything else yields an empty
    fragment (full history).
    """
    if "days" not in params:
        return "", ()
    try:
        days = int(params["days"])
    except (TypeError, ValueError):
        return "", ()
    if days <= 0:
        return "", ()
    return (
        "AND trade_date >= DATE_SUB((SELECT MAX(trade_date) FROM price_history WHERE symbol = %s), INTERVAL %s DAY)",
        (symbol, days),
    )


def _decimate(df, max_points):
    """Evenly decimate rows so large windows stay fast to serialize/plot."""
    if len(df) <= max_points:
        return df
    step = (len(df) + max_points - 1) // max_points
    return df.iloc[::step].reset_index(drop=True)


@register
def history(params):
    symbol = params.get("symbol", "AAPL").upper()
    limit = int(params.get("limit", 250))
    lim_sql, lim_params = _limit_clause(limit)
    window_sql, window_params = _window_clause(symbol, params)
    df = pd.DataFrame(db.query(
        f"""
        SELECT trade_date, open, high, low, close, adj_close, volume
        FROM price_history
        WHERE symbol = %s
        {window_sql}
        ORDER BY trade_date DESC
        {lim_sql}
        """,
        (symbol,) + window_params + lim_params,
    ))
    df = df.iloc[::-1].reset_index(drop=True)
    return df, "history", "line", "trade_date", "close", symbol


@register
def market_3d(params):
    """3D market-structure view for the Flask main page (internal metric).

    x = symbol and y = trade_date are fixed channels; z/size/color accept
    optional numeric columns (DEFAULT_CHANNELS fallback). `symbols`
    filters which symbols plot: absent = all, non-empty comma list = those,
    present-but-totally-empty = none. `days` is absence-based (see module
    docstring) — app passes 30 by default and omits it for "All history".

    `source=sampled` switches the query to sampled_market_data: x = symbol
    and y = date, channels validated against SAMPLED_NUMERIC_COLUMNS.
    """
    source = params.get("source", "connected")
    if source not in SOURCES:
        source = "connected"

    def _pick(param, default, candidates):
        value = (params.get(param) or "").strip().lower()
        return value if value in candidates else default

    if source == "sampled":
        return _market_3d_sampled(params, _pick)

    z = _pick("z", DEFAULT_CHANNELS["z"], NUMERIC_COLUMNS)
    size = _pick("size", DEFAULT_CHANNELS["size"], NUMERIC_COLUMNS)
    color = _pick("color", DEFAULT_CHANNELS["color"], NUMERIC_COLUMNS)

    symbols = None
    if "symbols" in params:
        symbols = [s.strip().upper() for s in params["symbols"].split(",") if s.strip()]
    if symbols == []:
        return (
            pd.DataFrame(), "market_3d", "scatter3d", "symbol", "trade_date", "no symbols",
            {"meta": {"message": "No symbols selected."}},
        )

    window_sql, window_params = "", ()
    window_label = "all history"
    if "days" in params:
        try:
            days = int(params["days"])
        except (TypeError, ValueError):
            days = 0
        if days > 0:
            days = min(days, 365)
            window_sql = "AND trade_date >= DATE_SUB((SELECT MAX(trade_date) FROM price_history), INTERVAL %s DAY)"
            window_params = (days,)
            window_label = f"{days} days"

    try:
        max_points = int(params.get("max_points", MAX_POINTS)) or MAX_POINTS
    except (TypeError, ValueError):
        max_points = MAX_POINTS

    sym_sql, sym_params = "", ()
    if symbols is not None:
        placeholders = ", ".join(["%s"] * len(symbols))
        sym_sql = f"AND symbol IN ({placeholders})"
        sym_params = tuple(symbols)

    rows = db.query(
        f"""
        SELECT symbol, trade_date, open, high, low, close, adj_close, volume
        FROM price_history
        WHERE 1=1
        {window_sql}
        {sym_sql}
        ORDER BY trade_date
        """,
        window_params + sym_params,
    )
    df = pd.DataFrame(rows)
    if df.empty:
        if window_sql:
            message = f"No rows in the last {days} days."
        elif symbols is not None:
            message = "No rows for the selected symbols."
        else:
            message = "No data ingested yet. Run: python ingest.py, python load_staging.py, python load_close_open_ratio.py"
        return (
            pd.DataFrame(), "market_3d", "scatter3d", "symbol", "trade_date", window_label,
            {"meta": {"message": message}},
        )

    df["symbol"] = df["symbol"].astype(str)
    df["trade_date"] = df["trade_date"].astype(str)
    df = df.dropna(subset=["close", "adj_close", "volume"])
    df = df[(df["volume"] > 0) & (df["close"] > 0)]
    df = _decimate(df, max_points)

    chart = {
        "type": "scatter3d",
        "x": "symbol",             # fixed MySQL column -> scatter x axis
        "y": "trade_date",         # fixed MySQL column -> scatter y axis
        "z": z,                    # MySQL column -> scatter z axis
        "size": size,              # MySQL column -> marker size
        "hover": "symbol",         # MySQL column -> hover label
        "color": color,            # MySQL column -> continuous marker color
        "colorscale": "RdYlGn",
        "colorbar_title": f"{color}",
        "opacity": 0.6,
        "scene": {"x": "Symbol", "y": "Trade Date", "z": z},
    }
    return df, "market_3d", "scatter3d", "symbol", "trade_date", window_label, {"chart": chart}


def _market_3d_sampled(params, _pick):
    """sampled_market_data variant of market_3d: x = date, z = symbol.

    Shares the absence-based `days` / `symbols` window semantics with the
    connected variant; `symbols` here are ticker symbols (strings matching
    the tick/untick checkbox values).

    Z may be the computed binary channel (BINARY_CHANNEL = "change_bin"):
    the flag is derived at QUERY TIME from the `threshold` param
    (int >= 0, negative or unparseable falls back to 0) via
    `CASE WHEN s.change >= %s THEN 1 ELSE 0 END` — computed directly from
    sampled_market_data.change (the change_y_binary mirror table no longer
    exists). When the binary channel is selected, `meta` carries
    above/total counts for the page summary line.
    """
    z = _pick("z", SAMPLED_DEFAULT_CHANNELS["z"], SAMPLED_CHANNEL_COLUMNS)
    size = _pick("size", SAMPLED_DEFAULT_CHANNELS["size"], SAMPLED_SIZE_COLUMNS)
    color = _pick("color", SAMPLED_DEFAULT_CHANNELS["color"], SAMPLED_NUMERIC_COLUMNS)

    binary = z == BINARY_CHANNEL
    try:
        threshold = int(params.get("threshold", 0))
    except (TypeError, ValueError):
        threshold = 0
    if threshold < 0:
        threshold = 0

    symbols = None
    if "symbols" in params:
        symbols = [s.strip().upper() for s in params["symbols"].split(",") if s.strip()]
    if symbols == []:
        return (
            pd.DataFrame(), "market_3d", "scatter3d", "date", "symbol", "no symbols",
            {"meta": {"message": "No symbols selected."}},
        )

    window_sql, window_params = "", ()
    window_label = "all history"
    if "days" in params:
        try:
            days = int(params["days"])
        except (TypeError, ValueError):
            days = 0
        if days > 0:
            days = min(days, 365)
            window_sql = ("AND s.`date` >= DATE_SUB((SELECT MAX(`date`) "
                          "FROM sampled_market_data), INTERVAL %s DAY)")
            window_params = (days,)
            window_label = f"{days} days"

    try:
        max_points = int(params.get("max_points", MAX_POINTS)) or MAX_POINTS
    except (TypeError, ValueError):
        max_points = MAX_POINTS

    sym_sql, sym_params = "", ()
    if symbols is not None:
        placeholders = ", ".join(["%s"] * len(symbols))
        sym_sql = f"AND s.symbol IN ({placeholders})"
        sym_params = tuple(symbols)

    if binary:
        rows = db.query(
            f"""
            SELECT s.symbol, s.`date`,
                   {", ".join(f"s.`{c}`" for c in SAMPLED_NUMERIC_COLUMNS)},
                   CASE WHEN s.change >= %s THEN 1 ELSE 0 END AS {BINARY_CHANNEL}
            FROM sampled_market_data s
            WHERE 1=1
            {window_sql}
            {sym_sql}
            ORDER BY s.`date`
            """,
            (threshold,) + window_params + sym_params,
        )
    else:
        rows = db.query(
            f"""
            SELECT s.symbol, s.`date`, {", ".join(f"s.`{c}`" for c in SAMPLED_NUMERIC_COLUMNS)}
            FROM sampled_market_data s
            WHERE 1=1
            {window_sql}
            {sym_sql}
            ORDER BY s.`date`
            """,
            window_params + sym_params,
        )
    df = pd.DataFrame(rows)
    if df.empty:
        if window_sql:
            message = f"No rows in the last {days} days."
        elif symbols is not None:
            message = "No rows for the selected symbols."
        else:
            message = "No data in sampled_market_data yet. Run: venv/bin/python load_sampled.py"
        return (
            pd.DataFrame(), "market_3d", "scatter3d", "date", "symbol", window_label,
            {"meta": {"message": message}},
        )

    chart = {
        "type": "scatter3d",
        "x": "date",             # snapshot date -> scatter x axis (time)
        "y": z,                  # selectable channel -> scatter y axis
        "z": "symbol",           # sampled identity -> scatter z axis (depth)
        "size": size,
        "hover": "symbol",
        "color": color,
        "colorscale": "RdYlGn",
        "colorbar_title": f"{color}",
        "opacity": 0.6,
        "scene": {"x": "Date", "y": z, "z": "Symbol"},
    }
    df["symbol"] = df["symbol"].astype(str)
    df["date"] = df["date"].astype(str)
    df = df.dropna(subset=[z, size, color])
    df = df[df["price"] > 0]
    extras = {"chart": chart}
    if binary:
        extras["meta"] = {"above": int((df[BINARY_CHANNEL] > 0.5).sum()),
                          "total": len(df)}
    df = _decimate(df, max_points)

    return df, "market_3d", "scatter3d", "date", "symbol", window_label, extras


@register
def change_binary(params):
    """Standalone binary view over sampled_market_data.change (retained
    for direct calls; the main page shows the same view via market_3d's
    `change_bin` Z channel).

    Converts change into a 0/1 flag at QUERY TIME from the `threshold`
    param: change >= threshold -> 1, else 0. Threshold is an int >= 0
    (negative or unparseable values fall back to 0). Optional `symbols`
    (ticker symbols) filter and absence-based `days` trailing window; `limit`
    caps the returned rows (0 = all, decimated to MAX_POINTS).

    `meta` carries the above-threshold count for the page summary.
    """
    try:
        threshold = int(params.get("threshold", 0))
    except (TypeError, ValueError):
        threshold = 0
    if threshold < 0:
        threshold = 0

    symbols = None
    if "symbols" in params:
        symbols = [s.strip().upper() for s in params["symbols"].split(",") if s.strip()]
    if symbols == []:
        return (
            pd.DataFrame(), "change_binary", "scatter3d", "symbol", "date", "no symbols",
            {"meta": {"message": "No symbols selected."}},
        )

    window_sql, window_params = "", ()
    if "days" in params:
        try:
            days = int(params["days"])
        except (TypeError, ValueError):
            days = 0
        if days > 0:
            days = min(days, 365)
            window_sql = ("AND `date` >= DATE_SUB((SELECT MAX(`date`) "
                          "FROM sampled_market_data), INTERVAL %s DAY)")
            window_params = (days,)

    try:
        limit = int(params.get("limit", 0))
    except (TypeError, ValueError):
        limit = 0

    sym_sql, sym_params = "", ()
    if symbols is not None:
        placeholders = ", ".join(["%s"] * len(symbols))
        sym_sql = f"AND symbol IN ({placeholders})"
        sym_params = tuple(symbols)

    lim_sql, lim_params = _limit_clause(limit)

    rows = db.query(
        f"""
        SELECT symbol, `date`, `change`,
               CASE WHEN `change` >= %s THEN 1 ELSE 0 END AS change_bin
        FROM sampled_market_data
        WHERE 1=1
        {window_sql}
        {sym_sql}
        ORDER BY symbol, `date`
        {lim_sql}
        """,
        (threshold,) + window_params + sym_params + lim_params,
    )
    df = pd.DataFrame(rows)
    title_suffix = f"threshold={threshold}"
    if df.empty:
        return (
            pd.DataFrame(), "change_binary", "scatter3d", "symbol", "date",
            title_suffix, {"meta": {"message": "No rows match the current settings."}},
        )

    above = int((df["change"] >= threshold).sum())
    total = len(df)
    df["symbol"] = df["symbol"].astype(str)
    df["date"] = df["date"].astype(str)
    df = _decimate(df, MAX_POINTS)

    chart = {
        "type": "scatter3d",
        "x": "symbol",           # sampled identity -> scatter x axis
        "y": "date",             # snapshot date -> scatter y axis
        "z": "change",           # raw value -> scatter z axis
        "hover": "symbol",
        "color": "change_bin",   # 0/1 flag -> continuous marker color
        "colorscale": "RdYlGn",
        "colorbar_title": "change_bin",
        "opacity": 0.6,
        "title": f"Change Binary — threshold={threshold}",
        "scene": {"x": "Symbol", "y": "Date", "z": "Change (%)"},
    }
    return (df, "change_binary", "scatter3d", "symbol", "date", title_suffix,
            {"chart": chart, "meta": {"above": above, "total": total}})


# ----------------------------------------------------------------------
# Envelope handling
# ----------------------------------------------------------------------
def _to_json_safe(df):
    """Turn a DataFrame into JSON-safe columns + rows (dates -> iso strings)."""
    if df is None or df.empty:
        return [], []
    df = df.copy()
    for col in df.columns:
        if pd.api.types.is_datetime64_any_dtype(df[col]):
            df[col] = df[col].astype(str)
    columns = [str(c) for c in df.columns]
    rows = [list(map(_scalar, row)) for row in df.itertuples(index=False, name=None)]
    return columns, rows


def _scalar(value):
    if value is None or isinstance(value, (int, float, str, bool)):
        return value
    return str(value)


def handle_request(metric, params=None):
    """Validate the slug, dispatch, and return the canonical envelope.

    A metric may return an optional 7th tuple element, a dict with
    "meta" and/or "chart" keys that are merged into the envelope (used
    by market_3d to carry empty-state messages and full chart metadata).

    Never raises — every failure becomes an error envelope.
    """
    params = params or {}
    if metric not in registered_metrics:
        return {
            "metric": metric,
            "status": "error",
            "title": "Unknown metric",
            "meta": {"errors": [f"metric '{metric}' is not registered"], "params": params,
                     "generated_at": datetime.now(timezone.utc).isoformat()},
            "chart": {},
            "columns": [],
            "rows": [],
        }
    try:
        fn = registered_metrics[metric]
        result = fn(params)
        extras = {}
        if isinstance(result, tuple):
            df, metric_name, chart_type, x_col, y_col, title_suffix = result[:6]
            if len(result) > 6 and result[6]:
                extras = result[6]
        else:
            df = result
            metric_name, chart_type, x_col, y_col, title_suffix = metric, "line", "", "", ""
        columns, rows = _to_json_safe(df)
        title = f"{metric} — {title_suffix}" if title_suffix else metric
        envelope = {
            "metric": metric_name,
            "status": "ok" if rows else "empty",
            "title": title,
            "meta": {"params": params, "rows": len(rows),
                     "generated_at": datetime.now(timezone.utc).isoformat()},
            "chart": {"type": chart_type, "x": x_col, "y": y_col, "color": None},
            "columns": columns,
            "rows": rows,
        }
        if extras.get("meta"):
            envelope["meta"].update(extras["meta"])
        if extras.get("chart"):
            envelope["chart"].update(extras["chart"])
        return envelope
    except Exception as exc:  # noqa: BLE001 — contract: never raise to the UI
        return {
            "metric": metric,
            "status": "error",
            "title": "Metric failed",
            "meta": {"errors": [f"{type(exc).__name__}: {exc}"], "params": params,
                     "generated_at": datetime.now(timezone.utc).isoformat()},
            "chart": {},
            "columns": [],
            "rows": [],
        }