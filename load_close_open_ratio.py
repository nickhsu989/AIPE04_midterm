"""load_close_open_ratio.py — load data/staging/<SYM>_max.csv into MySQL table
close_open_ratio_chgpct (same finance_app database).

Companion to load_staging.py: the table carries the same (symbol, trade_date)
primary key as price_history ("the original symbol_max table") and stores the
per-row ratio close / open. One row per price_history row, nothing else — the
table is meant to be joined to price_history on its primary key.

Self-contained: creates its own table (CREATE TABLE IF NOT EXISTS) and
bulk-upserts (symbol, trade_date, close_open_ratio). Idempotent re-runs.

Usage:
    venv/bin/python load_close_open_ratio.py              # all data/staging/*_max.csv
    venv/bin/python load_close_open_ratio.py --max 5      # smoke test (first 5 files)
"""
import argparse
import os
import signal

import pandas as pd

import db

TABLE = "close_open_ratio_chgpct"

# Per-file watchdog: any single file (parse or DB upsert) taking longer than
# this is skipped and logged as an error so the bulk run never freezes.
FILE_TIMEOUT = 120  # seconds

# Drop ratios that would overflow DECIMAL(18,6) (~1e12) — the same 1264 class
# of failure that rejected the 13 corrupt symbols in load_staging.py.
RATIO_CEILING = 1e11

DDL = f"""
CREATE TABLE IF NOT EXISTS {TABLE} (
  `symbol`          VARCHAR(16) NOT NULL,
  `trade_date`      DATE NOT NULL,
  `close_open_ratio` DECIMAL(18,6) NULL,
  PRIMARY KEY (`symbol`, `trade_date`),
  INDEX idx_date (`trade_date`)
) ENGINE=InnoDB
"""


def ensure_table():
    """Create the target table if it does not exist yet."""
    conn = db.get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(DDL)
        conn.commit()
    finally:
        conn.close()


class _Timeout(Exception):
    pass


def _timeout_handler(signum, frame):
    raise _Timeout("file exceeded the per-file watchdog")


def _watchdog(seconds):
    """Install a SIGALRM watchdog; must run in the main thread."""
    signal.signal(signal.SIGALRM, _timeout_handler)
    signal.alarm(seconds)


def process_file(path, conn=None):
    """Read one <SYM>_max.csv, compute close/open, upsert into MySQL.

    A per-file watchdog (FILE_TIMEOUT seconds) interrupts any pathological
    stall (parse or DB) so the bulk run never freezes on one file — the
    stalled file is returned as an error and the run continues.

    `conn` is the run's shared connection (reused across files).

    Returns (symbol, rows_loaded, error_or_None).
    """
    symbol = os.path.basename(path).split("_")[0]
    _watchdog(FILE_TIMEOUT)
    try:
        return _process_file(path, symbol, conn=conn)
    except _Timeout:
        return symbol, 0, f"timed out after {FILE_TIMEOUT}s (watchdog)"
    except Exception as exc:  # noqa: BLE001
        return symbol, 0, f"{type(exc).__name__}: {exc}"
    finally:
        signal.alarm(0)


def _process_file(path, symbol, conn=None):
    try:
        df = pd.read_csv(path)
    except Exception as exc:  # noqa: BLE001
        return symbol, 0, f"unreadable csv: {type(exc).__name__}: {exc}"
    if df.empty:
        return symbol, 0, "empty file"

    df["open"] = pd.to_numeric(df["open"], errors="coerce")
    df["close"] = pd.to_numeric(df["close"], errors="coerce")
    df["trade_date"] = pd.to_datetime(df["trade_date"], errors="coerce").dt.date

    valid_open = df["open"].notna() & (df["open"] != 0)
    df = df[valid_open & df["close"].notna() & df["trade_date"].notna()]
    if df.empty:
        return symbol, 0, "no valid rows after parsing"

    df["close_open_ratio"] = df["close"] / df["open"]
    df = df[df["close_open_ratio"].abs() < RATIO_CEILING]
    if df.empty:
        return symbol, 0, "no rows within DECIMAL(18,6) range after filtering"

    columns = ["symbol", "trade_date", "close_open_ratio"]
    rows = [tuple(r) for r in df[columns].itertuples(index=False, name=None)]
    try:
        written = upsert_rows(columns, rows, conn=conn)
        return symbol, written, None
    except Exception as exc:  # noqa: BLE001
        return symbol, 0, f"{type(exc).__name__}: {exc}"


def upsert_rows(columns, rows, conn=None):
    """Bulk upsert on the (symbol, trade_date) primary key.

    Delegates to db.insert_rows so the loader automatically uses the fast
    bulk path (FTE_FAST_LOAD=1) when it is enabled and available.
    """
    return db.insert_rows(TABLE, columns, rows, conn=conn)


def main():
    parser = argparse.ArgumentParser(
        description="compute close/open per row from data/staging CSVs "
                    "into close_open_ratio_chgpct")
    parser.add_argument("--dir", default="data/staging")
    parser.add_argument("--suffix", default="max")
    parser.add_argument("--max", type=int, default=0,
                        help="load at most N files (smoke test)")
    args = parser.parse_args()

    files = sorted(f for f in os.listdir(args.dir)
                   if f.endswith(f"_{args.suffix}.csv"))
    if args.max:
        files = files[:args.max]

    ensure_table()

    ok = err = rows = 0
    conn = db.loader_conn()
    try:
        for fname in files:
            path = os.path.join(args.dir, fname)
            symbol, written, error = process_file(path, conn=conn)
            if error:
                db.log_ingest("csv", symbol, error, 0, "error", conn=conn)
                print(f"ERROR: {symbol} -> {error}", flush=True)
                err += 1
            else:
                db.log_ingest("csv", symbol, fname, written, "ok", conn=conn)
                print(f"OK: {symbol} -> {written} rows ({path})", flush=True)
                ok += 1
                rows += written
    finally:
        conn.close()

    print(f"DONE ok={ok} err={err} rows={rows} of {len(files)}", flush=True)
    return 1 if err else 0


if __name__ == "__main__":
    raise SystemExit(main())
