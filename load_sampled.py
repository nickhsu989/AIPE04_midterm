"""load_sampled.py — load data/sampled_184408.csv into MySQL table
sampled_market_data (same finance_app database).

Self-contained: creates its own table (CREATE TABLE IF NOT EXISTS) and
bulk-upserts the daily per-ticker metrics snapshot.

The source CSV currently has only an integer Ticker_id. A future reload of
the updated CSV (with a 'symbol' column appended) is supported: when a
symbol/ticker column is present it is uppercased and stored in the table's
nullable `symbol` column, which is later used to JOIN against instruments.

Usage:
    venv/bin/python load_sampled.py              # load the whole file
    venv/bin/python load_sampled.py --max 5      # smoke test (first 5 rows)
"""
import argparse
import os

import pandas as pd

import db

TABLE = "sampled_market_data"
NUMERIC_COLS = [
    "market_cap", "52w_low", "prev_close", "price", "volume", "52w_high",
    "perf_ytd", "perf_year", "sma200", "perf_half_y", "avg_volume",
    "perf_quarter", "sma50", "perf_month", "sma20", "atr", "rsi_14",
    "perf_week", "rel_volume", "change", "change_y",
]

DDL = f"""
CREATE TABLE IF NOT EXISTS {TABLE} (
  `ticker_id`   INT NOT NULL,
  `date`        DATE NOT NULL,
  `symbol`      VARCHAR(16) NULL,
  `market_cap`  DECIMAL(20,6),
  `52w_low`     DECIMAL(18,6),
  `prev_close`  DECIMAL(18,6),
  `price`       DECIMAL(18,6),
  `volume`      BIGINT,
  `52w_high`    DECIMAL(18,6),
  `perf_ytd`    DECIMAL(18,6),
  `perf_year`   DECIMAL(18,6),
  `sma200`      DECIMAL(18,6),
  `perf_half_y` DECIMAL(18,6),
  `avg_volume`  BIGINT,
  `perf_quarter` DECIMAL(18,6),
  `sma50`       DECIMAL(18,6),
  `perf_month`  DECIMAL(18,6),
  `sma20`       DECIMAL(18,6),
  `atr`         DECIMAL(18,6),
  `rsi_14`      DECIMAL(18,6),
  `perf_week`   DECIMAL(18,6),
  `rel_volume`  DECIMAL(18,6),
  `change`      DECIMAL(18,6),
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
        "Market_Cap": "market_cap", "52W_Low": "52w_low",
        "Prev_Close": "prev_close", "52W_High": "52w_high",
        "Ticker_id": "ticker_id", "Perf_YTD": "perf_ytd",
        "Perf_Year": "perf_year", "Perf_Half_Y": "perf_half_y",
        "Avg_Volume": "avg_volume", "Perf_Quarter": "perf_quarter",
        "Perf_Month": "perf_month", "Perf_Week": "perf_week",
        "Rel_Volume": "rel_volume", "ChangeY": "change_y",
    }
    df = df.rename(columns=rename)
    df.columns = [str(c).strip().lower() for c in df.columns]

    df["ticker_id"] = pd.to_numeric(df["ticker_id"], errors="coerce").astype("Int64")
    df["date"] = pd.to_datetime(df["date"].astype(str),
                                format="%Y%m%d", errors="coerce").dt.date
    df = df.dropna(subset=["ticker_id", "date"])
    if df.empty:
        return 0, 0, "no valid rows after parsing"

    for col in NUMERIC_COLS:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    if "symbol" in df.columns:
        df["symbol"] = df["symbol"].astype(str).str.strip().str.upper()
        df.loc[df["symbol"] == "NAN", "symbol"] = None
        df.loc[df["symbol"] == "", "symbol"] = None
    else:
        df["symbol"] = None

    columns = ["ticker_id", "date", "symbol"] + NUMERIC_COLS
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
        description="load the sampled metrics CSV into sampled_market_data")
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
