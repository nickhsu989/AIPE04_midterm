"""unify_databases.py — build the unified market dataset on (symbol, date).

Creates `unified_market_data`, the single table both apps read after the
two-source (Connected MySQL / Sampled dataset) toggle was dropped. It is
the INNER JOIN of the two source tables on (symbol, trade_date):

    price_history       x  sampled_market_data
    (yfinance OHLCV)       (sample.csv snapshot metrics)

so the unified dataset holds exactly the tickers present in BOTH tables
(4,434) across the sampled CSV's date range (251 trading days,
2024-11-18 .. 2025-11-18).

Column families keep their source provenance via the -yf / -fin
suffixes in the app's channel dropdowns; in the table itself only the
one real name collision is disambiguated: price_history.volume ->
volume_yf, sampled_market_data.volume -> volume_fin.

Self-contained: creates its own table (CREATE TABLE IF NOT EXISTS,
matching the load_sampled.py precedent) and is idempotent (upsert) —
re-running never duplicates rows. The source tables are never modified.

Usage:
    venv/bin/python unify_databases.py
"""
import argparse

import db

TABLE = "unified_market_data"

YF_COLUMNS = ("open", "high", "low", "close", "adj_close", "volume_yf")
FIN_COLUMNS = (
    "market_cap", "52w_low", "prev_close", "price", "volume_fin", "52w_high",
    "perf_ytd", "perf_year", "sma200", "perf_half_y", "avg_volume",
    "perf_quarter", "sma50", "perf_month", "sma20", "atr", "rsi_14",
    "perf_week", "rel_volume", "change",
)

DDL = f"""
CREATE TABLE IF NOT EXISTS {TABLE} (
  `symbol`      VARCHAR(16) NOT NULL,
  `trade_date`  DATE NOT NULL,
  `open`        DECIMAL(18,6),
  `high`        DECIMAL(18,6),
  `low`         DECIMAL(18,6),
  `close`       DECIMAL(18,6),
  `adj_close`   DECIMAL(18,6),
  `volume_yf`   BIGINT,
  `market_cap`  DECIMAL(20,6),
  `52w_low`     DECIMAL(18,6),
  `prev_close`  DECIMAL(18,6),
  `price`       DECIMAL(18,6),
  `volume_fin`  BIGINT,
  `52w_high`    DECIMAL(18,6),
  `perf_ytd`    DECIMAL(18,6),
  `perf_year`   DECIMAL(18,6),
  `sma200`      DECIMAL(18,6),
  `perf_half_y` DECIMAL(18,6),
  `avg_volume`  DECIMAL(18,6),
  `perf_quarter` DECIMAL(18,6),
  `sma50`       DECIMAL(18,6),
  `perf_month`  DECIMAL(18,6),
  `sma20`       DECIMAL(18,6),
  `atr`         DECIMAL(18,6),
  `rsi_14`      DECIMAL(18,6),
  `perf_week`   DECIMAL(18,6),
  `rel_volume`  DECIMAL(18,6),
  `change`      DECIMAL(18,6),
  PRIMARY KEY (`symbol`, `trade_date`),
  INDEX idx_date (`trade_date`)
) ENGINE=InnoDB
"""


def ensure_table():
    """CREATE TABLE IF NOT EXISTS — no-op on re-runs."""
    conn = db.get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(DDL)
        conn.commit()
    finally:
        conn.close()


def build():
    """Inner-join upsert of price_history x sampled_market_data.

    Returns (rows_loaded, error_or_None).
    """
    yf_sql = ", ".join(
        "p.`volume` AS `volume_yf`" if c == "volume_yf" else f"p.`{c}`"
        for c in YF_COLUMNS
    )
    fin_sql = ", ".join(
        "s.`volume` AS `volume_fin`" if c == "volume_fin" else f"s.`{c}`"
        for c in FIN_COLUMNS
    )
    columns = ", ".join(f"`{c}`" for c in ("symbol", "trade_date") + YF_COLUMNS + FIN_COLUMNS)
    updates = ", ".join(f"`{c}` = VALUES(`{c}`)" for c in YF_COLUMNS + FIN_COLUMNS)
    sql = f"""
    INSERT INTO {TABLE} ({columns})
    SELECT p.symbol, p.trade_date, {yf_sql}, {fin_sql}
    FROM price_history p
    JOIN sampled_market_data s
      ON s.symbol = p.symbol AND s.`date` = p.trade_date
    ON DUPLICATE KEY UPDATE {updates}
    """
    conn = db.get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(sql)
            written = cur.rowcount
        conn.commit()
        return written, None
    except Exception as exc:  # noqa: BLE001
        conn.rollback()
        return 0, f"{type(exc).__name__}: {exc}"
    finally:
        conn.close()


def verify():
    """Self-check: row / ticker / date counts of the unified table."""
    return db.query(
        f"SELECT COUNT(*) rows_n, COUNT(DISTINCT symbol) syms, "
        f"MIN(trade_date) d0, MAX(trade_date) d1 "
        f"FROM {TABLE}"
    )[0]


def main():
    parser = argparse.ArgumentParser(
        description="build unified_market_data (price_history x sampled_market_data, inner join)")
    parser.parse_args()

    ensure_table()
    written, error = build()
    if error:
        db.log_ingest("api", "unified", error, 0, "error")
        print(f"ERROR: {error}", flush=True)
        return 1
    db.log_ingest("api", "unified", "unified build: price_history x sampled_market_data", written, "ok")
    counts = verify()
    print(
        f"OK: {TABLE} <- {written:,} rows "
        f"({counts['syms']:,} tickers, {counts['d0']} .. {counts['d1']})",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())