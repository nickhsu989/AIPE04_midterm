"""db.py — centralized MySQL access (backend only).

Called by the ingestion scripts (writes) and the logic layer (reads).
The browser and Streamlit never connect to MySQL directly.

Bulk writes go through `insert_rows`, which has two interchangeable paths:

  * row-wise  (default, works on every MySQL/MariaDB) — one parameterized
    `INSERT ... ON DUPLICATE KEY UPDATE`; pymysql's executemany merges the
    rows into multi-value statements up to its statement size limit.
  * fast      (opt-in via `FTE_FAST_LOAD=1` in .env) — `LOAD DATA LOCAL
    INFILE` into a temporary staging table followed by one server-side
    `INSERT ... SELECT ... ON DUPLICATE KEY UPDATE`. 1.0-1.5x faster
    end-to-end on bulk loads (measured, see README §8) because the driver
    no longer escapes every value into SQL text. Requires the server's
    `local_infile=ON`; when it is off the call falls back to the row-wise
    path (once, with a warning) so a run never dies on a missing server
    setting.

Both paths are idempotent (same ON DUPLICATE KEY UPDATE semantics) and are
pure pymysql — no platform-specific code, so macOS and Linux behave the same.

Note: schema.sql ships empty (per spec). execute_schema() runs its
statements once the user populates it; while empty it is a no-op and
queries that touch missing tables raise, which callers handle gracefully.
"""
import os
import tempfile

import pymysql

from config import CFG

# Fast path is opt-in and only kicks in for bulk batches; small upserts
# (one instruments row, one ingest_log row) stay on the row-wise path.
FAST_LOAD_MIN_ROWS = 2000
_TMP_TABLE = "db_load_stage"
_fast_load_disabled_reason = None


def fast_load_enabled():
    """True when `FTE_FAST_LOAD` asks for the LOAD DATA fast path."""
    return str(CFG.get("FTE_FAST_LOAD", "0")).strip().lower() in ("1", "true", "yes", "on")


def loader_conn():
    """A connection for a bulk-load run: reused across files by the loaders.

    Reuse matters because every new connection costs a TLS handshake
    (~12 ms on localhost), and the per-file loaders used to open two or
    three of them. The connection is configured for LOAD DATA when the fast
    path is requested; if the server refuses it, `insert_rows` falls back.
    """
    return get_conn(local_infile=fast_load_enabled())


def get_conn(local_infile=False):
    """Return a short-lived pymysql connection (DictCursor, autocommit off).

    `local_infile=True` additionally allows LOAD DATA LOCAL INFILE on this
    connection (the server must also have local_infile=ON).
    """
    return pymysql.connect(
        host=CFG["DB_HOST"],
        port=CFG["DB_PORT"],
        user=CFG["DB_USER"],
        password=CFG["DB_PASSWORD"],
        database=CFG["DB_NAME"],
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=False,
        charset="utf8mb4",
        local_infile=local_infile,
    )


def query(sql, params=None):
    """Run a parameterized SELECT and return a list of row dicts.

    Passing None (no params) makes pymysql skip client-side interpolation,
    so a literal `%` in the SQL (e.g. LIKE 'abc%') stays literal.
    """
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params if params else None)
            return cur.fetchall()
    finally:
        conn.close()


def _staging_ddl(conn, table, columns):
    """Create a temporary table shaped like `table`'s `columns` (no indexes —
    the staging table is only a transport buffer, the final upsert does the
    indexed work)."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s ORDER BY ORDINAL_POSITION",
            (CFG["DB_NAME"], table),
        )
        meta = {r["COLUMN_NAME"]: r for r in cur.fetchall()}
    if not meta:
        return False
    ddl = ", ".join(
        f"`{c}` {meta[c]['COLUMN_TYPE']}"
        + ("" if meta[c]["IS_NULLABLE"] == "NO" else " NULL")
        for c in columns
    )
    with conn.cursor() as cur:
        cur.execute(f"DROP TEMPORARY TABLE IF EXISTS `{_TMP_TABLE}`")
        cur.execute(f"CREATE TEMPORARY TABLE `{_TMP_TABLE}` ({ddl})")
    return True


def _tsv_value(value):
    """One TSV field: NULL -> \\N, everything else escaped for
    LOAD DATA ... FIELDS TERMINATED BY '\\t' ESCAPED BY '\\\\'.

    Floats use repr() (shortest round-trip) so the text handed to MySQL is
    byte-identical to what pymysql sends on the row-wise path — formatting
    to a fixed precision here would round differently from MySQL's own
    DECIMAL conversion and the two paths would diverge in the last digit.
    """
    if value is None:
        return "\\N"
    if isinstance(value, bool):
        return "1" if value else "0"
    text = str(value)
    if text == "":
        return ""
    if "\\" in text:
        text = text.replace("\\", "\\\\")
    if "\t" in text:
        text = text.replace("\t", "\\t")
    if "\n" in text or "\r" in text:
        text = text.replace("\r\n", "\\r\\n").replace("\n", "\\n").replace("\r", "\\r")
    return text


def _write_tsv(rows, path):
    with open(path, "w", encoding="utf-8", newline="") as fh:
        for row in rows:
            fh.write("\t".join(_tsv_value(v) for v in row))
            fh.write("\n")


def _insert_rows_fast(table, columns, rows, conn=None):
    """LOAD DATA LOCAL INFILE into a temp staging table, then one server-side
    upsert. Same ON DUPLICATE KEY UPDATE semantics as the row-wise path.

    Raises on any failure (the caller falls back to the row-wise path).
    """
    quoted = [f"`{c}`" for c in columns]
    update_cols = [c for c in columns if c != "symbol"] or columns
    updates = ", ".join(f"`{c}` = VALUES(`{c}`)" for c in update_cols)
    own_conn = conn is None
    conn = conn or get_conn(local_infile=True)
    tmp_path = None
    try:
        if not _staging_ddl(conn, table, columns):
            raise RuntimeError(f"columns of {table} not found")
        fd, tmp_path = tempfile.mkstemp(prefix="db_load_", suffix=".tsv")
        os.close(fd)
        _write_tsv(rows, tmp_path)
        with conn.cursor() as cur:
            cur.execute(
                f"LOAD DATA LOCAL INFILE %s INTO TABLE `{_TMP_TABLE}` CHARACTER SET utf8mb4 "
                r"FIELDS TERMINATED BY '\t' ESCAPED BY '\\' LINES TERMINATED BY '\n' "
                f"({', '.join(quoted)})",
                (tmp_path,),
            )
            cur.execute(
                f"INSERT INTO {table} ({', '.join(quoted)}) "
                f"SELECT {', '.join(quoted)} FROM `{_TMP_TABLE}` "
                f"ON DUPLICATE KEY UPDATE {updates}"
            )
            cur.execute(f"DROP TEMPORARY TABLE IF EXISTS `{_TMP_TABLE}`")
        conn.commit()
        return len(rows)
    except Exception:
        conn.rollback()
        raise
    finally:
        if own_conn:
            conn.close()
        if tmp_path and os.path.exists(tmp_path):
            os.remove(tmp_path)


def insert_rows(table, columns, rows, conn=None):
    """Bulk upsert rows into `table`.

    rows is an iterable of tuples aligned to `columns`.
    Uses INSERT ... ON DUPLICATE KEY UPDATE for idempotent re-runs.

    Bulk batches (>= FAST_LOAD_MIN_ROWS rows) take the LOAD DATA fast path
    when `FTE_FAST_LOAD=1` and the server allows it; anything else — and any
    fast-path failure, e.g. `local_infile=OFF` on the server — goes through
    the portable row-wise path.

    `conn` reuses a caller-owned connection across many calls (the loaders
    pass one connection per run and commit per file); when omitted a
    short-lived connection is opened and closed here.
    """
    if not rows:
        return 0
    global _fast_load_disabled_reason
    rows = list(rows)
    if len(rows) >= FAST_LOAD_MIN_ROWS and fast_load_enabled() and _fast_load_disabled_reason is None:
        try:
            return _insert_rows_fast(table, columns, rows, conn=conn)
        except Exception as exc:  # noqa: BLE001 — any fast-path problem falls back
            _fast_load_disabled_reason = f"{type(exc).__name__}: {exc}"
            print(f"[db] fast load unavailable, falling back to row-wise upserts "
                  f"({_fast_load_disabled_reason})", flush=True)
    placeholders = ", ".join(["%s"] * len(columns))
    update_cols = [c for c in columns if c != "symbol"] or columns
    quoted_cols = [f"`{c}`" for c in columns]
    quoted_updates = ", ".join(f"{q} = VALUES({q})" for q in
                               [f"`{c}`" for c in update_cols])
    sql = f"INSERT INTO {table} ({', '.join(quoted_cols)}) VALUES ({placeholders}) " \
          f"ON DUPLICATE KEY UPDATE {quoted_updates}"
    own_conn = conn is None
    conn = conn or get_conn()
    try:
        with conn.cursor() as cur:
            cur.executemany(sql, rows)
        conn.commit()
        return len(rows)
    except Exception:
        conn.rollback()
        raise
    finally:
        if own_conn:
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


def log_ingest(source, symbol, detail, rows_written, status, conn=None):
    """Write one ingest_log row. Best-effort: logging must never crash a pipeline."""
    try:
        insert_rows(
            "ingest_log",
            ["source", "symbol", "detail", "rows_written", "status"],
            [(source, symbol, detail, rows_written, status)],
            conn=conn,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"[ingest_log skipped] {type(exc).__name__}: {exc}")