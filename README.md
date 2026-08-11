# Financial Analytics Platform — Operator's Manual

This README is the operator's manual: install, configure, run, and operate the
platform. For the full architecture, see `docs/spec.md`.

**Current status (2026-08-09) — resume points:**

| Pipeline stage | State | Evidence |
|---|---|---|
| Symbol verification | DONE — `data/universe/verify_ok.csv` 7,454 / `data/universe/verify_bad.csv` 4,629 | `verify_tickers.py` |
| Full-history export | DONE — 7,452 attempted, 7,240 `_max.csv` in `data/staging2/` (~212 "no data returned") | `data/staging2/` file count (run log not retained) |
| Load into MySQL (full pass) | DONE — 7,227 ok / 13 rejected | `logs/load2.log`: `DONE ok=7227 err=13 rows=32930670 of 7240` |
| Load refresh re-run | CLOSED (2026-08-09 audit) — stopped deliberately at `BIRD` (~790/7,240 files, last_sync 18:08); the refresh was dropped: it reloads the same local CSVs (see "Resume actions") | `logs/load3.log` |
| Rejected registry | DONE — 13 symbols in `data/universe/verified_rejected.csv`; `load_staging2.py` auto-appends failures (deduped) | file + §8 |
| Sampled snapshot load | DONE — 184,408 rows / 32 dates / 5,992 tickers into `sampled_market_data` | `load_sampled.py` + §8 |
| Close/Open ratio table | DONE — `close_open_ratio_chgpct` (PK `symbol`+`trade_date`, `close/open`) loaded from all 7,240 staging2 CSVs | `load_close_open_ratio.py` + §8 |

DB totals: `instruments` 7,338 · `price_history` 32,934,291 rows · `ingest_log`
15,346 ok / 220 error (api 7,251 ok / 205 err; csv 8,095 ok / 15 err — the 13
rejected symbols are unique, rest are dev-phase probes) · `sampled_market_data`
184,408 rows.

**Resume playbook (next session):**

1. Start the apps if they are down: `pgrep -af "app_flask|app_streamlit"`, then
   `curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:5000/` and the
   same on :8501 — restart per §7 if down.
2. Check the registry: `wc -l data/universe/verified_rejected.csv` must be 14 (header + 13
   symbols, no duplicates).
3. No open items remain — the previous refresh/rejection items were closed by
   audit on 2026-08-09 (see "Resume actions" below).

**Resume actions:**

- Closed (2026-08-09, audit) — no longer on the list:
  - Data refresh (`load_staging2.py` full re-run): dropped — it reloads the
    *same local CSVs* (no network, no new data); run-1 already covers all
    7,240 files (7,227 ok + 13 rejected). Only meaningful if
    `ingest_universe.py` is re-exported first.
  - Resume/skip logic in `load_staging2.py`: dropped — it existed only to
    speed up the refresh above; the loader is already idempotent (upsert).
  - Re-run `load_sampled.py` on an updated `data/sampled_184408.csv` with a
    `symbol` column: deferred indefinitely — the file has not arrived and no
    feature joins on `symbol` (the upsert still fills it in place if it ever
    does, see §8).
  - Fix the 13 rejected symbols (value sanitization): closed as won't-fix —
    the CSVs contain corrupt yfinance values (`open/high/low` up to ~5e14
    vs. the `DECIMAL(18,6)` cap ~1e12; `adj_close` `-inf` / `-1e28`), the
    failure re-verified in `logs/load3.log`, and the symbols are micro-cap
    junk invisible on the decimated chart. The registry
    (`data/universe/verified_rejected.csv`) is kept as the reviewable record.

---

## 1. Overview

The platform ingests market data (equity + indices) from yfinance, stores
it in MySQL, computes analytics in a single replaceable
**Logic Layer** (`logic_layer.py`), and presents it through two UIs:

- **Main page** — Flask, server-rendered 3D market chart
  (http://127.0.0.1:5000) without the Plotly modebar toolbar; all
  query/filter/column-mapping logic lives in
  the `market_3d` metric in `logic_layer.py`; a **Data source** toggle in
  the topbar switches between the connected MySQL tables
  (`price_history`, `source=connected`) and the sampled snapshot dataset
  (`sampled_market_data`, `source=sampled`); in sampled mode the Z
  dropdown offers the **binary view** (`change_y_bin`, a 0/1 flag from
  `change_y > threshold` with a topbar threshold slider)
- **Dashboard** — Streamlit secondary page, linked from the main page
  (http://127.0.0.1:8501), with a "Return to Main Page" button

Data flow: `ingest_api.py` / `load_sampled.py` → MySQL
(`db.py`) → `logic_layer.py` (`history` + `market_3d` metrics) → Flask /
Streamlit → browser.

---

## 2. Project layout

```
midtermproject2/
├── .env                  # secrets (DB_USER / DB_PASSWORD) — gitignored
├── .env.example          # template with all defaults
├── requirements.txt      # Python dependencies
├── setup_finance_app.sql # one-time MySQL bootstrap: DB + user + tables
├── schema.sql            # EMPTY by design — future MySQL DDL lives here
├── verify_tickers.py     # checks tickerinventory.csv symbols against yfinance
├── config.py             # loads .env → CFG dict
├── db.py                 # MySQL connection + query/insert/schema helpers
├── ingest_api.py         # yfinance → cleaning → staging CSV → MySQL
├── ingest_universe.py    # bulk export: every data/universe/verify_ok.csv symbol, max history → data/staging2/
├── load_staging2.py      # loads data/staging2/ CSVs into MySQL
├── load_sampled.py       # self-contained: creates sampled_market_data + upserts data/sampled_184408.csv
├── load_change_y_binary.py # self-contained: creates change_y_binary (PK ticker_id+date) from the same CSV
├── load_close_open_ratio.py # self-contained: creates close_open_ratio_chgpct (PK symbol+trade_date, close/open) from data/staging2/
├── logic_layer.py        # THE logic layer: metric registry + envelopes
├── app_presenter.py      # envelope → Plotly figure (shared)
├── app_flask.py          # Flask main app (server-rendered page + /api/config)
├── app_streamlit.py      # Streamlit dashboard
├── static/
│   ├── index.html        # main page DOM
│   └── style.css         # layout + dark/light themes
├── docs/                 # spec + architecture docs
├── logs/                 # runtime logs (flask.log, streamlit.log, load2/load3.log)
└── data/
    ├── universe/         # tickerinventory.csv, verify_ok.csv, verify_bad.csv, verified_rejected.csv
    ├── sampled_184408.csv # sampled snapshot dataset (loaded by load_sampled.py)
    ├── staging/          # API ingestion staging CSVs
    ├── staging2/         # full-history export checkpoints (<SYM>_max.csv)
```

---

## 3. Prerequisites

- Python 3.12 (with `venv` available)
- MySQL server running locally (`systemctl status mysql` → `active`)
- Nothing else — no Docker, no Node, no build tools

---

## 4. Installation

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

---

## 5. Configuration (`.env`)

Copy the template and fill **only the two credential values** — everything
else already has working defaults:

```bash
cp .env.example .env
```

| Key | Default | Your job |
|---|---|---|
| `DB_HOST` | `localhost` | — |
| `DB_PORT` | `3306` | — |
| `DB_NAME` | `finance_app` | — |
| `DB_USER` | *(blank)* | **fill** |
| `DB_PASSWORD` | *(blank)* | **fill** |
| `FTE_STAGING_DIR` | `data/staging` | — |
| `FTE_UPLOAD_DIR` | `data/uploads` | — |
| `FTE_PROCESSED_DIR` | `data/processed` | — |
| `FTE_REJECTED_DIR` | `data/rejected` | — |
| `FTE_BIND_HOST` | `127.0.0.1` (config.py default) | set `0.0.0.0` for LAN access — the shipped `.env.example` already ships `0.0.0.0` |
| `FTE_MAIN_URL` | `http://127.0.0.1:5000` | — |
| `FTE_STREAMLIT_URL` | `http://127.0.0.1:8501` | — |

Do not put quotes around values; avoid `#` and spaces in the password.
Never commit `.env` (it is gitignored).

---

## 6. Database setup (operator checklist)

The one-time bootstrap is automated by `setup_finance_app.sql` — it creates
the database, the app user, its grants, and all three tables in a single
pass. It is idempotent (`IF NOT EXISTS`), so re-running is safe.

Run once as a MySQL admin (from this directory, root with your password):

```bash
mysql -u root -p < setup_finance_app.sql
```

What it creates:

- database `finance_app` (utf8mb4)
- MySQL user `user`@`localhost` (password `user`) with all privileges on
  `finance_app.*`
- tables `instruments`, `price_history`, `ingest_log` (DDL per `docs/spec.md`
  Appendix A)

The sampled snapshot table `sampled_market_data` is **not** created here —
`load_sampled.py` creates it itself with `CREATE TABLE IF NOT EXISTS`
(see §8) and loads `data/sampled_184408.csv` into it.

To use different credentials, edit the `CREATE USER` line in
`setup_finance_app.sql` and set the same values in `.env`.

Then fill the two credentials in `.env`:

```
DB_USER=user
DB_PASSWORD=user
```

Verify connectivity:

```bash
source venv/bin/activate
python -c "import db; print(db.query('SELECT 1 AS ok'))"
```

Expected: `[{'ok': 1}]`.

`schema.sql` remains an **empty placeholder** reserved for future MySQL DDL
(spec Rule 2); once populated it can be applied with
`python -c "import db; db.execute_schema()"`. The setup script above is the
primary path and already creates the tables.

If you see `Access denied for user 'root' (using password: YES)`, do **not**
keep trying root — create the dedicated `user` account via the script above
instead (Ubuntu MySQL often uses `auth_socket` for root, which rejects
passwords).

---

## 7. Running the app

> These are the canonical startup commands — the single source of truth.
> Every other section (and `docs/spec.md`) refers back here.

Terminal A — main page (Flask):

```bash
source venv/bin/activate
python app_flask.py
```

Expected:

```
 * Serving Flask app 'app_flask'
 * Debug mode: off
WARNING: This is a development server. Do not use it in a production deployment.
   Use a production WSGI server instead.
 * Running on http://127.0.0.1:5000
Press CTRL+C to quit
```

→ open http://127.0.0.1:5000

`python app_flask.py` binds `FTE_BIND_HOST` from `.env` (default
`127.0.0.1`; set it to `0.0.0.0` for LAN access, see below). Use
`flask --app app_flask run` only if you need Flask's default
127.0.0.1-only binding.

Terminal B — dashboard (Streamlit):

```bash
source venv/bin/activate
streamlit run app_streamlit.py
```

Expected:

```
  You can now view your Streamlit app in your browser.

  Local URL: http://localhost:8501
  Network URL: http://192.168.21.161:8501
  External URL: http://1.160.7.205:8501
```

→ open http://127.0.0.1:8501

Both at once in one terminal:

```bash
python app_flask.py & streamlit run app_streamlit.py &
```

Stop with `Ctrl+C` (or `kill %1 %2`).

### Accessing from the local network (other host)

By default the apps bind to this machine only. To reach them from another
machine on the LAN (e.g., from the host of a VM):

1. Set in `.env`:
   ```
   FTE_BIND_HOST=0.0.0.0
   FTE_MAIN_URL=http://<THIS-MACHINE-LAN-IP>:5000
   FTE_STREAMLIT_URL=http://<THIS-MACHINE-LAN-IP>:8501
   ```
2. Start Flask with `python app_flask.py` (it binds `FTE_BIND_HOST`), and
   Streamlit with `streamlit run app_streamlit.py --server.address 0.0.0.0`
   (per §7 commands).
3. From the other machine open `http://<LAN-IP>:5000` and
   `http://<LAN-IP>:8501`. The nav links between the two pages use
   `FTE_MAIN_URL` / `FTE_STREAMLIT_URL`, so both directions work.

Get the LAN IP with `ip -4 addr` (e.g. `192.168.21.161`).

### Start the app over SSH

Connect to the host from your own machine and run everything there, so the
servers live next to the data:

```bash
ssh student@192.168.21.161
cd Desktop/AIPE04_midterm
source venv/bin/activate
```

Prerequisites (the LAN section above covers these):

- `.env` has `FTE_BIND_HOST=0.0.0.0`
- `FTE_MAIN_URL` / `FTE_STREAMLIT_URL` use the host's LAN IP, **not**
  `127.0.0.1`, or the "Dashboard ->" / "Return to Main Page" links will be
  dead in your browser.

Launch both servers as background jobs. `nohup` keeps them alive after you
close the SSH session and writes each log into `logs/`:

```bash
nohup python app_flask.py > logs/flask.log 2>&1 &
nohup streamlit run app_streamlit.py --server.address 0.0.0.0 --server.headless true > logs/streamlit.log 2>&1 &
```

The launch itself prints nothing (each shell prints a job id like `[1] 3476`).
Confirm both servers are up by reading the logs:

```bash
sleep 3 && cat logs/flask.log && echo --- && cat logs/streamlit.log
```

- `logs/flask.log` ends with ` * Running on all addresses (0.0.0.0)` /
  ` * Running on http://192.168.21.161:5000`
- `logs/streamlit.log` ends with `You can now view your Streamlit app in your
  browser.` and the `Local URL` / `Network URL` lines (see §7)

Sanity-check from the SSH session:

```bash
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:5000/
```

Expected: `200` — the main page renders its 3D chart server-side from the
`market_3d` metric envelope.

Then open `http://192.168.21.161:5000` (main page) and
`http://192.168.21.161:8501` (dashboard) in your own browser.

Stop the servers later, from a fresh SSH session:

```bash
pkill -f app_flask.py; pkill -f app_streamlit.py
```

Under `tmux` or `screen` you can leave the servers attached instead and
restart them interactively (no `nohup` needed).

---

## 8. Operating the data

**API ingestion** (any valid yfinance symbol; indices use `^`, e.g. `^GSPC`):

```bash
python ingest_api.py --symbol AAPL --period 1y
python ingest_api.py --symbol ^GSPC --period 1y
```

Result: staging CSV in `data/staging/`, rows in `price_history`, a log row in
`ingest_log`. Re-running is idempotent (upsert). Invalid symbols are skipped
and logged, never a crash.

Expected console output (one line per symbol):

```
OK: AAPL -> 251 rows (staging: data/staging/AAPL_1y.csv)
OK: ^GSPC -> 251 rows (staging: data/staging/^GSPC_1y.csv)
```

An invalid symbol prints `ERROR: no data returned (invalid symbol or empty
range)` and logs an `ingest_log` row with status `error` — no crash.

**Bulk export (full universe, CSV-only):**

`ingest_universe.py` is a CSV-only export — it **never touches MySQL**. It
fetches each symbol's full history (`--period max`, default) and writes one
CSV per symbol into `data/staging2/`.

```bash
python ingest_universe.py                 # every symbol in data/universe/verify_ok.csv, max period
python ingest_universe.py --period 5y     # shorter range if you prefer
python ingest_universe.py --max 5         # smoke test: first 5 remaining symbols
```

Serial and rate-safe: one symbol at a time with a 1s delay (`--delay 1.0`,
default) — ~1 req/s, well under Yahoo's rate cap. Each symbol's CSV in
`data/staging2/` is written immediately and doubles as the checkpoint. Files in
`data/staging/` are never read, written, or deleted.

Expected output (one line per symbol):

```
OK: A -> 6716 rows (data/staging2/A_max.csv)
```

**Resume:** re-run the same command. Existing `<SYMBOL>_max.csv` files in
`data/staging2/` are skipped (other periods/`data/staging` never count); the
newest file is re-run to ensure it is complete. The full 7,400+-symbol run
takes several hours — run it under `nohup`/tmux:

```bash
nohup python ingest_universe.py > logs/seed2.log 2>&1 &
```

**Load staging2 exports into MySQL:**

Once the export above has finished, load the full-history CSVs into the
database with `load_staging2.py` (local files only — no network, no delay
needed):

```bash
python load_staging2.py --max 5          # smoke test: first 5 files
python load_staging2.py                  # load all data/staging2/*_max.csv
```

Per file: upserts `instruments` (parent first), bulk-upserts
`price_history` (idempotent `symbol`+`trade_date` PK), and writes an
`ingest_log` row. Files are never moved or deleted.

Every failed symbol (unreadable/malformed CSV **or** DB error such as MySQL
1264 overflow / `-inf` values) is appended to `data/universe/verified_rejected.csv`
(header `symbol`, deduped, best-effort) so failures can be
reviewed without relying on the log.

Expected output (one line per file):

```
OK: A -> 6716 rows (data/staging2/A_max.csv)
DONE ok=7240 err=0 rows=... of 7240
```

**Sampled snapshot dataset:**

`load_sampled.py` is self-contained: it creates the `sampled_market_data`
table in the same `finance_app` database (`CREATE TABLE IF NOT EXISTS`,
DDL lives only in the script) and bulk-upserts `data/sampled_184408.csv`
(184,408 rows · 32 dates · 5,992 ticker_ids). It has no foreign keys —
it is a standalone snapshot table.

```bash
python load_sampled.py --max 5          # smoke test: first 5 rows
python load_sampled.py                  # load the whole file
```

- PK is `(ticker_id, date)`; upserts are idempotent — re-running never
  duplicates rows and writes one `ingest_log` row per run.
- `date` is parsed from the CSV's `YYYYMMDD` integers into a real MySQL
  `DATE` column.
- The table carries a nullable `symbol VARCHAR(16)` column (indexed via
  `idx_symbol`) that is **NULL** in the current file. When the updated CSV
  with a `symbol` column appended is dropped in, re-running the same command
  fills `symbol` in place (the upsert updates every column, including
  `symbol`), ready for a later `JOIN` against `instruments.symbol`.
- Identifier gotcha handled in the code: MySQL reserved words / digit-leading
  names (`change`, `52w_low`, `52w_high`) are backtick-quoted everywhere.

Expected output:

```
OK: data/sampled_184408.csv -> 184408 rows (symbols filled: 0)
```

Verify in MySQL:

```sql
SELECT COUNT(*), COUNT(DISTINCT date), COUNT(DISTINCT ticker_id),
       COUNT(symbol), MIN(date), MAX(date) FROM sampled_market_data;
-- 184408 | 32 | 5992 | 0 | 2025-09-10 | 2025-10-23
```

**Change_y binary table (`change_y_binary`):**

`load_change_y_binary.py` mirrors `load_sampled.py`: it creates
`change_y_binary` (`CREATE TABLE IF NOT EXISTS`, DDL in the script) with the
**same PK `(ticker_id, date)`** as `sampled_market_data` plus a nullable
`symbol` column, and bulk-upserts the sampled CSV's `change_y` column
(idempotent; one `ingest_log` row per run). It stores **only the raw
value** — the binary conversion happens at **query time**.

```bash
python load_change_y_binary.py --max 5          # smoke test: first 5 rows
python load_change_y_binary.py                  # load the whole file
```

Expected output:

```
OK: data/sampled_184408.csv -> 184408 rows (symbols filled: 0)
```

**Close/Open ratio table (`close_open_ratio_chgpct`):**

`load_close_open_ratio.py` mirrors `load_staging2.py` in reverse: it reads
every `data/staging2/<SYM>_max.csv` (the "symbol_max" exports), computes the
per-row ratio `close / open`, and upserts it into `close_open_ratio_chgpct`
(`CREATE TABLE IF NOT EXISTS`, DDL in the script). It carries the **same PK
`(symbol, trade_date)`** as `price_history` — the intended access pattern is a
primary-key `JOIN` against `price_history` (`ON p.symbol = r.symbol AND
p.trade_date = r.trade_date`) — plus `INDEX idx_date (trade_date)`.

```bash
python load_close_open_ratio.py --max 5          # smoke test: first 5 files
python load_close_open_ratio.py                  # load all data/staging2/*_max.csv
```

- Value stored raw as the ratio (`close/open`, `DECIMAL(18,6)`); rows with
  `open` = 0 / NaN / non-finite, or ratios that would overflow
  `DECIMAL(18,6)` (~1e12), are skipped — the 13 corrupt symbols load the rows
  that are valid and drop the rest.
- Idempotent upserts on `(symbol, trade_date)` — re-running never duplicates
  rows; one `ingest_log` row per file (source `csv`, symbol = ticker).
- Files are never moved or deleted.

Expected output (one line per file):

```
OK: A -> 6716 rows (data/staging2/A_max.csv)
DONE ok=7240 err=0 rows=... of 7240
```

**Binary view (main page, `source=sampled`):** the binary view is
integrated into the main :5000 page — no separate `/binary` page. With
`source=sampled`, the **Z dropdown** offers the computed channel
`change_y_bin`: the 0/1 flag (`change_y > threshold -> 1`, `change_y <=
threshold -> 0`) is computed **at query time** by `market_3d` in SQL
(`CASE WHEN b.change_y > %s THEN 1 ELSE 0 END`, `LEFT JOIN change_y_binary`
on the shared `(ticker_id, date)` key). A **threshold slider** (0–100,
step 1, default 0) appears in the topbar only while `change_y_bin` is
selected as Z; it sends `?threshold=N` (negative or unparseable values
fall back to 0, clamped 0–100). Below the 3D chart, a one-line summary
shows ("N rows · M above threshold (p%)"). The `change_y_binary` metric in
`logic_layer.py` is retained as a standalone metric for direct calls.

---

## 9. The logic layer (the contract)

All analytics logic lives in **`logic_layer.py`** — no app file queries
MySQL or decides what a chart shows.

Registered metrics (exactly what the apps display):

| Metric | Used by |
|---|---|
| `history` | Streamlit dashboard (fixed price-history view) |
| `market_3d` | Flask main page 3D chart (internal — not user-selectable); `source=sampled` Z channel `change_y_bin` is the binary 0/1 view (`change_y > threshold -> 1 / else 0` computed at query time from the `threshold` param, ≥ 0) |
| `change_y_binary` | standalone metric retained for direct calls — `change_y > threshold -> 1 / else 0` at query time from the `threshold` param (≥ 0); the main page shows the same via `market_3d`'s `change_y_bin` Z channel |

The main page 3D chart has **fixed channels**: X = `symbol`, Y =
`trade_date`, hover = `symbol`. Z/Size/Color are user-selectable dropdowns
sent as `z/size/color` query params and validated against `NUMERIC_COLUMNS`
(bad or missing values fall back to the defaults: Z = `close`, Size =
`volume`, Color = `adj_close`). The time range dropdown maps to an
**absence-based** `days` param: the UI always sends an explicit `30`
(default) /60/90/180/365, and "All history" sends `days=` (empty) so the
metric applies no window. The symbol listbox ticks/unticks symbols via the
`symbols` param (`symbols=` = none ticked, empty chart).

**Data source toggle** (`source` query param): the main page's "Data
source" dropdown switches which table `market_3d` reads.

- `source=sampled` (default) — `sampled_market_data`: x = `date`, y = the
  selectable channel (default `change_y_bin`), z = `ticker_id` (depth),
  channels from `SAMPLED_NUMERIC_COLUMNS` (market_cap, price, rsi_14,
  change, …) with defaults Y = `change_y_bin`, Size = `volume`, Color = `change`;
  the Z dropdown additionally offers the computed binary channel
  `change_y_bin` (`SAMPLED_CHANNEL_COLUMNS`); the symbol listbox lists
  ticker_ids instead (`symbol_list("sampled")`), `symbols=4405` filters one
  ticker, and `days` windows off `MAX(date)` from the sampled table.
- `source=connected` — existing behavior: `price_history`, x = `symbol`,
  y = `trade_date`, channels from `NUMERIC_COLUMNS`, symbol listbox from
  `instruments` (`symbol_list("connected")`).

The channel dropdown options are rendered per source by `app_flask.py`
(`_channel_options`), and the rendered-chart cache key includes `source`
and `threshold`.

To change which view is default, the window options, or the channel
defaults:

1. Edit `market_3d` / `symbol_list` in `logic_layer.py` and the UI defaults
   in `app_flask.py` / `static/index.html`,
2. restart the Flask server (changes to imported modules require restarts).

To add a new analytics view: write one `@register` function in
`logic_layer.py` returning the canonical envelope (see `history`), and call
it via `logic_layer.handle_request` — no app file changes.

---

## 10. API reference

| Endpoint | Purpose |
|---|---|
| `GET /` | main page HTML (3D chart rendered server-side); query params `days`, `symbols`, `z/size/color`, `source` (`sampled` default \| `connected`), and `threshold` (binary Z channel, default 0, clamped 0–100) |
| `GET /api/config` | main/Streamlit URLs (used by the main page nav link) |

---

## 11. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `Access denied for user 'root' (using password: YES)` | root uses `auth_socket` or wrong password → run `setup_finance_app.sql` (section 6) to create the dedicated app user and set it in `.env` |
| `Table 'finance_app.price_history' doesn't exist` | tables not created → run `mysql -u root -p < setup_finance_app.sql` (section 6) |
| Charts still using old code after an edit | Streamlit only hot-reloads the main script (`app_streamlit.py`); changes to imported modules (`logic_layer.py`, `app_presenter.py`, `db.py`) require restarting both servers |
| `no data returned (invalid symbol or empty range)` | wrong/unlisted yfinance symbol → try `AAPL` or `^GSPC`; check with `period=5d` |
| Charts empty on both pages | no data ingested → run `ingest_api.py --symbol AAPL --period 1y` first |
| `HTTP 404` on port 8501 | Streamlit not running → restart it per §7 (`streamlit run app_streamlit.py`) |
| `429` rate-limit from yfinance | transient — the pipeline retries once and logs; wait and re-run |
| Dashboard "Return to Main Page" dead | `FTE_MAIN_URL` mismatch or Flask not running on 5000 |
| App dies when I close the SSH session | background jobs need `nohup` (or run under `tmux`/`screen`) — see §7 "Start the app over SSH" |
| Sampled mode shows `ProgrammingError 1064` | server running an old `logic_layer.py` (reserved/digit-leading columns like `change`, `52w_*` must be backtick-quoted) → restart Flask |

---

## 12. Verification checklist

```bash
python -c "import config, db, logic_layer, app_presenter, app_flask"   # no output = imports clean
python -c "import logic_layer; print(logic_layer.handle_request('history',   {'symbol':'AAPL','limit':0})['status'])"
python -c "import logic_layer; print(logic_layer.handle_request('history',   {'symbol':'AAPL','days':30,'limit':0})['status'])"
python -c "import logic_layer; print(logic_layer.handle_request('market_3d', {'days':90})['status'])"
python -c "import logic_layer; print(logic_layer.handle_request('market_3d', {'days':90,'symbols':'AAPL,MSFT'})['status'])"
python -c "import logic_layer; print(logic_layer.symbol_list())"        # e.g. ['AAPL', 'MSFT', ...]
curl -s -o /dev/null -w "%{http_code}\n" "http://127.0.0.1:5000/?days=90&symbols=AAPL"
python ingest_api.py --symbol AAPL --period 5d                          # OK: AAPL -> N rows (staging: data/staging/AAPL_5d.csv)
python load_staging2.py --max 5                                         # smoke load; failures auto-recorded to data/universe/verified_rejected.csv
python load_sampled.py --max 5                                          # smoke load: first 5 rows -> sampled_market_data
python load_change_y_binary.py --max 5                                 # smoke load: first 5 rows -> change_y_binary
python load_close_open_ratio.py --max 5                                # smoke load: first 5 files -> close_open_ratio_chgpct
python -c "import logic_layer; print(logic_layer.handle_request('market_3d', {'source':'sampled','days':30,'symbols':'4405','z':'rsi_14'})['status'])"
python -c "import logic_layer; print(logic_layer.handle_request('market_3d', {'source':'sampled','days':30,'z':'change_y_bin','threshold':0})['status'])"
python -c "import logic_layer; print(logic_layer.handle_request('market_3d', {'source':'sampled','days':30,'z':'change_y_bin','threshold':10})['status'])"
python -c "import logic_layer; print(logic_layer.handle_request('market_3d', {'source':'sampled','days':30,'z':'change_y_bin','threshold':-5})['status'])"   # negative -> falls back to 0
python -c "import logic_layer; print(logic_layer.symbol_list('sampled'))"  # e.g. ['0', '1', '2', ...] ticker_ids
curl -s -o /dev/null -w "%{http_code}\n" "http://127.0.0.1:5000/?source=sampled&days=30&symbols=4405"
curl -s -o /dev/null -w "%{http_code}\n" "http://127.0.0.1:5000/?source=sampled&days=30&z=change_y_bin&threshold=10"
```

`data/universe/verified_rejected.csv` must contain exactly the header `symbol` plus the 13
rejected symbols (`ADTX, CUBT, CYDX, DTII, NICH, NUWE, NXPL, PADEF, PBNNF,
PPCB, PTPIF, SIGO, TOPS`) with no duplicates — and must not grow on a
repeat load (dedupe check).

Then open http://127.0.0.1:5000 — the page loads in **sampled** mode by
default and the 3D market chart renders in front view (camera perpendicular
to the x/y plane: date × change_y_bin × ticker_id); confirm the time-range
select works. Switch the **Data source** dropdown to "Connected MySQL" and
back — the chart should use the sampled channel defaults with the 0/1 flag
on the vertical axis, the threshold slider visible, and the summary line
showing the above-threshold count. Switch to the dashboard (nav
link) and confirm the same data renders there with a working "Return to
Main Page" button.
# AIPE04_midterm
