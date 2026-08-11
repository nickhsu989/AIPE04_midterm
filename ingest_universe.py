"""ingest_universe.py — bulk export: full-history (max period) daily data for
every symbol in a universe CSV, written to data/staging2/.

Deliberately simple, one job — CSV-only (never touches MySQL):
  - serial loop, one symbol at a time, then a fixed sleep
    (default 1s = 1 req/s, well under Yahoo's ~2 req/s rate cap)
  - per-symbol CSV is written immediately to OUTDIR = the checkpoint
  - resume = scan OUTDIR for "<SYMBOL>_<period>.csv": existing files are
    skipped, except the newest (likely killed mid-write) which is re-run
  - never reads, writes, or deletes anything in data/staging/

Usage:
  python ingest_universe.py                  # every symbol in verify_ok.csv, max period
  python ingest_universe.py --max 5          # smoke test (first 5 remaining)
"""
import argparse
import os
import time

import pandas as pd

from ingest_api import clean, fetch_history

OUTDIR = "data/staging2"
COLUMNS = ["symbol", "trade_date", "open", "high", "low", "close",
           "adj_close", "volume"]


def load_symbols(path):
    """Uppercase symbol list from the universe CSV (skips header/empty)."""
    raw = pd.read_csv(path, dtype=str, keep_default_na=False)
    col = "symbol" if "symbol" in raw.columns else raw.columns[0]
    syms = raw[col].str.strip().str.upper()
    return sorted({s for s in syms if s and s != "SYMBOL"})


def done_symbols(period):
    """Symbols already exported for this period, except the newest file."""
    suffix = f"_{period}.csv"
    staged = []
    for name in os.listdir(OUTDIR):
        if not name.endswith(suffix):
            continue
        path = os.path.join(OUTDIR, name)
        staged.append((name[: -len(suffix)], os.path.getmtime(path)))
    if not staged:
        return set()
    staged.sort(key=lambda item: item[1])
    return {sym for sym, _ in staged[:-1]}  # newest is re-run


def export_one(symbol, period, interval, delay):
    """Fetch, clean, and write one symbol's CSV to OUTDIR (the checkpoint)."""
    raw, error = fetch_history(symbol, period, interval)
    if error:
        print(f"ERROR: {symbol} -> {error}", flush=True)
        time.sleep(delay)
        return
    df = clean(raw, symbol)
    if df.empty:
        print(f"ERROR: {symbol} -> empty after cleaning", flush=True)
        time.sleep(delay)
        return
    path = os.path.join(OUTDIR, f"{symbol}_{period}.csv")
    df[COLUMNS].to_csv(path, index=False)
    print(f"OK: {symbol} -> {len(df)} rows ({path})", flush=True)
    time.sleep(delay)


def main():
    parser = argparse.ArgumentParser(
        description="bulk export full-history data for every universe symbol")
    parser.add_argument("--file", default="data/universe/verify_ok.csv")
    parser.add_argument("--period", default="max",
                        help="yfinance period, e.g. 1y, 5y, 10y, max (default)")
    parser.add_argument("--interval", default="1d")
    parser.add_argument("--delay", type=float, default=1.0,
                        help="seconds between symbols (keeps us under the rate cap)")
    parser.add_argument("--max", type=int, default=0,
                        help="stop after N symbols (smoke test)")
    args = parser.parse_args()

    os.makedirs(OUTDIR, exist_ok=True)
    todo = [s for s in load_symbols(args.file) if s not in done_symbols(args.period)]
    if args.max:
        todo = todo[: args.max]
    print(f"todo={len(todo)} (existing {OUTDIR} files skipped)", flush=True)
    if not todo:
        print("nothing left to do", flush=True)
        return 0
    for i, sym in enumerate(todo, 1):
        export_one(sym, args.period, args.interval, args.delay)
        if i % 100 == 0:
            print(f"[{i}/{len(todo)}]", flush=True)
    print(f"DONE {len(todo)} symbols", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
