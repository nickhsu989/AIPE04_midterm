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
                   Z may be the computed binary channel `change_bin`
                   (0/1 from the `threshold` param, derived from
                   `change` at query time).
  - change_binary -> standalone metric retained for direct calls; the
                   main page now exposes the same binary view via
                   `market_3d`'s `change_bin` Z channel.

All metrics read the UNIFIED table `unified_market_data` (built by
unify_databases.py: the inner join of price_history x sampled_market_data
on (symbol, date) — 4,434 tickers over 251 dates, 2024-11-18 ..
2025-11-18). The former two-source toggle (connected/sampled) is gone;
channel dropdowns carry -yf / -fin suffixes (see CHANNEL_LABELS) to show
which source family each column came from.

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
# The unified table holds both source families: yfinance OHLCV (from
# price_history) and the sample.csv snapshot metrics (from
# sampled_market_data); the only name collision (volume) is disambiguated
# as volume_yf / volume_fin.
YF_COLUMNS = ("open", "high", "low", "close", "adj_close", "volume_yf")
FIN_COLUMNS = (
    "market_cap", "52w_low", "prev_close", "price", "volume_fin", "52w_high",
    "perf_ytd", "perf_year", "sma200", "perf_half_y", "avg_volume",
    "perf_quarter", "sma50", "perf_month", "sma20", "atr", "rsi_14",
    "perf_week", "rel_volume", "change",
)
UNIFIED_NUMERIC_COLUMNS = YF_COLUMNS + FIN_COLUMNS
DEFAULT_CHANNELS = {"z": "close", "size": "market_cap", "color": "perf_year"}

# Binary Z channel: not a real column — a computed 0/1 flag
# (change >= threshold) at query time, derived directly from
# unified_market_data.change.
BINARY_CHANNEL = "change_bin"

# Whitelist config accessors. Only these keys are exposed to the apps — DB
# credentials (DB_*) never cross this boundary; they stay with db.py.
def get_urls():
    """Return the inter-app URLs (main + streamlit) for the UIs' nav links."""
    return {"main_url": CFG.get("FTE_MAIN_URL"), "streamlit_url": CFG.get("FTE_STREAMLIT_URL")}


def get_bind_host():
    """Return the host the Flask app should bind to."""
    return CFG.get("FTE_BIND_HOST", "127.0.0.1")

# Z-dropdown candidates: the real metric columns plus the computed binary
# channel.
CHANNEL_COLUMNS = UNIFIED_NUMERIC_COLUMNS + (BINARY_CHANNEL,)

# Channel dropdown labels: -yf = yfinance (price_history) family,
# -fin = sample.csv (sampled_market_data) family. Values stay clean
# column names; only the rendered option text carries the suffix.
CHANNEL_LABELS = {
    **{c: f"{c}-yf" for c in YF_COLUMNS},
    **{c: f"{c}-fin" for c in FIN_COLUMNS},
    # the disambiguated volume pair: the suffix replaces the underscore
    # instead of being appended ("volume-yf", not "volume_yf-yf").
    "volume_yf": "volume-yf",
    "volume_fin": "volume-fin",
}

# Size/Color dropdown candidates (subsets of UNIFIED_NUMERIC_COLUMNS):
# Size = OHLCV + price/volume family — always non-negative (Plotly marker
# size requires values >= 0); Color = OHLCV + performance/trend family —
# may be negative (color scales handle negatives fine).
SIZE_COLUMNS = (
    "open", "high", "low", "close", "adj_close", "volume_yf",
    "market_cap", "volume_fin", "avg_volume", "prev_close", "price",
    "52w_low", "52w_high", "atr", "rsi_14",
)
COLOR_COLUMNS = (
    "open", "high", "low", "close", "adj_close", "volume_yf",
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


def symbol_list():
    """Every symbol in the unified dataset, alphabetical — the symbol
    pool for the main-page tick/untick listbox and the :8501 Symbol
    dropdown. Only tickers present in BOTH source tables appear.
    """
    return [r["symbol"] for r in db.query(
        "SELECT DISTINCT symbol FROM unified_market_data ORDER BY symbol")]


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
        "AND trade_date >= DATE_SUB((SELECT MAX(trade_date) FROM unified_market_data WHERE symbol = %s), INTERVAL %s DAY)",
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
    """2D history view for the Streamlit dashboard (:8501).

    Returns the full unified row (trade_date + all UNIFIED_NUMERIC_COLUMNS),
    so the :8501 data column dropdown can plot any dataset column — the
    same dataset the :5000 3D cloud reads. `y` (optional) picks the
    plotted column (validated against UNIFIED_NUMERIC_COLUMNS, default
    "close") and becomes the chart's y channel.
    """
    symbol = params.get("symbol", "AMD").upper()
    y = (params.get("y") or "").strip().lower()
    if y not in UNIFIED_NUMERIC_COLUMNS:
        y = "close"
    limit = int(params.get("limit", 250))
    lim_sql, lim_params = _limit_clause(limit)
    window_sql, window_params = _window_clause(symbol, params)
    df = pd.DataFrame(db.query(
        f"""
        SELECT trade_date,
               {", ".join(f"`{c}`" for c in UNIFIED_NUMERIC_COLUMNS)}
        FROM unified_market_data
        WHERE symbol = %s
        {window_sql}
        ORDER BY trade_date DESC
        {lim_sql}
        """,
        (symbol,) + window_params + lim_params,
    ))
    df = df.iloc[::-1].reset_index(drop=True)
    return df, "history", "line", "trade_date", y, symbol


@register
def market_3d(params):
    """3D market-structure view for the Flask main page (internal metric).

    x = trade_date (time) and z = symbol (depth) are fixed channels; y
    carries the selectable channel (the page's Z dropdown maps to the
    vertical axis, matching the former sampled-dataset default view).
    z/size/color accept optional numeric columns (DEFAULT_CHANNELS
    fallback). `symbols` filters which symbols plot: absent = all,
    non-empty comma list = those, present-but-totally-empty = none.
    `days` is absence-based (see module docstring) — app passes 30 by
    default and omits it for "All history".

    Z may be the computed binary channel (BINARY_CHANNEL = "change_bin"):
    the flag is derived at QUERY TIME from the `threshold` param
    (int >= 0, negative or unparseable falls back to 0) via
    `CASE WHEN change >= %s THEN 1 ELSE 0 END` — computed directly from
    unified_market_data.change. When the binary channel is selected,
    `meta` carries above/total counts for the page summary line.
    """
    def _pick(param, default, candidates):
        value = (params.get(param) or "").strip().lower()
        return value if value in candidates else default

    z = _pick("z", DEFAULT_CHANNELS["z"], CHANNEL_COLUMNS)
    size = _pick("size", DEFAULT_CHANNELS["size"], SIZE_COLUMNS)
    color = _pick("color", DEFAULT_CHANNELS["color"], COLOR_COLUMNS)

    binary = z == BINARY_CHANNEL
    try:
        threshold = int(params.get("threshold", 0))
    except (TypeError, ValueError):
        threshold = 0
    threshold = max(threshold, 0)

    symbols = None
    if "symbols" in params:
        symbols = [s.strip().upper() for s in params["symbols"].split(",") if s.strip()]
    if symbols == []:
        return (
            pd.DataFrame(), "market_3d", "scatter3d", "trade_date", "symbol", "no symbols",
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
            window_sql = "AND trade_date >= DATE_SUB((SELECT MAX(trade_date) FROM unified_market_data), INTERVAL %s DAY)"
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

    bin_sql, bin_params = "", ()
    if binary:
        bin_sql = f", CASE WHEN `change` >= %s THEN 1 ELSE 0 END AS {BINARY_CHANNEL}"
        bin_params = (threshold,)

    rows = db.query(
        f"""
        SELECT symbol, trade_date,
               {", ".join(f"`{c}`" for c in UNIFIED_NUMERIC_COLUMNS)}
               {bin_sql}
        FROM unified_market_data
        WHERE 1=1
        {window_sql}
        {sym_sql}
        ORDER BY trade_date
        """,
        bin_params + window_params + sym_params,
    )
    df = pd.DataFrame(rows)
    if df.empty:
        if window_sql:
            message = f"No rows in the last {days} days."
        elif symbols is not None:
            message = "No rows for the selected symbols."
        else:
            message = ("No data ingested yet. Run: python ingest.py, python load_staging.py, "
                       "python load_close_open_ratio.py, python unify_databases.py")
        return (
            pd.DataFrame(), "market_3d", "scatter3d", "trade_date", "symbol", window_label,
            {"meta": {"message": message}},
        )

    df["symbol"] = df["symbol"].astype(str)
    df["trade_date"] = df["trade_date"].astype(str)
    df = df.dropna(subset=[z, size, color])
    df = df[df["price"] > 0]
    df = _decimate(df, max_points)

    chart = {
        "type": "scatter3d",
        "x": "trade_date",         # unified date -> scatter x axis (time)
        "y": z,                    # selectable channel -> scatter y axis
        "z": "symbol",             # unified identity -> scatter z axis (depth)
        "size": size,              # MySQL column -> marker size
        "hover": "symbol",         # MySQL column -> hover label
        "color": color,            # MySQL column -> continuous marker color
        "colorscale": "RdYlGn",
        "colorbar_title": f"{color}",
        "opacity": 0.6,
        "scene": {"x": "Date", "y": z, "z": "Symbol"},
    }
    extras = {"chart": chart}
    if binary:
        extras["meta"] = {"above": int((df[BINARY_CHANNEL] > 0.5).sum()),
                          "total": len(df)}
    return df, "market_3d", "scatter3d", "trade_date", "symbol", window_label, extras


@register
def change_binary(params):
    """Standalone binary view over unified_market_data.change (retained
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
            pd.DataFrame(), "change_binary", "scatter3d", "symbol", "trade_date", "no symbols",
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
            window_sql = ("AND `trade_date` >= DATE_SUB((SELECT MAX(`trade_date`) "
                          "FROM unified_market_data), INTERVAL %s DAY)")
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
        SELECT symbol, `trade_date`, `change`,
               CASE WHEN `change` >= %s THEN 1 ELSE 0 END AS change_bin
        FROM unified_market_data
        WHERE 1=1
        {window_sql}
        {sym_sql}
        ORDER BY symbol, `trade_date`
        {lim_sql}
        """,
        (threshold,) + window_params + sym_params + lim_params,
    )
    df = pd.DataFrame(rows)
    title_suffix = f"threshold={threshold}"
    if df.empty:
        return (
            pd.DataFrame(), "change_binary", "scatter3d", "symbol", "trade_date",
            title_suffix, {"meta": {"message": "No rows match the current settings."}},
        )

    above = int((df["change"] >= threshold).sum())
    total = len(df)
    df["symbol"] = df["symbol"].astype(str)
    df["trade_date"] = df["trade_date"].astype(str)
    df = _decimate(df, MAX_POINTS)

    chart = {
        "type": "scatter3d",
        "x": "symbol",           # unified identity -> scatter x axis
        "y": "trade_date",       # unified date -> scatter y axis
        "z": "change",           # raw value -> scatter z axis
        "hover": "symbol",
        "color": "change_bin",   # 0/1 flag -> continuous marker color
        "colorscale": "RdYlGn",
        "colorbar_title": "change_bin",
        "opacity": 0.6,
        "title": f"Change Binary — threshold={threshold}",
        "scene": {"x": "Symbol", "y": "Trade Date", "z": "Change (%)"},
    }
    return (df, "change_binary", "scatter3d", "symbol", "trade_date", title_suffix,
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
        prefix = "Symbol" if metric == "history" else metric
        title = f"{prefix} — {title_suffix}" if title_suffix else metric
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