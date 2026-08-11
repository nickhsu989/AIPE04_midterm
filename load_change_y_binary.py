"""load_change_y_binary.py — load data/sampled_184408.csv into MySQL table
change_y_binary (same finance_app database).

Companion to load_sampled.py: the table carries the same (ticker_id, date)
primary key as sampled_market_data and stores the raw change_y column. The
binary 0/1 conversion (change_y > threshold -> 1, else 0) is computed at
QUERY TIME by the logic_layer `market_3d` metric's `change_y_bin` Z channel
(main page, sampled source) — nothing binary is stored here.

Self-contained: creates its own table (CREATE TABLE IF NOT EXISTS) and
bulk-upserts (ticker_id, date, change_y).

Usage:
    venv/bin/python load_change_y_binary.py              # load the whole file
    venv/bin/python load_change_y_binary.py --max 5      # smoke test (first 5 rows)
"""
import argparse
import os

import pandas as pd

import db

TABLE = "change_y_binary"

DDL = f"""
CREATE TABLE IF NOT EXISTS {TABLE} (
  `ticker_id`   INT NOT NULL,
  `date`        DATE NOT NULL,
  `symbol`      VARCHAR(16) NULL,
  `change_y`    DECIMAL(18,6),
  PRIMARY KEY (`ticker_id`, `date`),
  INDEX idx_symbol (`symbol`)
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


def load_file(path, max_rows=0):
    """Read the sampled CSV, map columns, upsert into MySQL.

    Returns (rows_loaded, symbols, error_or_None).
    """
    try:
        df = pd.read_csv(path)
    except Exception as exc:  # noqa: BLE001
        return 0, 0, f"unreadable csv: {type(exc).__name__}: {exc}"
    if df.empty:
        return 0, 0, "empty file"
    if max_rows:
        df = df.head(max_rows)

    rename = {
        "Ticker_id": "ticker_id",
        "ChangeY": "change_y",
    }
    df = df.rename(columns=rename)
    df.columns = [str(c).strip().lower() for c in df.columns]

    df["ticker_id"] = pd.to_numeric(df["ticker_id"], errors="coerce").astype("Int64")
    df["date"] = pd.to_datetime(df["date"].astype(str),
                                format="%Y%m%d", errors="coerce").dt.date
    df["change_y"] = pd.to_numeric(df["change_y"], errors="coerce")
    df = df.dropna(subset=["ticker_id", "date"])
    if df.empty:
        return 0, 0, "no valid rows after parsing"

    if "symbol" in df.columns:
        df["symbol"] = df["symbol"].astype(str).str.strip().str.upper()
        df.loc[df["symbol"] == "NAN", "symbol"] = None
        df.loc[df["symbol"] == "", "symbol"] = None
    else:
        df["symbol"] = None

    columns = ["ticker_id", "date", "symbol", "change_y"]
    rows = [tuple(r) for r in df[columns].itertuples(index=False, name=None)]
    try:
        written = upsert_rows(columns, rows)
        return written, int(df["symbol"].notna().sum()), None
    except Exception as exc:  # noqa: BLE001
        return 0, 0, f"{type(exc).__name__}: {exc}"


def upsert_rows(columns, rows):
    """Bulk upsert like db.insert_rows, but also updates `symbol` on re-runs
    (db.insert_rows excludes it because price_history treats symbol as key)."""
    if not rows:
        return 0
    quoted = [f"`{c}`" for c in columns]
    placeholders = ", ".join(["%s"] * len(columns))
    updates = ", ".join(f"{c} = VALUES({c})" for c in quoted)
    sql = f"INSERT INTO {TABLE} ({', '.join(quoted)}) VALUES ({placeholders}) " \
          f"ON DUPLICATE KEY UPDATE {updates}"
    conn = db.get_conn()
    try:
        with conn.cursor() as cur:
            cur.executemany(sql, rows)
        conn.commit()
        return len(rows)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(
        description="load the sampled change_y column into change_y_binary")
    parser.add_argument("--file", default="data/sampled_184408.csv")
    parser.add_argument("--max", type=int, default=0,
                        help="load at most N rows (smoke test)")
    args = parser.parse_args()

    ensure_table()
    source_name = os.path.splitext(os.path.basename(args.file))[0]
    written, symbols, error = load_file(args.file, args.max)
    if error:
        db.log_ingest("csv", source_name, error, 0, "error")
        print(f"ERROR: {error}", flush=True)
        return 1
    db.log_ingest("csv", source_name, args.file, written, "ok")
    print(f"OK: {args.file} -> {written} rows (symbols filled: {symbols})",
          flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
