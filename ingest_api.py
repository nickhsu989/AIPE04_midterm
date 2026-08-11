"""ingest_api.py — yfinance API ingestion pipeline.

Process sequence (docs/spec.md §5.1):
  1. fetch OHLCV from yfinance (requests under the hood)
  2. normalize into a pandas DataFrame
  3. numpy cleaning pass (numeric coercion, NaN policy)
  4. serialize to a staging CSV
  5. bulk-insert the staging CSV into MySQL
  6. upsert the instrument row
  7. write one ingest_log row

Usage:
    python ingest_api.py --symbol AAPL --period 1y [--interval 1d]
"""
import argparse
import os
import time

import numpy as np
import pandas as pd
import yfinance as yf

import db
from config import CFG

TARGET_COLUMNS = ["symbol", "trade_date", "open", "high", "low", "close",
                  "adj_close", "volume"]


def fetch_history(symbol, period, interval):
    """Download OHLCV history; returns (DataFrame or None, error message)."""
    try:
        raw = yf.download(symbol, period=period, interval=interval,
                          threads=False, progress=False, auto_adjust=False)
    except Exception as exc:  # noqa: BLE001
        return None, f"download raised {type(exc).__name__}: {exc}"
    if raw is None or raw.empty:
        return None, "no data returned (invalid symbol or empty range)"
    return raw, None


def clean(raw, symbol):
    """pandas/numpy cleaning pass -> clean DataFrame."""
    if isinstance(raw.columns, pd.MultiIndex):
        raw.columns = raw.columns.get_level_values(0)
    df = raw.reset_index()
    df.columns = [str(c).strip().lower() for c in df.columns]
    df = df.rename(columns={"date": "trade_date", "adj close": "adj_close"})
    df["symbol"] = symbol.upper()
    for col in ["open", "high", "low", "close", "adj_close", "volume"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    # numpy pass: coerce the whole matrix to float, forward-fill, drop dead rows
    numeric = df[["open", "high", "low", "close", "adj_close", "volume"]].to_numpy(dtype=np.float64)
    numeric = pd.DataFrame(numeric, columns=["open", "high", "low", "close", "adj_close", "volume"])
    numeric = numeric.ffill()
    df = df[["symbol", "trade_date"]].join(numeric)
    df = df.dropna(subset=["close"])
    df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.date
    return df


def main():
    parser = argparse.ArgumentParser(description="yfinance ingestion pipeline")
    parser.add_argument("--symbol", required=True, help="any valid yfinance symbol, e.g. AAPL or ^GSPC")
    parser.add_argument("--period", default="1y", help="e.g. 1d, 5d, 1mo, 1y, max")
    parser.add_argument("--interval", default="1d", help="e.g. 1d, 1wk, 1mo")
    args = parser.parse_args()

    symbol = args.symbol.strip().upper()
    os.makedirs(CFG["FTE_STAGING_DIR"], exist_ok=True)

    # 1. download (with one retry on rate-limit/transient failure)
    raw, error = fetch_history(symbol, args.period, args.interval)
    if error:
        print(f"[retry] {error}")
        time.sleep(3)
        raw, error = fetch_history(symbol, args.period, args.interval)
    if error:
        db.log_ingest("api", symbol, error, 0, "error")
        print(f"ERROR: {error}")
        return 1

    # 2+3. clean
    df = clean(raw, symbol)
    if df.empty:
        db.log_ingest("api", symbol, "empty after cleaning", 0, "error")
        print("ERROR: empty after cleaning")
        return 1

    # 4. serialize to staging CSV
    staging_path = os.path.join(CFG["FTE_STAGING_DIR"], f"{symbol}_{args.period}.csv")
    df.to_csv(staging_path, index=False)

    # 5. bulk-insert into MySQL (parent instrument row FIRST — FK constraint)
    staged = pd.read_csv(staging_path, parse_dates=["trade_date"])
    staged["trade_date"] = pd.to_datetime(staged["trade_date"]).dt.date
    rows = [tuple(r) for r in staged[TARGET_COLUMNS].itertuples(index=False, name=None)]
    try:
        db.insert_rows("instruments",
                       ["symbol", "asset_type", "last_sync"],
                       [(symbol, "index" if symbol.startswith("^") else "equity", pd.Timestamp.now().to_pydatetime())])
        written = db.insert_rows("price_history", TARGET_COLUMNS, rows)
    except Exception as exc:  # noqa: BLE001
        db.log_ingest("api", symbol, f"{type(exc).__name__}: {exc}", 0, "error")
        print(f"ERROR inserting: {exc}")
        return 1

    # 6. log
    db.log_ingest("api", symbol, staging_path, written, "ok")
    print(f"OK: {symbol} -> {written} rows (staging: {staging_path})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
