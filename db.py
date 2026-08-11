"""db.py — centralized MySQL access (backend only).

Called by the ingestion scripts (writes) and the logic layer (reads).
The browser and Streamlit never connect to MySQL directly.

Note: schema.sql ships empty (per spec). execute_schema() runs its
statements once the user populates it; while empty it is a no-op and
queries that touch missing tables raise, which callers handle gracefully.
"""
import pymysql

from config import CFG


def get_conn():
    """Return a short-lived pymysql connection (DictCursor, autocommit off)."""
    return pymysql.connect(
        host=CFG["DB_HOST"],
        port=CFG["DB_PORT"],
        user=CFG["DB_USER"],
        password=CFG["DB_PASSWORD"],
        database=CFG["DB_NAME"],
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=False,
        charset="utf8mb4",
    )


def query(sql, params=None):
    """Run a parameterized SELECT and return a list of row dicts."""
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params or ())
            return cur.fetchall()
    finally:
        conn.close()


def insert_rows(table, columns, rows):
    """Bulk upsert rows into `table`.

    rows is an iterable of tuples aligned to `columns`.
    Uses INSERT ... ON DUPLICATE KEY UPDATE for idempotent re-runs.
    """
    if not rows:
        return 0
    placeholders = ", ".join(["%s"] * len(columns))
    update_cols = [c for c in columns if c != "symbol"] or columns
    updates = ", ".join(f"{c} = VALUES({c})" for c in update_cols)
    sql = f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders}) " \
          f"ON DUPLICATE KEY UPDATE {updates}"
    conn = get_conn()
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


def execute_schema():
    """Run every ';'-terminated statement found in schema.sql."""
    import os

    path = os.path.join(os.path.dirname(__file__), "schema.sql")
    with open(path, encoding="utf-8") as f:
        script = f.read()
    statements = [s.strip() for s in script.split(";") if s.strip() and not s.strip().startswith("--")]
    if not statements:
        return 0
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            for stmt in statements:
                cur.execute(stmt)
        conn.commit()
        return len(statements)
    finally:
        conn.close()


def log_ingest(source, symbol, detail, rows_written, status):
    """Write one ingest_log row. Best-effort: logging must never crash a pipeline."""
    try:
        insert_rows(
            "ingest_log",
            ["source", "symbol", "detail", "rows_written", "status"],
            [(source, symbol, detail, rows_written, status)],
        )
    except Exception as exc:  # noqa: BLE001
        print(f"[ingest_log skipped] {type(exc).__name__}: {exc}")