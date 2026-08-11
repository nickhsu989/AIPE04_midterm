"""load_staging2.py — load the full-history CSV exports from data/staging2/
into MySQL.

Companion to ingest_universe.py (CSV-only export): this reads every
<SYMBOL>_max.csv in the staging2 directory and upserts it into
instruments / price_history (idempotent) with an ingest_log row each.

Deliberately simple: no network, no delay needed. Files are never moved or
deleted — staging2 remains the export checkpoint.
"""
import argparse
import os

import pandas as pd

import db

REQUIRED = {"symbol", "trade_date", "close"}
COLUMNS = ["symbol", "trade_date", "open", "high", "low", "close",
           "adj_close", "volume"]
REJECTED_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "data", "universe", "verified_rejected.csv")


def ensure_rejected_file():
    """Create verified_rejected.csv with its header when absent/empty."""
    if not os.path.exists(REJECTED_FILE) or os.path.getsize(REJECTED_FILE) == 0:
        with open(REJECTED_FILE, "w", newline="") as fh:
            fh.write("symbol\n")


def load_rejected():
    """Return the set of symbols already recorded in verified_rejected.csv."""
    ensure_rejected_file()
    symbols = set()
    with open(REJECTED_FILE, newline="") as fh:
        for line in fh:
            sym = line.strip()
            if sym and sym != "symbol":
                symbols.add(sym)
    return symbols


def record_rejected(symbol, rejected_syms):
    """Best-effort append of a failed symbol (deduped); never crashes."""
    if symbol in rejected_syms:
        return
    rejected_syms.add(symbol)
    try:
        with open(REJECTED_FILE, "a", newline="") as fh:
            fh.write(f"{symbol}\n")
    except OSError as exc:
        print(f"WARN: could not record {symbol} in {REJECTED_FILE}: {exc}",
              flush=True)


def load_file(path, symbol):
    """Upsert one staging CSV into MySQL. Returns (ok, rows, error)."""
    try:
        df = pd.read_csv(path, dtype=str, keep_default_na=False)
    except Exception as exc:  # noqa: BLE001
        return False, 0, f"unreadable csv: {type(exc).__name__}: {exc}"
    if df.empty:
        return False, 0, "empty file"
    missing = REQUIRED - set(df.columns)
    if missing:
        return False, 0, f"missing required columns: {sorted(missing)}"
    df["symbol"] = df["symbol"].astype(str).str.strip().str.upper()
    df["trade_date"] = pd.to_datetime(df["trade_date"], errors="coerce").dt.date
    for col in ["open", "high", "low", "close", "adj_close", "volume"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=["symbol", "trade_date", "close"])
    if df.empty:
        return False, 0, "no valid rows after parsing"
    for col in COLUMNS:
        if col not in df.columns:
            df[col] = 0 if col == "volume" else None
    rows = [tuple(r) for r in df[COLUMNS].itertuples(index=False, name=None)]
    try:
        db.insert_rows("instruments",
                       ["symbol", "asset_type", "last_sync"],
                       [(symbol, "equity", pd.Timestamp.now().to_pydatetime())])
        written = db.insert_rows("price_history", COLUMNS, rows)
        db.log_ingest("csv", symbol, os.path.basename(path), written, "ok")
        return True, written, None
    except Exception as exc:  # noqa: BLE001
        db.log_ingest("csv", symbol, f"{type(exc).__name__}: {exc}", 0, "error")
        return False, 0, f"{type(exc).__name__}: {exc}"


def main():
    parser = argparse.ArgumentParser(
        description="load data/staging2/ full-history CSVs into MySQL")
    parser.add_argument("--dir", default="data/staging2")
    parser.add_argument("--suffix", default="max",
                        help="filename suffix filter, e.g. max (default)")
    parser.add_argument("--max", type=int, default=0,
                        help="load at most N files (smoke test)")
    args = parser.parse_args()

    suffix = f"_{args.suffix}.csv"
    files = sorted(name for name in os.listdir(args.dir) if name.endswith(suffix))
    if args.max:
        files = files[: args.max]
    print(f"files={len(files)} in {args.dir}", flush=True)
    if not files:
        print("nothing to load", flush=True)
        return 0

    done = errors = rows_total = 0
    rejected_syms = load_rejected()
    if rejected_syms:
        print(f"loaded {len(rejected_syms)} previously rejected symbols from "
              f"{REJECTED_FILE}", flush=True)
    for i, name in enumerate(files, 1):
        path = os.path.join(args.dir, name)
        symbol = name[: -len(suffix)]
        ok, n, err = load_file(path, symbol)
        if ok:
            done += 1
            rows_total += n
            print(f"OK: {symbol} -> {n} rows ({path})", flush=True)
        else:
            errors += 1
            record_rejected(symbol, rejected_syms)
            print(f"ERROR: {symbol} -> {err}", flush=True)
        if i % 100 == 0:
            print(f"[{i}/{len(files)}] ok={done} err={errors} rows={rows_total}", flush=True)
    print(f"DONE ok={done} err={errors} rows={rows_total} of {len(files)}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())