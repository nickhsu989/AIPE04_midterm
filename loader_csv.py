"""loader_csv.py — bulk CSV upload engine.

Polls the upload directory for new .csv files, maps columns to MySQL
fields, inserts transactionally, and moves each file to processed/ or
rejected/.

Usage:
    python loader_csv.py [--poll 5]     # watch forever, check every 5s
    python loader_csv.py --once         # single sweep, then exit (tests)
"""
import argparse
import os
import shutil
import time

import pandas as pd

import db
from config import CFG

# header name (lowercased, stripped) -> target column
COLUMN_MAP = {
    "symbol": "symbol",
    "ticker": "symbol",
    "date": "trade_date",
    "trade_date": "trade_date",
    "timestamp": "trade_date",
    "open": "open",
    "high": "high",
    "low": "low",
    "close": "close",
    "adj close": "adj_close",
    "adj_close": "adj_close",
    "volume": "volume",
    "amount": "volume",
}
REQUIRED = {"symbol", "trade_date", "close"}


def map_csv(path):
    """Parse one CSV file -> (df with target columns or None, error or None)."""
    try:
        raw = pd.read_csv(path)
    except Exception as exc:  # noqa: BLE001
        return None, f"unreadable csv: {type(exc).__name__}: {exc}"
    if raw.empty:
        return None, "empty file"

    rename = {}
    for col in raw.columns:
        key = str(col).strip().lower()
        if key in COLUMN_MAP:
            rename[col] = COLUMN_MAP[key]
    df = raw.rename(columns=rename)
    df.columns = [str(c).strip().lower() for c in df.columns]

    missing = REQUIRED - set(df.columns)
    if missing:
        return None, f"missing required columns: {sorted(missing)}"

    df["symbol"] = df["symbol"].astype(str).str.strip().str.upper()
    df["trade_date"] = pd.to_datetime(df["trade_date"], errors="coerce").dt.date
    for col in ["open", "high", "low", "close", "adj_close", "volume"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=["trade_date", "close"])
    if df.empty:
        return None, "no valid rows after parsing"
    for col in ["open", "high", "low", "adj_close"]:
        if col not in df.columns:
            df[col] = None
    if "volume" not in df.columns:
        df["volume"] = 0
    return df[["symbol", "trade_date", "open", "high", "low", "close",
               "adj_close", "volume"]], None


def process_file(path):
    """Transactional commit of one file. Returns (status, message)."""
    df, error = map_csv(path)
    if error:
        return "rejected", error
    rows = [tuple(r) for r in df.itertuples(index=False, name=None)]
    try:
        symbols = sorted(df["symbol"].unique())
        for sym in symbols:
            db.insert_rows("instruments", ["symbol", "last_sync"],
                           [(sym, pd.Timestamp.now().to_pydatetime())])
        written = db.insert_rows("price_history",
                                 ["symbol", "trade_date", "open", "high", "low",
                                  "close", "adj_close", "volume"], rows)
        db.log_ingest("csv", ",".join(symbols), os.path.basename(path), written, "ok")
        return "processed", f"{written} rows"
    except Exception as exc:  # noqa: BLE001
        db.log_ingest("csv", os.path.basename(path),
                      f"{type(exc).__name__}: {exc}", 0, "error")
        return "error", f"{type(exc).__name__}: {exc}"


def sweep(upload_dir):
    """Handle every .csv currently in the upload dir. Returns handled count."""
    handled = 0
    for name in sorted(os.listdir(upload_dir)):
        if not name.lower().endswith(".csv"):
            continue
        src = os.path.join(upload_dir, name)
        status, message = process_file(src)
        dest = os.path.join(CFG[{"processed": "FTE_PROCESSED_DIR",
                                 "rejected": "FTE_REJECTED_DIR",
                                 "error": "FTE_REJECTED_DIR"}[status]], name)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        shutil.move(src, dest)
        print(f"{status.upper()}: {name} -> {message}")
        handled += 1
    return handled


def main():
    parser = argparse.ArgumentParser(description="bulk CSV upload engine")
    parser.add_argument("--poll", type=float, default=None,
                        help="watch every N seconds (default: keep watching, 5s)")
    parser.add_argument("--once", action="store_true", help="single sweep then exit")
    args = parser.parse_args()

    upload_dir = CFG["FTE_UPLOAD_DIR"]
    os.makedirs(upload_dir, exist_ok=True)

    if args.once:
        return 0 if sweep(upload_dir) >= 0 else 1
    interval = args.poll if args.poll is not None else 5
    print(f"watching {upload_dir} every {interval}s (Ctrl+C to stop)")
    while True:
        try:
            sweep(upload_dir)
            time.sleep(interval)
        except KeyboardInterrupt:
            print("\nstopped")
            return 0


if __name__ == "__main__":
    raise SystemExit(main())
