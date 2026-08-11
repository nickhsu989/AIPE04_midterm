"""app_presenter.py — presentation helper shared by both UIs.

Maps a Logic Layer envelope to a Plotly figure. Generic by design:
chart types are limited to {line, bar, scatter, candlestick, table},
so no new metric ever requires UI changes.
"""
import pandas as pd
import plotly.graph_objects as go

CHART_TYPES = ("line", "bar", "scatter", "candlestick", "table")


def envelope_to_figure(envelope):
    """envelope -> plotly.graph_objects.Figure (or None if no chart data)."""
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
