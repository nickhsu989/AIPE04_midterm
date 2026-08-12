"""load_sampled.py — load data/for_train_y_2025_11_18sample.csv into MySQL
table sampled_market_data (same finance_app database).

Self-contained: creates its own table (CREATE TABLE IF NOT EXISTS) and
bulk-upserts the daily per-ticker metrics snapshot.

The snapshot dataset is keyed by real ticker symbols: the CSV's `Ticker`
column becomes the table's `symbol` (VARCHAR(16)) primary key along with
`date` — the old integer ticker_id identity is gone. The `market_cap`
column carries human-formatted values (`B`/`M`/`K` suffixes) and is
expanded to the actual number. There is no ChangeY column in this
dataset, so the table stores no `change_y`; the binary 0/1 view is
computed at query time from `change`.

572 MB file: read in chunks (pandas chunksize) so memory stays bounded;
each chunk is upserted immediately (idempotent — re-running never
duplicates rows and writes one ingest_log row per run).

Usage:
    venv/bin/python load_sampled.py              # load the whole file
    venv/bin/python load_sampled.py --max 5      # smoke test (first 5 rows)
"""
import argparse
import math
import os
import re

import pandas as pd

import db

TABLE = "sampled_market_data"
CHUNK_SIZE = 200_000
# Columns mixing numbers with human-formatted strings (e.g. `40.78B`,
# `5.35%`, `-`) — force string dtype to silence pandas' DtypeWarning and
# let the column-level parsers handle them.
STRING_COLS = {
    "Market_Cap", "Income", "Sales", "Dividend", "EPS_Growth_this_Y",
    "EPS_Annual_Growth_Next_5Y",
}
NUMERIC_COLS = [
    "market_cap", "52w_low", "prev_close", "price", "volume", "52w_high",
    "perf_ytd", "perf_year", "sma200", "perf_half_y", "avg_volume",
    "perf_quarter", "sma50", "perf_month", "sma20", "atr", "rsi_14",
    "perf_week", "rel_volume", "change",
]

DDL = f"""
CREATE TABLE IF NOT EXISTS {TABLE} (
  `symbol`      VARCHAR(16) NOT NULL,
  `date`        DATE NOT NULL,
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
  PRIMARY KEY (`symbol`, `date`)
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


def parse_market_cap(value):
    """Human-formatted market cap -> number (B = billion, M = million,
    K = thousand); empty / '-' / unparseable -> None."""
    v = str(value).strip()
    if not v or v == "-":
        return None
    m = re.fullmatch(r"([+-]?\d+(?:\.\d+)?)([KMB]?)", v)
    if not m:
        return None
    return float(m.group(1)) * {"": 1.0, "K": 1e3, "M": 1e6, "B": 1e9}[m.group(2)]


def ingest_log_label(path):
    """Short ingest_log symbol for the source file.

    The full basename ('for_train_y_2025_11_18sample') exceeds
    ingest_log.symbol VARCHAR(16); derive a compact label from the
    embedded date when present, else truncate.
    """
    name = os.path.splitext(os.path.basename(path))[0]
    m = re.search(r"(\d{4})_?(\d{2})_?(\d{2})", name)
    if m:
        label = f"sample_{m.group(1)}{m.group(2)}{m.group(3)}"
        if len(label) <= 16:
            return label
    return name[:16]


def prepare_chunk(df):
    """Normalize one CSV chunk: rename/clean columns, coerce numerics,
    parse dates and market_cap. Returns the DataFrame ready to upsert."""
    rename = {
        "Market_Cap": "market_cap", "52W_Low": "52w_low",
        "Prev_Close": "prev_close", "52W_High": "52w_high",
        "Ticker": "ticker", "Perf_YTD": "perf_ytd",
        "Perf_Year": "perf_year", "Perf_Half_Y": "perf_half_y",
        "Avg_Volume": "avg_volume", "Perf_Quarter": "perf_quarter",
        "Perf_Month": "perf_month", "Perf_Week": "perf_week",
        "Rel_Volume": "rel_volume",
    }
    df = df.rename(columns=rename)
    df.columns = [str(c).strip().lower() for c in df.columns]
    if "ticker" in df.columns:
        df = df.rename(columns={"ticker": "symbol"})

    df["symbol"] = df["symbol"].astype(str).str.strip().str.upper()
    df.loc[df["symbol"] == "NAN", "symbol"] = None
    df.loc[df["symbol"] == "", "symbol"] = None
    df["date"] = pd.to_datetime(df["date"].astype(str),
                                format="%Y%m%d", errors="coerce").dt.date
    df = df.dropna(subset=["symbol", "date"])

    for col in NUMERIC_COLS:
        if col == "market_cap":
            df[col] = pd.Series([parse_market_cap(v) for v in df[col]],
                                index=df.index, dtype="float64")
        elif col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    missing = [c for c in NUMERIC_COLS if c not in df.columns]
    for col in missing:
        df[col] = None

    return df


def _clean(value):
    """NaN / +/-inf floats -> None (MySQL rejects non-finite floats)."""
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def load_file(path, max_rows=0):
    """Read the sampled CSV in chunks, map columns, upsert into MySQL.

    Returns (rows_loaded, symbols, error_or_None).
    """
    columns = ["symbol", "date"] + NUMERIC_COLS
    written = 0
    symbols = 0
    remaining = max_rows or None
    try:
        reader = pd.read_csv(path, chunksize=CHUNK_SIZE,
                             dtype={c: str for c in STRING_COLS})
        for chunk in reader:
            if remaining is not None:
                if remaining <= 0:
                    break
                chunk = chunk.head(remaining)
                remaining -= len(chunk)
            df = prepare_chunk(chunk)
            if df.empty:
                continue
            rows = [tuple(_clean(v) for v in r)
                    for r in df[columns].itertuples(index=False, name=None)]
            try:
                written += upsert_rows(columns, rows)
            except Exception as exc:  # noqa: BLE001
                return written, symbols, f"{type(exc).__name__}: {exc}"
            symbols += int(df["symbol"].notna().sum())
        return written, symbols, None
    except Exception as exc:  # noqa: BLE001
        return written, symbols, f"unreadable csv: {type(exc).__name__}: {exc}"


def upsert_rows(columns, rows):
    """Bulk upsert covering every column (idempotent re-runs)."""
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
    parser.add_argument("--file", default="data/for_train_y_2025_11_18sample.csv")
    parser.add_argument("--max", type=int, default=0,
                        help="load at most N rows (smoke test)")
    args = parser.parse_args()

    ensure_table()
    source_name = ingest_log_label(args.file)
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