"""ingest.py — CSV-only bulk export: full-history (max period) daily data for
every symbol in a check_exist CSV, written to data/staging/.

Replaces ingest_api.py + ingest_universe.py:
  - never touches MySQL (loading is load_staging.py / load_close_open_ratio.py's job)
  - serial loop, one symbol at a time, then a fixed sleep
    (default 1s = 1 req/s, well under Yahoo's ~2 req/s rate cap)
  - per-symbol CSV is written immediately to --outdir = the checkpoint
  - resume = scan outdir for "<SYMBOL>_<period>.csv": existing files are
    skipped, except the newest (likely killed mid-write) which is re-run
  - download-time failures are appended to data/check_exist/ingest_failures.csv
    (audit ledger, best-effort — never crashes the run)

Usage:
  python ingest.py                          # every symbol in verify_ok.csv, max period
  python ingest.py --max 5                  # smoke test (first 5 remaining)
  python ingest.py --period 5y              # shorter range if you prefer
"""
import argparse
import csv
import os
import time
from datetime import datetime

import numpy as np
import pandas as pd
import yfinance as yf

COLUMNS = ["symbol", "trade_date", "open", "high", "low", "close",
           "adj_close", "volume"]
FAILURES_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "data", "check_exist", "ingest_failures.csv")


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


def record_failure(symbol, period, reason):
    """Append one failure row to the audit ledger; never crashes."""
    try:
        if not os.path.exists(FAILURES_FILE) or os.path.getsize(FAILURES_FILE) == 0:
            with open(FAILURES_FILE, "w", newline="") as fh:
                csv.writer(fh).writerow(["symbol", "period", "reason", "ts"])
        with open(FAILURES_FILE, "a", newline="") as fh:
            csv.writer(fh).writerow(
                [symbol, period, reason, datetime.now().isoformat(timespec="seconds")])
    except OSError as exc:
        print(f"WARN: could not record failure in {FAILURES_FILE}: {exc}", flush=True)


def load_symbols(path):
    """Uppercase symbol list from the check_exist CSV (skips header/empty)."""
    raw = pd.read_csv(path, dtype=str, keep_default_na=False)
    col = "symbol" if "symbol" in raw.columns else raw.columns[0]
    syms = raw[col].str.strip().str.upper()
    return sorted({s for s in syms if s and s != "SYMBOL"})


def done_symbols(outdir, period):
    """Symbols already exported for this period, except the newest file."""
    suffix = f"_{period}.csv"
    staged = []
    for name in os.listdir(outdir):
        if not name.endswith(suffix):
            continue
        path = os.path.join(outdir, name)
        staged.append((name[: -len(suffix)], os.path.getmtime(path)))
    if not staged:
        return set()
    staged.sort(key=lambda item: item[1])
    return {sym for sym, _ in staged[:-1]}  # newest is re-run


def export_one(outdir, symbol, period, interval, delay):
    """Fetch, clean, and write one symbol's CSV to outdir (the checkpoint)."""
    raw, error = fetch_history(symbol, period, interval)
    if error:
        print(f"[retry] {error}", flush=True)
        time.sleep(3)
        raw, error = fetch_history(symbol, period, interval)
    if error:
        record_failure(symbol, period, error)
        print(f"ERROR: {symbol} -> {error}", flush=True)
        time.sleep(delay)
        return
    df = clean(raw, symbol)
    if df.empty:
        record_failure(symbol, period, "empty after cleaning")
        print(f"ERROR: {symbol} -> empty after cleaning", flush=True)
        time.sleep(delay)
        return
    path = os.path.join(outdir, f"{symbol}_{period}.csv")
    df[COLUMNS].to_csv(path, index=False)
    print(f"OK: {symbol} -> {len(df)} rows ({path})", flush=True)
    time.sleep(delay)


def main():
    parser = argparse.ArgumentParser(
        description="bulk export full-history data for every check_exist symbol (CSV-only)")
    parser.add_argument("--file", default="data/check_exist/verify_ok.csv")
    parser.add_argument("--period", default="max",
                        help="yfinance period, e.g. 1y, 5y, 10y, max (default)")
    parser.add_argument("--interval", default="1d")
    parser.add_argument("--outdir", default="data/staging",
                        help="destination directory (loaders read this by default)")
    parser.add_argument("--delay", type=float, default=1.0,
                        help="seconds between symbols (keeps us under the rate cap)")
    parser.add_argument("--max", type=int, default=0,
                        help="stop after N symbols (smoke test)")
    args = parser.parse_args()

    os.makedirs(args.outdir, exist_ok=True)
    if args.period != "max":
        print(f"note: files written as {{SYMBOL}}_{args.period}.csv; loaders default to "
              f"--suffix max — load with: python load_staging.py --suffix {args.period} && "
              f"python load_close_open_ratio.py --suffix {args.period}", flush=True)
    todo = [s for s in load_symbols(args.file) if s not in done_symbols(args.outdir, args.period)]
    if args.max:
        todo = todo[: args.max]
    print(f"todo={len(todo)} (existing {args.outdir} files skipped)", flush=True)
    if not todo:
        print("nothing left to do", flush=True)
        return 0
    for i, sym in enumerate(todo, 1):
        export_one(args.outdir, sym, args.period, args.interval, args.delay)
        if i % 100 == 0:
            print(f"[{i}/{len(todo)}]", flush=True)
    print(f"DONE {len(todo)} symbols", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())