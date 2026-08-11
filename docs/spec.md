# Financial Analytics Platform — Architectural & Technical Specification

Version: 1.0
Audience: implementers, maintainers, graders
Scope: full design of a data-driven financial analytics web app, per `docs/architecture_description.txt`, built in this directory (`midtermproject2`).

---

## 1. System Overview

The platform ingests external market data through a yfinance API pipeline and a bulk CSV upload engine, stores it in a relational MySQL database, processes it through an isolated, replaceable **Logic Layer**, and serves the results through two self-contained presentation surfaces: a **Flask** main page (custom HTML/CSS/JS frontend) and a **Streamlit** secondary dashboard.

### 1.1 Goals

1. Correctly ingest, validate, and persist market data (equity + market indices).
2. Keep all domain/analytical compute inside exactly one evolvable file: `logic_layer.py`.
3. Keep every other layer **static and generic** so they serve *any valid request* produced by the Logic Layer without modification.
4. Use only the simplest-to-read technologies within the mandated stack (see §1.3) and the simplest Python constructs (plain functions, parameterized SQL, JSON).

### 1.2 Data flow (end-to-end)

```
                    yfinance API                     CSV uploads            sampled snapshot
                        │                                │                  data/sampled_184408.csv
                        ▼                                ▼                          │
                ingest_api.py                    loader_csv.py                load_sampled.py
              (requests/yfinance →     (polls data/uploads, maps columns  (self-contained: creates
               pandas/numpy clean →      → transactional commits)         sampled_market_data +
               staging CSV → insert)          │                           upserts snapshot rows)
                        │                     │                                  │
                        │                     │                     load_change_y_binary.py
                        │                     │                     (creates change_y_binary,
                        │                     │                      PK ticker_id+date, §5.5)
                        ▼                     ▼                                  ▼
              ┌────────────────────────────────────────────────────────────────────┐
              │              Storage Tier (MySQL, db.py)                          │
              │  instruments · price_history · ingest_log · sampled_market_data   │
              │  · change_y_binary · close_open_ratio_chgpct                      │
              └────────────────────────────────────────────────────────────────────┘
                        │            ▲
                        │ SQL reads │ (backend only — no client access)
                        ▼            │
              ┌─────────────────────────────────────────┐
              │      LOGIC LAYER  (logic_layer.py)      │
              │  metric registry → canonical envelope   │
              └─────────────────────────────────────────┘
│
      app_flask.py (main)              app_streamlit.py (secondary)
      GET /  (3D chart rendered        fixed history view (symbol picker),
      server-side from the             Return-to-Main button
      market_3d metric envelope)
      GET /api/config
              │
              ▼
      static/ index.html · style.css
      (server-injected Figure HTML)

     Main page ⇄ secondary page: hyperlink both ways (FTE_MAIN_URL / FTE_STREAMLIT_URL)
```

### 1.3 Tech stack (mandated)

| Concern | Choice |
|---|---|
| Language | Python 3.12 |
| Ingestion API | `requests` → `yfinance` |
| Data cleaning | `numpy`, `pandas` (ingestion only) |
| Universe scraping | `requests` + `beautifulsoup4` (builds the symbol dropdown list) |
| Storage | MySQL (relational), `pymysql` driver |
| Config/secrets | `python-dotenv` → `.env` |
| Presentation A (main) | `flask` + server-rendered Plotly figure + static `index.html`/`style.css` |
| Presentation B (secondary) | `streamlit` |
| Charts | `plotly` (figure built server-side) → Plotly.js (main page) and `st.plotly_chart` (Streamlit) |
| Frontend assets | `index.html` + `style.css` (static, Plotly.js CDN) |

**`requirements.txt`** (exact, pinned to what the build verified):
`numpy`, `pandas`, `pymysql`, `flask`, `streamlit`, `plotly`, `requests`, `beautifulsoup4`, `yfinance`, `python-dotenv`

---

## 2. Project Structure

```
midtermproject2/
├── .env                      # real secrets (gitignored) — user fills ONLY DB_USER / DB_PASSWORD
├── .env.example              # committed template with all non-secret defaults
├── .gitignore                # .env, venv/, __pycache__/, data staging + processed files
├── requirements.txt          # dependency list (§1.3)
├── setup_finance_app.sql     # one-time admin bootstrap: DB + user + grants + tables
├── schema.sql                # EMPTY placeholder — reserved for future MySQL DDL only
├── config.py                 # loads .env, single source of truth for all settings
├── db.py                     # pymysql connection + execute_schema() + query/insert helpers
├── ingest_api.py             # yfinance → DataFrame → numpy cleaning → staging CSV → MySQL
├── ingest_universe.py        # bulk full-history export (CSV-only, resumable) → data/staging2/ (§5.4)
├── loader_csv.py             # polls upload dir, maps CSV columns → MySQL, transactional
├── load_sampled.py           # creates sampled_market_data + upserts data/sampled_184408.csv (§5.3)
├── load_change_y_binary.py   # creates change_y_binary (PK ticker_id+date) from the same CSV (§5.5)
├── load_staging2.py          # loads data/staging2/ CSVs → MySQL; failures → verified_rejected.csv (§5.4)
├── load_close_open_ratio.py  # creates close_open_ratio_chgpct (PK symbol+trade_date, close/open) from the same CSVs (§5.6)
├── verify_tickers.py         # checks universe symbols against yfinance → verify_ok/bad.csv (§5.4)
├── logic_layer.py            # THE Logic Layer: registry + metrics + canonical envelopes
├── app_presenter.py          # envelope → Plotly figure JSON (shared by both UIs)
├── app_flask.py              # Flask main app: server-rendered 3D page + GET /api/config
├── app_streamlit.py          # Streamlit secondary page + "Return to Main Page" button
├── static/
│   ├── index.html            # main page DOM (server-injected 3D figure)
│   └── style.css             # layout, dark/light themes
├── docs/                     # this spec + architecture.md + original architecture_description.txt
├── logs/                     # runtime logs (flask.log, streamlit.log, load2/load3.log)
└── data/
    ├── sampled_184408.csv    # sampled snapshot dataset (§5.3)
    ├── universe/             # tickerinventory.csv, verify_ok.csv, verify_bad.csv, verified_rejected.csv (§5.4)
    ├── staging/              # ingest_api.py writes staged CSVs here
    ├── staging2/             # ingest_universe.py export checkpoints (<SYM>_max.csv) (§5.4)
    ├── uploads/              # loader_csv.py watches this directory
    ├── processed/            # loader_csv.py moves handled CSVs here
    └── rejected/             # loader_csv.py moves invalid CSVs here
```

---

## 3. Configuration (`.env`)

`config.py` reads `.env` via `python-dotenv`. Every setting is defined there; code never hardcodes a host/port/db/url.

| Key | Default (in `.env.example`) | User-supplied |
|---|---|---|
| `DB_HOST` | `localhost` | — |
| `DB_PORT` | `3306` | — |
| `DB_NAME` | `finance_app` | — |
| `DB_USER` | *(empty)* | **yes** |
| `DB_PASSWORD` | *(empty)* | **yes** |
| `FTE_STAGING_DIR` | `data/staging` | — |
| `FTE_UPLOAD_DIR` | `data/uploads` | — |
| `FTE_PROCESSED_DIR` | `data/processed` | — |
| `FTE_REJECTED_DIR` | `data/rejected` | — |
| `FTE_BIND_HOST` | `127.0.0.1` (config.py default) | `0.0.0.0` for LAN access — the shipped `.env.example` already ships `0.0.0.0` |
| `FTE_MAIN_URL` | `http://127.0.0.1:5000` | — |
| `FTE_STREAMLIT_URL` | `http://127.0.0.1:8501` | — |

- All keys except `DB_USER` / `DB_PASSWORD` are pre-set with working defaults. The user only fills in the two credential values.
- `.env` is gitignored; `.env.example` is committed and checked in.

---

## 4. Storage Tier

**Rule 1 — Backend-only access.** Every database operation goes through `db.py`. The browser and Streamlit never connect to MySQL directly; all reads flow through the Logic Layer.

**Rule 2 — `schema.sql` policy.** `schema.sql` ships **empty** and is reserved exclusively for future MySQL DDL. `db.py` exposes `execute_schema()` which reads `schema.sql` and runs each `;`-terminated statement. Until the user populates it, no tables are created by the app itself; the full target DDL is documented in **Appendix A**, ready to paste into `schema.sql`. Exception: `sampled_market_data` is created by its own loader (`load_sampled.py`, §5.3) with `CREATE TABLE IF NOT EXISTS` — its DDL lives in the script, not `schema.sql`.

`db.py` API (used by ingestion + logic layers):
- `get_conn()` — one short-lived `pymysql.connect` per operation (DictCursor).
- `query(sql, params)` — parameterized SELECT → list of dicts.
- `insert_rows(table, columns, rows)` — `executemany` with `INSERT ... ON DUPLICATE KEY UPDATE` upsert.
- `execute_schema()` — runs statements from `schema.sql` (no-op while empty).
- `log_ingest(...)` — writes to `ingest_log`.

---

## 5. Data Ingestion

### 5.1 API pipeline — `ingest_api.py`

CLI: `python ingest_api.py --symbol AAPL --period 1y [--interval 1d]`

Process sequence (per the architecture description):
1. Resolve config, connect `db.get_conn()`.
2. Fetch OHLCV history from yfinance (`requests` under the hood). Symbol validity: empty response → log `error` (`invalid`), skip.
3. Normalize into a pandas DataFrame; force numeric dtypes and parse dates.
4. **numpy** cleaning pass: forward-fill/leave `NaN`s handled per column policy; drop fully-empty rows.
5. Serialize the in-memory DataFrame to a localized staging CSV in `FTE_STAGING_DIR` (`<SYMBOL>_<period>.csv`).
6. Ensure the symbol row exists in `instruments` (upsert) — **parent row first**, required by the `price_history → instruments` foreign key (inserting children first fails with MySQL error 1452).
7. Bulk-insert the staging CSV into `price_history` on a short-lived connection (`db.insert_rows`), upsert semantics.
8. Write one `ingest_log` row: source `api`, symbol, rows written, status, timestamps.

Rate-limit handling: on yfinance failure, log and retry once with a short sleep; final failure → `ingest_log` row with status `error`.

### 5.2 Bulk CSV upload — `loader_csv.py`

1. Polls `FTE_UPLOAD_DIR` every N seconds (simple loop; no extra watchdog library).
2. On a new `.csv` file: sniff headers from row 1, map delimited columns to MySQL fields (data dictionary in Appendix C).
3. Validate types with pandas/numpy; skip/move malformed files to `FTE_REJECTED_DIR`.
4. Open a transaction: upsert the distinct symbols into `instruments` **first** (FK parent-first, see §5.1), then `executemany` inserts into `price_history` (upsert), commit; on exception roll back and log.
5. Move the file to `FTE_PROCESSED_DIR`; write `ingest_log` rows.

Both paths write only to `instruments`, `price_history`, `ingest_log`. The Logic Layer never cares which ingestion produced the data.

### 5.3 Sampled snapshot loader — `load_sampled.py`

CLI: `python load_sampled.py [--file data/sampled_184408.csv] [--max N]`

Loads the sampled daily snapshot (`data/sampled_184408.csv`: 184,408 rows,
32 dates, 5,992 ticker_ids) into a **standalone** table
`sampled_market_data` in the same `finance_app` database — no foreign keys.

1. `CREATE TABLE IF NOT EXISTS sampled_market_data` (DDL below, in-script
   only) — PK `(ticker_id, date)`, nullable `symbol VARCHAR(16)` +
   `INDEX idx_symbol`.
2. Read the CSV with pandas; rename headers to snake_case; parse the
   `YYYYMMDD` integer `date` column into a MySQL `DATE`; coerce numerics.
3. Upsert via `INSERT ... ON DUPLICATE KEY UPDATE` covering **every**
   column (unlike `db.insert_rows`, which excludes `symbol` — here `symbol`
   must be updatable). This makes reloads idempotent.
4. Write one `ingest_log` row (`source=csv`, `symbol=sampled_184408`).

**Future symbol linkage:** the current CSV has only integer `Ticker_id`, so
`symbol` stays NULL. When the updated CSV with a `symbol` column appended is
loaded, the loader uppercases/strips it and the upsert fills `symbol` in
place — enabling a later `JOIN sampled_market_data.symbol =
instruments.symbol`.

**Identifier quoting:** every column reference is backtick-quoted in the
DDL and SQL because several names are not bare identifiers in MySQL:
`change` (reserved word) and `52w_low` / `52w_high` (leading digits).

### 5.4 Bulk universe pipeline — `verify_tickers.py` / `ingest_universe.py` / `load_staging2.py`

The checkpoint flow that built the full price-history dataset (12k+
ticker inventory → 7,454 verified symbols → 7,240 exported CSVs):

1. `verify_tickers.py` — serial existence check of
   `data/universe/tickerinventory.csv` symbols against yfinance
   (`get_history_metadata`); writes `data/universe/verify_ok.csv` /
   `verify_bad.csv`, resumable (skips symbols already in either output).
2. `ingest_universe.py` — **CSV-only bulk export**: fetches full history
   (`--period max` default) for every `verify_ok.csv` symbol and writes one
   `<SYMBOL>_max.csv` into `data/staging2/`. Never touches MySQL and never
   reads/writes/deletes `data/staging/`. Serial, ~1 req/s (`--delay 1.0`),
   resumable: existing files are skipped, the newest is re-run. The CSV
   file is the checkpoint.
3. `load_staging2.py` — loads every `<SYMBOL>_max.csv` from `data/staging2/`
   into MySQL (local files only, no network): upserts the `instruments`
   parent row first, bulk-upserts `price_history` (idempotent
   `symbol`+`trade_date` PK), writes one `ingest_log` row per file. Files
   are never moved or deleted.

**Rejected registry:** every failed symbol (unreadable/malformed CSV **or**
DB error such as MySQL 1264 overflow / `-inf` values) is appended
best-effort and deduped to `data/universe/verified_rejected.csv` (header
`symbol`) so failures can be reviewed without grepping logs. Current
registry: 13 symbols.

### 5.5 Change_y binary table — `load_change_y_binary.py`

CLI: `python load_change_y_binary.py [--file data/sampled_184408.csv] [--max N]`

Self-contained loader (same pattern as §5.3) that creates the table
`change_y_binary` in the same `finance_app` database — **no foreign keys**,
mirroring `sampled_market_data`:

```sql
CREATE TABLE IF NOT EXISTS change_y_binary (
  `ticker_id`   INT NOT NULL,
  `date`        DATE NOT NULL,
  `symbol`      VARCHAR(16) NULL,
  `change_y`    DECIMAL(18,6),
  PRIMARY KEY (`ticker_id`, `date`),
  INDEX idx_symbol (`symbol`)
) ENGINE=InnoDB
```

1. PK `(ticker_id, date)` is **identical to `sampled_market_data`** — the
   "via primary key" connection between the snapshot dataset and this table.
2. Reads the same CSV as `load_sampled.py`; maps `ChangeY` → `change_y`,
   parses the `YYYYMMDD` `date`, coerces numerics, uppercases `symbol` when
   present (nullable; currently NULL in the file).
3. Upserts via `INSERT ... ON DUPLICATE KEY UPDATE` covering `change_y` and
   `symbol` — idempotent re-runs.
4. One `ingest_log` row per run.

**Query-time binary conversion (not stored):** the table holds only the raw
`change_y`. The `market_3d` metric converts it per request when its
`change_y_bin` Z channel is selected (§6.3): `CASE WHEN b.change_y > %s
THEN 1 ELSE 0 END AS change_y_bin` against `change_y_binary`, where `%s` is
the caller's `threshold` (int, **≥ 0**; negative or unparseable → 0). The
`change_y_binary` metric (§6.3) still converts the same way for direct
calls.

### 5.6 Close/Open ratio table — `load_close_open_ratio.py`

CLI: `python load_close_open_ratio.py [--dir data/staging2] [--suffix max] [--max N]`

Self-contained loader (same pattern as §5.3/§5.5) that creates the table
`close_open_ratio_chgpct` in the same `finance_app` database — **no foreign
keys**, connected to `price_history` **via primary key**:

```sql
CREATE TABLE IF NOT EXISTS close_open_ratio_chgpct (
  `symbol`           VARCHAR(16) NOT NULL,
  `trade_date`       DATE NOT NULL,
  `close_open_ratio` DECIMAL(18,6) NULL,
  PRIMARY KEY (`symbol`, `trade_date`),
  INDEX idx_date (`trade_date`)
) ENGINE=InnoDB
```

1. PK `(symbol, trade_date)` is **identical to `price_history`** (the table
   loaded from the `<SYM>_max.csv` staging2 exports) — the "via primary key"
   connection: `JOIN ... ON p.symbol = r.symbol AND p.trade_date =
   r.trade_date`.
2. Reads every `data/staging2/<SYM>_max.csv`, computes `close / open` per
   row, coerces numerics, parses `trade_date`.
3. Sanitization: rows with `open` = 0 / NaN / non-finite, or a ratio whose
   magnitude would overflow `DECIMAL(18,6)` (~1e12), are skipped — this also
   lets the 13 corrupt-symbol files load their valid rows.
4. Upserts via `INSERT ... ON DUPLICATE KEY UPDATE` — idempotent re-runs;
   one `ingest_log` row per file (source `csv`, symbol = ticker).
5. Files are never moved or deleted. Not exposed in any metric/UI yet.

---

## 6. Logic Layer — the only layer that changes (CORE)

The **brain** of the system. It accepts a request, runs valid SQL/transforms, and returns one canonical envelope. It never assumes which UI is asking.

### 6.1 Registry / dispatch

`logic_layer.py` exposes:
- `registered_metrics: dict[str, callable]` — slug → metric function; holds exactly what the apps display (`history` + `market_3d`).
- `handle_request(metric, params) -> envelope` — validates the slug, dispatches, catches exceptions into an error envelope (never raises to the UI).
- A metric may return an optional 7th tuple element — a dict with `/meta` and/or `chart` keys merged into the envelope (used by `market_3d` for empty-state messages and full chart metadata).

Metric functions are pure: `(params) -> DataFrame`. They query only via `db.query()` (parameterized SQL).

### 6.2 Canonical envelope (every metric returns exactly this shape)

```json
{
  "metric": "history",
  "status": "ok",
  "title": "history — AAPL",
  "meta": {
    "rows": 250,
    "params": {"symbol": "AAPL", "limit": 250},
    "generated_at": "2026-08-04T10:00:00Z"
  },
  "chart": {
    "type": "line",
    "x": "trade_date",
    "y": "close",
    "title": "history — AAPL"
  },
  "columns": ["trade_date", "open", "high", "low", "close", "adj_close", "volume"],
  "rows": [["2026-07-31", 240.5, 242.0, 238.9, 241.5, 241.5, 50000000]]
}
```

Rules:
- `chart.type` ∈ `line | bar | scatter | candlestick | table` (the types `app_presenter` implements) — plus `scatter3d` for `market_3d` and `change_y_binary`, rendered by `app_flask`.
- `market_3d`'s `chart` additionally carries the full column→channel mapping (`z`, `size`, `hover`, `color`) and visual knobs (`colorscale`, `colorbar_title`, `opacity`) so column-assignment changes happen only in the logic layer.
- `rows` are JSON-safe lists aligned to `columns`.
- Errors: `{"status": "error", "meta": {"errors": [...]}, "columns": [], "rows": []}`.

### 6.3 Built-in metrics

| slug | used by | returns |
|---|---|---|
| `history` | Streamlit dashboard | raw OHLCV rows, chronological order oldest-first (line/candlestick); optional `days` trailing window; `limit` (default 250, `0` = no limit) |
| `market_3d` | Flask main page (internal, not user-selectable) | decimated window of OHLCV columns + `chart` metadata mapping columns to the 3D scene; `x=symbol`, `y=trade_date` are **fixed** channels, `z/size/color` params override which numeric MySQL column drives each remaining channel (defaults: close/volume/adj_close, validated with fallback to defaults). **`source` param** (see below) switches the table and channels; `source=sampled` with `z=change_y_bin` is the **binary view** — a 0/1 flag computed at query time from `threshold` (≥ 0, default 0, negative/unparseable → 0) via `CASE WHEN b.change_y > %s THEN 1 ELSE 0 END` against `change_y_binary` (joined on the shared `(ticker_id, date)` PK), with `meta` counts (`above`/`total`) for the page summary |
| `change_y_binary` | standalone (retained for direct calls) | sampled `change_y` → 0/1 flag at query time: `threshold` (int ≥ 0, default 0, negative/unparseable → 0); `change_y > threshold` → 1 else 0; optional `symbols` (ticker_ids), `days` window, `limit`; `chart` = `scatter3d` (x = `ticker_id`, y = `date`, z = `change_y`, color = `change_y_bin`) + `meta` counts (`above`/`total`) — the main page shows the same view via `market_3d`'s `change_y_bin` Z channel |

**`source` param (`market_3d` only):** `connected` (default) reads
`price_history` as above. `source=sampled` reads `sampled_market_data`:
`x=ticker_id`, `y=date`, hover = `ticker_id`; `z/size/color` validate
against `SAMPLED_NUMERIC_COLUMNS` (`market_cap`, `52w_low`, `prev_close`,
`price`, `volume`, `52w_high`, `perf_ytd`, `perf_year`, `sma200`,
`perf_half_y`, `avg_volume`, `perf_quarter`, `sma50`, `perf_month`,
`sma20`, `atr`, `rsi_14`, `perf_week`, `rel_volume`, `change`, `change_y`)
with defaults z=`change_y_bin`, size=`volume`, color=`change`; **the Z channel
additionally offers the computed binary channel `change_y_bin`
(`SAMPLED_CHANNEL_COLUMNS` = `SAMPLED_NUMERIC_COLUMNS` + `change_y_bin`) —
selecting it joins `change_y_binary` on the shared `(ticker_id, date)` PK
and returns `CASE WHEN b.change_y > %s THEN 1 ELSE 0 END AS change_y_bin`
from the `threshold` param (§6.3), plus `meta` above/total counts**; the
`days` window offs from `MAX(date)` of the sampled table; `symbols`
filters raw `ticker_id`s (matching `symbol_list("sampled")`, which returns
distinct ticker_ids instead of instrument symbols).

**Window semantics — "absence-based" `days`** (both metrics): an absent,
non-positive or unparseable `days` means **no trailing window** (full
history); a positive `days` means a trailing N-day window (clamped to 365).
The apps always send an explicit UI default (30 days), except the main page's
"All history" range, which sends `days=` (empty) so the metric applies no
window.

**`symbols` param (`market_3d` only):** absent = all symbols; a comma list =
IN-filter; present-but-empty (`symbols=`) = no symbols → empty envelope with
message "No symbols selected."

All math is plain SQL (window functions) + simple Python formatting. pandas is used only for DataFrame plumbing — envelope serialization and the chronological re-sort (`oldest → newest`) applied to the latest-N rows in `history` so charts always render left-to-right in time.

### 6.4 Extension rule (the contract)

> **To adjust what the main page chart shows (fixed x/y, z/size/color columns, default/empty states): edit only `market_3d` + `symbol_list` in `logic_layer.py`** — the `SELECT` (which columns exist) and its returned `chart` dict. No app file changes. (Restart the Flask server after editing imported modules.) The `source` branch is part of `market_3d`; the per-source channel dropdown options are rendered by `app_flask._channel_options` from the same `NUMERIC_COLUMNS` / `SAMPLED_NUMERIC_COLUMNS` constants (Z in sampled mode additionally offers `SAMPLED_CHANNEL_COLUMNS`, i.e. `SAMPLED_NUMERIC_COLUMNS` + the computed `change_y_bin`).
>
> **To add a new analytics view:** (1) write one `@register` function in `logic_layer.py`, (2) call it via `logic_layer.handle_request` from the app that displays it. The Streamlit dashboard is a fixed price-history view and does not expose a metric menu (§7.2).

---

## 7. Presentation Tier

### 7.1 Flask — MAIN page (`app_flask.py`)

- `GET /` — serves `static/index.html` with the 3D scatter figure injected server-side from the `market_3d` envelope (`app_flask._chart_html` → `logic_layer.handle_request`). TTL-cached per `(source, days, symbols, z, size, color, threshold)`.
- **Binary view on the main page:** with `source=sampled`, the Z dropdown renders the computed `change_y_bin` option (`SAMPLED_CHANNEL_COLUMNS`); while it is selected a threshold slider (`<input type="range">`, 0–100, step 1) appears in the topbar and reloads `/?threshold=N` (clamped server-side to `0..BINARY_MAX_THRESHOLD`); the page also renders a one-line summary from the `market_3d` envelope's `above`/`total` meta counts. There is no separate `/binary` page anymore.
- Main page topbar has a time-range dropdown (Last 30 days default / 60 / 90 / 180 / 365 / **All history**), a multi-select symbol listbox (default: first symbol ticked; `symbols=` = none ticked, `symbols=A,B` = those only; options injected at `<!-- SYMBOLS -->` from `logic_layer.symbol_list()`), and Z/Size/Color dropdowns (numeric-only, validated with fallback). Each change reloads `/?days=…&symbols=…&z=…&size=…&color=…`; `days=` (empty) means all history. `GET /api/config` is also served; the nav bar uses it for the **hyperlink** to the Streamlit secondary page (`FTE_STREAMLIT_URL`).

### 7.2 Streamlit — secondary page (`app_streamlit.py`)

Deliberately barebones: a fixed price-history view, no metric menu.

- Symbol dropdown populated live from `instruments` (`SELECT symbol FROM instruments ORDER BY symbol` via `db.py`) — only ingested symbols appear.
- On load and on symbol change it auto-runs the `history` metric through `logic_layer.handle_request` (same envelope contract, no duplicated query logic) and renders the chart + data table via `app_presenter.envelope_to_figure`.
- Window radio: **Last 30 days (default)** / 60 / 90 / 365 / All — windowed choices pass `days=N` with `limit=0` (full window), All omits `days` (full history); no row-count toggle.
- Data table headers are mapped to friendly names (`Trade Date`, `Open`, `High`, `Low`, `Close`, `Adj Close`, `Volume`).
- Empty DB → inline hint to run `ingest_api.py --symbol AAPL --period 1y`; error envelope → `st.error`.
- "**Return to Main Page**" button — hyperlink styled as a button pointing at `FTE_MAIN_URL`.

### 7.3 `app_presenter.py` (shared)

- `envelope_to_figure(envelope) -> plotly Figure` — pure mapping from the canonical envelope to a Plotly figure (chart type → `go.Scatter`/`go.Bar`/`go.Candlestick`/`go.Table`).
- Used by the Streamlit page directly. Flask does **not** share `envelope_to_figure`: its 3D chart is built server-side in `_chart_html` from the `market_3d` envelope's `chart` metadata.
- Because the envelope serializes numbers as strings (§6.2), the presenter coerces every numeric column back to real numbers and sets `autotypenumbers="convert types"` — otherwise `plotly_dark`'s strict type detection turns the y-axis into a categorical axis ordered by row order (values read top-to-bottom instead of low-to-high).

---

## 8. Client Tier (static)

### 8.1 `index.html`
- Minimal main page DOM: topbar (title, time-range `<select>` with "All history" option, multi-select symbol listbox, Z/Size/Color `<select>`s, "Dashboard →" link to Streamlit) + `#chart3d` container; the 3D figure is injected server-side at `<!-- FIGURE -->` and the symbol options at `<!-- SYMBOLS -->`.
- Small inline script: points the dashboard link at the configured `streamlit_url` (`/api/config`); reloads `/?days=…` on range change (empty value → all history), `/?symbols=…` on listbox change (all ticked → param omitted; none ticked → `symbols=` empty), and `/?z|size|color=…` on channel change — each preserving the other params.

### 8.2 `style.css`
- One stylesheet: CSS grid layout, `:root` CSS variables for light/dark themes, `[data-theme="dark"]` override, responsive single column on small screens.

---

## 9. Environment & Setup

```
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env            # then fill DB_USER / DB_PASSWORD
python ingest_api.py --symbol AAPL --period 1y    # seed data
python loader_csv.py &                            # optional upload watcher
flask --app app_flask run                          # main page  :5000
streamlit run app_streamlit.py                     # secondary :8501
```

MySQL prerequisites (run once as admin, from the project root):
`mysql -u root -p < setup_finance_app.sql` — creates the `finance_app`
database, the app user with grants, and all three tables. Then fill
`DB_USER` / `DB_PASSWORD` in `.env` (same values as the script).

---

## 10. Security & Error Handling

- All SQL parameterized (`db.query`) — never string-interpolated. SQL injection blocked.
- Secrets only in `.env` (gitignored); nothing secret in code, logs, or repo.
- No direct client-to-DB access; every read goes through the Logic Layer.
- yfinance 429/rate-limit: caught, retried once, else logged to `ingest_log` as `error`.
- Unknown metric / bad params / empty results → error envelope (never a bare 500 or uncaught traceback).
- Flask and Streamlit are stateless; no shared global state.

---

## 11. Verification Plan

| Stage | Test |
|---|---|
| Ingestion API | `ingest_api.py --symbol AAPL --period 1w` → staging CSV created; `ingest_log` has an `ok` row; rows visible in MySQL |
| Invalid symbol | e.g. `ZZZZ.AA` → skipped, logged as `error`, no crash |
| CSV loader | Drop valid OHLCV CSV in `data/uploads` → moved to `processed/`, rows inserted; malformed → `rejected/` |
| Logic layer | Call `history` and `market_3d` via `handle_request`; assert `chart.type` valid, `columns` == `rows` width, non-empty |
| **Column-mapping test** | Edit only `market_3d`'s `chart` dict (e.g. `z: close → adj_close`) → main page 3D chart reflects it with no app-file changes |
| Flask main | `GET /` → 200, 3D chart renders, time-range select reloads `?days=N` |
| Streamlit | page renders, symbol dropdown lists only ingested symbols, history chart renders oldest→newest with y-axis low→high, Return-to-Main hyperlink goes back to `/` |
| Security | `?symbol=%27%3B%20DROP%20TABLE%20price_history%3B--` → error envelope, DB intact |

---

## Appendix A — Full DDL (future `schema.sql` content)

```sql
-- Target schema. schema.sql ships EMPTY; paste this in when creating tables.
CREATE DATABASE IF NOT EXISTS finance_app CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
USE finance_app;

CREATE TABLE IF NOT EXISTS instruments (
  symbol     VARCHAR(16)  PRIMARY KEY,
  name       VARCHAR(255),
  asset_type ENUM('equity','index') NOT NULL DEFAULT 'equity',
  currency   CHAR(3)      DEFAULT 'USD',
  sector     VARCHAR(64),
  last_sync  DATETIME
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS price_history (
  symbol     VARCHAR(16) NOT NULL,
  trade_date DATE        NOT NULL,
  open       DECIMAL(18,6),
  high       DECIMAL(18,6),
  low        DECIMAL(18,6),
  close      DECIMAL(18,6),
  adj_close  DECIMAL(18,6),
  volume     BIGINT,
  PRIMARY KEY (symbol, trade_date),
  INDEX idx_date (trade_date),
  CONSTRAINT fk_px_symbol FOREIGN KEY (symbol)
    REFERENCES instruments (symbol) ON DELETE CASCADE
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS ingest_log (
  id           INT AUTO_INCREMENT PRIMARY KEY,
  source       ENUM('api','csv') NOT NULL,
  symbol       VARCHAR(16),
  detail       VARCHAR(500),
  rows_written INT,
  status       ENUM('ok','error') NOT NULL,
  started_at   DATETIME DEFAULT CURRENT_TIMESTAMP,
  finished_at  DATETIME
) ENGINE=InnoDB;
```

Upserts use `INSERT ... ON DUPLICATE KEY UPDATE` (idempotent re-runs).

---

## Appendix B — Canonical envelope reference

- Producers: `logic_layer.handle_request(metric, params)` — called by `app_streamlit.py` (`history`) and `app_flask._chart_html` (`market_3d`, including the binary `change_y_bin` Z channel).
- Envelope shape per §6.2. `chart.type` ∈ `line | bar | scatter | candlestick | table`, plus `scatter3d` for `market_3d` (and the retained `change_y_binary` metric), rendered by `app_flask`.
- Consumers: `app_presenter.envelope_to_figure` (Streamlit) and `app_flask._chart_html` (main page 3D chart, binary included).

---

## Appendix C — Data Dictionary

| Field | Type | Notes |
|---|---|---|
| `symbol` | VARCHAR(16) | uppercase ticker; PK in `instruments`, PK part in `price_history` |
| `trade_date` | DATE | market day; PK part in `price_history` |
| `open/high/low/close/adj_close` | DECIMAL(18,6) | OHLC + adjusted close |
| `volume` | BIGINT | traded volume |
| `name` | VARCHAR(255) | instrument display name |
| `asset_type` | ENUM | `equity` / `index` |
| `currency` | CHAR(3) | default `USD` |
| `last_sync` | DATETIME | last successful ingest time |
| `source` | ENUM | `api` / `csv` (in `ingest_log`) |
| `status` | ENUM | `ok` / `error` (in `ingest_log`) |
| `close_open_ratio` | DECIMAL(18,6) | `close / open` per row; PK `(symbol, trade_date)` in `close_open_ratio_chgpct`, joins to `price_history` (§5.6) |

---

*End of spec v1.0.*
