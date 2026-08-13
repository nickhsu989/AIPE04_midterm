# Financial Analytics Platform — Architectural & Technical Specification

Version: 1.0
Audience: implementers, maintainers, graders
Scope: full design of a data-driven financial analytics web app, per `docs/architecture_description.txt`, built in this directory (`midtermproject2`).

---

## 1. System Overview

The platform ingests external market data through a yfinance API pipeline, stores it in a relational MySQL database, processes it through an isolated, replaceable **Logic Layer**, and serves the results through two self-contained presentation surfaces: a **Flask** main page (custom HTML/CSS/JS frontend) and a **Streamlit** secondary dashboard.

### 1.1 Goals

1. Correctly ingest, validate, and persist market data (equity + market indices).
2. Keep all domain/analytical compute inside exactly one evolvable file: `logic_layer.py`.
3. Keep every other layer **static and generic** so they serve *any valid request* produced by the Logic Layer without modification.
4. Use only the simplest-to-read technologies within the mandated stack (see §1.3) and the simplest Python constructs (plain functions, parameterized SQL, JSON).

### 1.2 Data flow (end-to-end)

```
                    yfinance API                     sampled snapshot
                        │                           for_train_y_2025_11_18sample.csv
                        ▼                                  │
                  ingest.py (CSV-only)                  load_sampled.py
              (requests/yfinance →            (self-contained: creates
               pandas/numpy clean →           sampled_market_data, PK
               <SYM>_max.csv → staging)       symbol+date, upserts rows)
                        │                                  │
                        ▼                                  ▼
         load_staging.py / load_close_open_ratio.py
                        │
                        ▼
              ┌────────────────────────────────────────────────────────────────────┐
              │              Storage Tier (MySQL, db.py)                          │
              │  instruments · price_history · ingest_log · sampled_market_data   │
              │  · close_open_ratio_chgpct                                         │
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
├── .gitignore                # .env, venv/, __pycache__/, data staging files
├── requirements.txt          # dependency list (§1.3)
├── setup_finance_app.sql     # one-time admin bootstrap: DB + user + grants + tables
├── schema.sql                # EMPTY placeholder — reserved for future MySQL DDL only
├── config.py                 # loads .env, single source of truth for all settings
├── db.py                     # pymysql connection + execute_schema() + query/insert helpers
├── ingest.py                 # CSV-only bulk export (yfinance → clean → <SYM>_max.csv → data/staging/) (§5.1, §5.4)
├── load_sampled.py           # creates sampled_market_data (PK symbol+date) + upserts data/for_train_y_2025_11_18sample.csv (§5.3)
├── load_staging.py           # loads data/staging/ CSVs → MySQL; failures → verified_rejected.csv (§5.4)
├── load_close_open_ratio.py  # creates close_open_ratio_chgpct (PK symbol+trade_date, close/open) from the same CSVs (§5.6)
├── verify_tickers.py         # checks check_exist symbols against yfinance → verify_ok/bad.csv (§5.4)
├── logic_layer.py            # THE Logic Layer: registry + metrics + canonical envelopes
├── app_flask.py              # Flask main app: server-rendered 3D page + GET /api/config
├── app_streamlit.py          # Streamlit secondary page + envelope → Plotly figure (folded in from the former app_presenter.py; §7.3)
├── static/
│   ├── index.html            # main page DOM (server-injected 3D figure)
│   └── style.css             # layout, dark/light themes
├── docs/                     # this spec + architecture.md + original architecture_description.txt
├── logs/                     # runtime logs (flask.log, streamlit.log, load2/load3.log)
└── data/
    ├── for_train_y_2025_11_18sample.csv  # sampled snapshot dataset (§5.3)
    ├── sampled_184408.csv  # archived superseded snapshot (unreferenced)
    ├── check_exist/          # tickerinventory.csv, verify_ok.csv, verify_bad.csv, verified_rejected.csv, ingest_failures.csv (§5.4)
    ├── staging/              # ingest.py export checkpoints (<SYM>_max.csv) (§5.4)
```

---

## 3. Configuration (`.env`)

`config.py` reads `.env` via `python-dotenv`. Every setting is defined there; code never hardcodes a host/port/db/url.

> **Who may read `CFG`:** backend modules only (`db.py`, `logic_layer.py` — the loaders
> read it transitively via `db`). The apps (`app_flask.py`, `app_streamlit.py`) never import
> `config` directly; they get app-facing values through `logic_layer`'s whitelist accessors
> `get_urls()` / `get_bind_host()` (DB credentials stay behind `db.py`).

| Key | Default (in `.env.example`) | User-supplied |
|---|---|---|
| `DB_HOST` | `localhost` | — |
| `DB_PORT` | `3306` | — |
| `DB_NAME` | `finance_app` | — |
| `DB_USER` | *(empty)* | **yes** |
| `DB_PASSWORD` | *(empty)* | **yes** |
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

### 5.1 Ingest tool — `ingest.py` (CSV-only, merged)

`ingest.py` merges the former `ingest_api.py` (single-symbol) and
`ingest_universe.py` (bulk) into one **CSV-only** exporter: it never touches
MySQL — loading is the loaders' job (§5.4, §5.6). The single-symbol/`1y`
interactive path is removed; the supported export is the bulk full-history
run (`--period max` default), see §5.4.

CLI: `python ingest.py [--file data/check_exist/verify_ok.csv] [--period max]
[--interval 1d] [--outdir data/staging] [--delay 1.0] [--max N]`

Process sequence:
1. Fetch OHLCV history from yfinance (`requests` under the hood). A transient
   failure is retried once (`[retry] ...`, 3s pause) before being recorded.
2. Normalize into a pandas DataFrame; force numeric dtypes and parse dates.
3. **numpy** cleaning pass: forward-fill, drop fully-empty rows.
4. Serialize to `<SYMBOL>_<period>.csv` in `--outdir` (default
   `data/staging`) — the file is the checkpoint (resume semantics §5.4).
5. **Failure ledger:** every final download failure or empty-after-cleaning
   result is appended to `data/check_exist/ingest_failures.csv` (header
   `symbol,period,reason,ts`, append-only, best-effort) — the MySQL-free
   audit trail replacing the former `ingest_log` `source=api` error rows.

### 5.3 Sampled snapshot loader — `load_sampled.py`

CLI: `python load_sampled.py [--file data/for_train_y_2025_11_18sample.csv] [--max N]`

Loads the sampled daily snapshot (`data/for_train_y_2025_11_18sample.csv`:
1,476,711 rows, 251 dates, 6,704 ticker symbols) into a **standalone**
table `sampled_market_data` in the same `finance_app` database — no foreign
keys. The archived `data/sampled_184408.csv` (old integer-`ticker_id`
snapshot) is kept on disk but never read.

1. `CREATE TABLE IF NOT EXISTS sampled_market_data` (DDL below, in-script
   only) — PK `(symbol, date)`: the CSV's `Ticker` column (real ticker
   symbols, uppercased) **replaces the old integer `ticker_id` identity**.
2. Read the CSV with pandas **in chunks** (`chunksize` 200k — the file is
   572 MB); rename headers to snake_case; parse the `YYYYMMDD` integer
   `date` column into a MySQL `DATE`; coerce numerics.
3. `Market_Cap` carries human-formatted values (`40.78B` = billion, `M` =
   million, `K` = thousand, `-`/empty = NULL) and is expanded to the actual
   number; a few columns mixing numbers with formatted strings are read as
   `str` to quiet pandas' mixed-type warning.
4. Rows whose `Ticker` cell is empty or the literal `nan` are dropped
   (251 such rows in this file) — no usable identity.
5. Upsert via `INSERT ... ON DUPLICATE KEY UPDATE` covering **every**
   column — idempotent reloads.
6. Write one `ingest_log` row (`source=csv`, `symbol=sample_20251118` —
   the compressed label keeps under `ingest_log.symbol VARCHAR(16)`).

**Identifier quoting:** every column reference is backtick-quoted in the
DDL and SQL because several names are not bare identifiers in MySQL:
`change` (reserved word) and `52w_low` / `52w_high` (leading digits).

**Binary view:** the dataset has no `ChangeY` column, so the binary 0/1
view now derives from the daily `Change` column at **query time** — the
mirror table `change_y_binary` and its loader `load_change_y_binary.py`
no longer exist (§5.5 removed, see §6.3).

### 5.4 Bulk check_exist pipeline — `verify_tickers.py` / `ingest.py` / `load_staging.py`

The checkpoint flow that built the full price-history dataset (12k+
ticker inventory → 7,454 verified symbols → 7,240 exported CSVs):

1. `verify_tickers.py` — serial existence check of
   `data/check_exist/tickerinventory.csv` symbols against yfinance
   (`get_history_metadata`); writes `data/check_exist/verify_ok.csv` /
   `verify_bad.csv`, resumable (skips symbols already in either output).
2. `ingest.py` — **CSV-only bulk export** (see §5.1): fetches full history
   (`--period max` default) for every `verify_ok.csv` symbol and writes one
   `<SYMBOL>_max.csv` into `data/staging/`. Never touches MySQL. Serial,
   ~1 req/s (`--delay 1.0`), resumable: existing files are skipped, the
   newest is re-run. The CSV file is the checkpoint. Download-time failures
   land in `data/check_exist/ingest_failures.csv` (§5.1).
3. `load_staging.py` — loads every `<SYMBOL>_max.csv` from `data/staging/`
   into MySQL (local files only, no network): upserts the `instruments`
   parent row first (`asset_type` = `index` for `^`-prefixed symbols, else
   `equity`), bulk-upserts `price_history` (idempotent
   `symbol`+`trade_date` PK), writes one `ingest_log` row per file. Files
   are never moved or deleted.

**Rejected registry:** every failed symbol (unreadable/malformed CSV **or**
DB error such as MySQL 1264 overflow / `-inf` values) is appended
best-effort and deduped to `data/check_exist/verified_rejected.csv` (header
`symbol`) so failures can be reviewed without grepping logs. Current
registry: 13 symbols.

### 5.5 ~~Change_y binary table~~ — removed (2026-08-12)

The `change_y_binary` mirror table and its loader `load_change_y_binary.py`
**no longer exist**. The dataset swap to
`data/for_train_y_2025_11_18sample.csv` dropped the `ChangeY` column, making
the mirror redundant: `change` now lives in `sampled_market_data` itself,
and the binary 0/1 flag is computed **at query time** by `market_3d`'s
`change_bin` Z channel (§6.3) — `CASE WHEN s.change >= %s THEN 1 ELSE 0 END`
against `sampled_market_data`, no join needed. See `load_sampled.py` (§5.3)
and the `change_binary` metric (§6.3).

### 5.6 Close/Open ratio table — `load_close_open_ratio.py`

CLI: `python load_close_open_ratio.py [--dir data/staging] [--suffix max] [--max N]`

Self-contained loader (same pattern as §5.3) that creates the table
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
   loaded from the `<SYM>_max.csv` staging exports) — the "via primary key"
   connection: `JOIN ... ON p.symbol = r.symbol AND p.trade_date =
   r.trade_date`.
2. Reads every `data/staging/<SYM>_max.csv`, computes `close / open` per
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
- Config whitelist accessors — `get_urls() -> {"main_url", "streamlit_url"}` and `get_bind_host() -> str` — the **only** way the apps read `config.py` (they never import it; §3).

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
    "y": "close"
  },
  "columns": ["trade_date", "open", "high", "low", "close", "adj_close", "volume"],
  "rows": [["2026-07-31", 240.5, 242.0, 238.9, 241.5, 241.5, 50000000]]
}
```

Rules:
- `chart.type` ∈ `line | bar | scatter | candlestick | table` (the types `app_streamlit.envelope_to_figure` implements) — plus `scatter3d` for `market_3d` and `change_binary`, rendered by `app_flask`.
- `market_3d`'s `chart` additionally carries the full column→channel mapping (`z`, `size`, `hover`, `color`) and visual knobs (`colorscale`, `colorbar_title`, `opacity`) so column-assignment changes happen only in the logic layer.
- `rows` are JSON-safe lists aligned to `columns`.
- Errors: `{"status": "error", "meta": {"errors": [...]}, "columns": [], "rows": []}`.

### 6.3 Built-in metrics

| slug | used by | returns |
|---|---|---|
| `history` | Streamlit dashboard | raw OHLCV rows, chronological order oldest-first (line/candlestick); optional `days` trailing window; `limit` (default 250, `0` = no limit) |
| `market_3d` | Flask main page (internal, not user-selectable) | decimated window of OHLCV columns + `chart` metadata mapping columns to the 3D scene; with `source=connected`: `x=symbol`, `y=trade_date` are **fixed** channels, `z/size/color` params override which numeric MySQL column drives each remaining channel (defaults: close/volume/adj_close, validated with fallback to defaults). **`source` param** (see below) switches the table and channels; with `source=sampled` (the page default) the scene maps `x=date` (time), `y` = the selectable channel (default `change_bin`), `z=symbol` (depth) — the **binary view**, a 0/1 flag computed at query time from `threshold` (≥ 0, default 0, negative/unparseable → 0) via `CASE WHEN s.change >= %s THEN 1 ELSE 0 END` against `sampled_market_data.change` (the `change_y_binary` mirror table no longer exists), with `meta` counts (`above`/`total`) for the page summary |
| `change_binary` | standalone (retained for direct calls) | sampled `change` → 0/1 flag at query time: `threshold` (int ≥ 0, default 0, negative/unparseable → 0); `change >= threshold` → 1 else 0; optional `symbols` (ticker symbols), `days` window, `limit`; `chart` = `scatter3d` (x = `symbol`, y = `date`, z = `change`, color = `change_bin`) + `meta` counts (`above`/`total`) — the main page shows the same view via `market_3d`'s `change_bin` default Y-axis mapping |

**`source` param (`market_3d` only):** `sampled` (default) reads
`sampled_market_data` (keyed `(symbol, date)`): `x=date`, `y` = the
selectable channel (default `change_bin`), `z=symbol`, hover = `symbol`;
`z/size/color` channels come from
`SAMPLED_NUMERIC_COLUMNS` (`market_cap`, `52w_low`, `prev_close`,
`price`, `volume`, `52w_high`, `perf_ytd`, `perf_year`, `sma200`,
`perf_half_y`, `avg_volume`, `perf_quarter`, `sma50`, `perf_month`,
`sma20`, `atr`, `rsi_14`, `perf_week`, `rel_volume`, `change`)
with defaults z=`change_bin`, size=`market_cap`, color=`perf_year`; the
**Size and Color dropdowns are restricted subsets** of
`SAMPLED_NUMERIC_COLUMNS` — Size offers `SAMPLED_SIZE_COLUMNS` (price/volume
family — always non-negative, since Plotly marker size requires values
>= 0: `market_cap`, `volume`, `avg_volume`, `prev_close`, `price`,
`52w_low`, `52w_high`, `atr`, `rsi_14`),
Color offers `SAMPLED_COLOR_COLUMNS` (perf/sma family, may be negative:
`perf_ytd` … `perf_year`, `sma20/50/200`, `rel_volume`, `change`) —
`size` validates against `SAMPLED_SIZE_COLUMNS` and `z`/`color` against the
full `SAMPLED_NUMERIC_COLUMNS`; **the Z channel
additionally offers the computed binary channel `change_bin`
(`SAMPLED_CHANNEL_COLUMNS` = `SAMPLED_NUMERIC_COLUMNS` + `change_bin`) —
selecting it computes `CASE WHEN s.change >= %s THEN 1 ELSE 0 END AS
change_bin`
from the `threshold` param (§6.3) directly against
`sampled_market_data.change` (the `change_y_binary` mirror table no longer
exists), plus `meta` above/total counts** (in
sampled mode the Z dropdown drives the scene's Y axis; the depth axis is
always `symbol`); the
`days` window offs from `MAX(date)` of the sampled table; `symbols`
filters ticker symbols (matching `symbol_list("sampled")`, which returns
distinct symbols from `sampled_market_data`). `source=connected`
reads `price_history` instead: `x=symbol`, `y=trade_date`, z/size/color
defaults close/volume/adj_close, symbol listbox from `instruments`.

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

> **To adjust what the main page chart shows (fixed x/y, z/size/color columns, default/empty states): edit only `market_3d` + `symbol_list` in `logic_layer.py`** — the `SELECT` (which columns exist) and its returned `chart` dict. No app file changes. (Restart the Flask server after editing imported modules.) The `source` branch is part of `market_3d`; the per-source channel dropdown options are rendered by `app_flask._channel_options` from the logic-layer constants (`NUMERIC_COLUMNS` for connected; for sampled, Z uses `SAMPLED_CHANNEL_COLUMNS` = `SAMPLED_NUMERIC_COLUMNS` + the computed `change_bin`, Size uses `SAMPLED_SIZE_COLUMNS`, Color uses `SAMPLED_COLOR_COLUMNS` — the latter two are restricted subsets of `SAMPLED_NUMERIC_COLUMNS`, with defaults `market_cap` / `perf_year`).
>
> **To add a new analytics view:** (1) write one `@register` function in `logic_layer.py`, (2) call it via `logic_layer.handle_request` from the app that displays it. The Streamlit dashboard is a fixed price-history view and does not expose a metric menu (§7.2).

---

## 7. Presentation Tier

### 7.1 Flask — MAIN page (`app_flask.py`)

- `GET /` — serves `static/index.html` with the 3D scatter figure injected server-side from the `market_3d` envelope (`app_flask._chart_html` → `logic_layer.handle_request`), rendered without the Plotly modebar toolbar (`config={"displayModeBar": False}`) and with a front-view camera on load (`scene.camera` eye on the depth axis — perpendicular to the x/y plane; users can still rotate). The page defaults to `source=sampled` (no `?source=` param). TTL-cached per `(source, days, symbols, z, size, color, threshold)`.
- **Binary view on the main page:** with `source=sampled` (the default), the Z dropdown renders the computed `change_bin` option (`SAMPLED_CHANNEL_COLUMNS`) and drives the scene's Y axis (x = date, z = symbol); while it is selected a threshold slider (`<input type="range">`, 0–100, step 1) appears in the topbar and reloads `/?threshold=N` (clamped server-side to `0..BINARY_MAX_THRESHOLD`); the slider is hidden otherwise — the page tags the element `hidden` and `static/style.css`'s `.topbar nav > label.hidden` rule enforces it (CSS specificity: without it, `.topbar nav > label`'s `display: flex` would override `.hidden`); the page also renders a one-line summary below the 3D chart from the `market_3d` envelope's `above`/`total` meta counts. There is no separate `/binary` page anymore.
- Main page topbar has a time-range dropdown (Last 30 days default / 60 / 90 / 180 / 365 / **All history**), a multi-select symbol listbox (default: first symbol ticked; `symbols=` = none ticked, `symbols=A,B` = those only; options injected at `<!-- SYMBOLS -->` from `logic_layer.symbol_list()`), and Z/Size/Color dropdowns (numeric-only, validated with fallback). Each change reloads `/?days=…&symbols=…&z=…&size=…&color=…`; `days=` (empty) means all history. `GET /api/config` is also served; the nav bar uses it for the **hyperlink** to the Streamlit secondary page (`FTE_STREAMLIT_URL`).

### 7.2 Streamlit — secondary page (`app_streamlit.py`)

Deliberately barebones: a fixed price-history view, no metric menu.

- Symbol dropdown populated live from `instruments` via `logic_layer.symbol_list()` (`SELECT symbol FROM instruments ORDER BY symbol`) — only ingested symbols appear.
- On load and on symbol change it auto-runs the `history` metric through `logic_layer.handle_request` (same envelope contract, no duplicated query logic) and renders the chart + data table via `envelope_to_figure` (defined in this file, §7.3).
- Window radio: **Last 30 days (default)** / 60 / 90 / 365 / All — windowed choices pass `days=N` with `limit=0` (full window), All omits `days` (full history); no row-count toggle.
- Data table headers are mapped to friendly names (`Trade Date`, `Open`, `High`, `Low`, `Close`, `Adj Close`, `Volume`).
- Empty DB → inline hint to run the ingest pipeline (`python ingest.py`, then `load_staging.py` / `load_close_open_ratio.py`); error envelope → `st.error`.
- "**Return to Main Page**" button — hyperlink styled as a button pointing at `FTE_MAIN_URL`.

### 7.3 `envelope_to_figure` (in `app_streamlit.py`)

- `envelope_to_figure(envelope) -> plotly Figure` — pure mapping from the canonical envelope to a Plotly figure (chart type → `go.Scatter`/`go.Bar`/`go.Candlestick`/`go.Table`).
- Defined inside `app_streamlit.py` (folded in from the former `app_presenter.py`, whose only consumer this was). Flask does **not** use it: its 3D chart is built server-side in `_chart_html` from the `market_3d` envelope's `chart` metadata.
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
python ingest.py --max 5                              # seed a few symbols (then load per §5.4)
python app_flask.py                               # main page  :5000 (binds FTE_BIND_HOST)
streamlit run app_streamlit.py                    # secondary :8501
```

Canonical startup instructions live in **README §7** — the single source of
truth for how to run the apps (including background/`nohup` and LAN forms).

MySQL prerequisites (run once as admin, from the project root):
`mysql -u root -p < setup_finance_app.sql` — creates the `finance_app`
database, the app user with grants, and all three tables. Then fill
`DB_USER` / `DB_PASSWORD` in `.env` (same values as the script).

---

## 10. Security & Error Handling

- All SQL parameterized (`db.query`) — never string-interpolated. SQL injection blocked.
- Secrets only in `.env` (gitignored); nothing secret in code, logs, or repo.
- No direct client-to-DB access; every read goes through the Logic Layer.
- yfinance 429/rate-limit: caught, retried once, else recorded in `data/check_exist/ingest_failures.csv` (MySQL-free ledger).
- Unknown metric / bad params / empty results → error envelope (never a bare 500 or uncaught traceback).
- Flask and Streamlit are stateless; no shared global state.

---

## 11. Verification Plan

| Stage | Test |
|---|---|
| Ingest pipeline | `python ingest.py --max 2` → `data/staging/<SYM>_max.csv` written; `python load_staging.py --max 1` → `ingest_log` has a `csv` `ok` row; rows visible in MySQL |
| Invalid symbol | e.g. `ZZZZ.AA` → `ERROR` printed, appended to `data/check_exist/ingest_failures.csv`, no crash |
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

- Producers: `logic_layer.handle_request(metric, params)` — called by `app_streamlit.py` (`history`) and `app_flask._chart_html` (`market_3d`, including the binary `change_bin` Z channel).
- Envelope shape per §6.2. `chart.type` ∈ `line | bar | scatter | candlestick | table`, plus `scatter3d` for `market_3d` (and the retained `change_binary` metric), rendered by `app_flask`.
- Consumers: `envelope_to_figure` in `app_streamlit.py` (Streamlit) and `app_flask._chart_html` (main page 3D chart, binary included).

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
