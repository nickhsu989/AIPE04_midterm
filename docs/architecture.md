# Architecture — relationships between the project `.py` files

Nodes are grouped by their **folder** (the diagram mirrors the real layout).
Solid arrows = direct import/usage (or file handoff). Dashed arrows = failure path / code reuse.
Every script reads settings from `config.py` (loaded via `.env`) — one representative edge is shown.
Not shown: `venv/`, `__pycache__/` (no relationships), and the root-level non-code files
`.env`, `.env.example`, `requirements.txt`, `schema.sql`, `setup_finance_app.sql`, `.gitignore`.

```mermaid
flowchart TD
    subgraph ROOT["midtermproject2/ (root)"]
        direction LR
        fl["app_flask.py"]
        sl["app_streamlit.py"]
        pres["app_presenter.py"]
        ll["logic_layer.py"]
        api["ingest_api.py"]
        lc["loader_csv.py"]
        v["verify_tickers.py"]
        u["ingest_universe.py"]
        l2["load_staging2.py"]
        lratio["load_close_open_ratio.py"]
        lsam["load_sampled.py"]
        lbin["load_change_y_binary.py"]
        db["db.py"]
        cfg["config.py"]
    end
    subgraph STATIC["static/"]
        idx["index.html"]
        css["style.css"]
    end
    subgraph DOCS["docs/"]
        spec["spec.md"]
        arch["architecture.md"]
        adesc["architecture_description.txt"]
        png["architecture PNG"]
    end
    subgraph LOGS["logs/"]
        flog["flask.log"]
        slog["streamlit.log"]
        l2log["load2.log"]
        l3log["load3.log"]
    end
    subgraph DATA["data/"]
        direction LR
        smp["sampled_184408.csv"]
        subgraph UNIV["universe/"]
            ti["tickerinventory.csv"]
            vok["verify_ok.csv"]
            vbad["verify_bad.csv"]
            rej2["verified_rejected.csv"]
        end
        subgraph STAG["staging/"]
            stg["&lt;SYM&gt;_1y.csv"]
        end
        subgraph STAG2["staging2/"]
            s2["&lt;SYM&gt;_max.csv"]
        end
        subgraph UP["uploads/"]
            up["*.csv"]
        end
        subgraph PROC["processed/"]
            prc["*.csv"]
        end
        subgraph REJ["rejected/"]
            rj["*.csv"]
        end
    end
    yf["yfinance API"]
    mysql[("MySQL finance_app: instruments · price_history · ingest_log · sampled_market_data · change_y_binary · close_open_ratio_chgpct")]

    yf --> v
    yf --> api
    smp --> lsam
    smp --> lbin
    up --> lc
    v --> vok
    v --> vbad
    vok --> u
    u --> s2
    s2 --> l2
    s2 --> lratio
    l2 -.-> rej2
    u -.-> api
    api --> stg
    lc --> prc
    lc --> rj
    l2 --> db
    lratio --> db
    lsam --> db
    lbin --> db
    api --> db
    lc --> db
    db --> mysql
    db --> cfg
    ll --> db
    fl --> ll
    sl --> ll
    sl --> pres
    sl --> db
    fl -.-> idx
    idx --> css
```

## Legend

| Node | Role |
|------|------|
| `app_flask.py` | Flask main page: 3D market chart, serves `static/index.html` |
| `app_streamlit.py` | Streamlit dashboard, reuses the same logic-layer metrics |
| `app_presenter.py` | Shared helper: Logic Layer envelope → Plotly figure |
| `logic_layer.py` | THE Logic Layer: metric registry, canonical envelopes |
| `verify_tickers.py` | Existence check of universe symbols on yfinance → `data/universe/verify_ok.csv` / `verify_bad.csv` |
| `ingest_universe.py` | Bulk full-history export to `data/staging2/` (CSV-only, resumable) |
| `load_staging2.py` | Loads `data/staging2/` CSVs into MySQL; failures → `data/universe/verified_rejected.csv` |
| `load_close_open_ratio.py` | Computes `close/open` per row from `data/staging2/` CSVs into `close_open_ratio_chgpct` (PK `(symbol, trade_date)`, joins to `price_history` by primary key) |
| `load_sampled.py` | Self-contained loader: creates `sampled_market_data` + upserts `data/sampled_184408.csv` |
| `load_change_y_binary.py` | Self-contained loader: creates `change_y_binary` (PK `(ticker_id, date)`, mirrors `sampled_market_data`) from the same CSV |
| `ingest_api.py` | Single-symbol yfinance pipeline (pandas + numpy cleaning → staging CSV → MySQL) |
| `loader_csv.py` | Bulk CSV upload engine (polls `data/uploads/` → `processed/` \| `rejected/`) |
| `db.py` | Centralized MySQL access (backend only; UIs never touch MySQL directly) |
| `config.py` | Single source of truth for settings (`.env`) |

## The ingestion pipelines

**Pipeline A — bulk universe** (the checkpoint flow):

`verify_tickers.py` → `data/universe/verify_ok.csv` → `ingest_universe.py` → `data/staging2/<SYM>_max.csv` → `load_staging2.py` → MySQL

- `ingest_universe.py` reuses `clean()` / `fetch_history()` from `ingest_api.py`
- per-symbol CSV = the checkpoint (resume-safe, re-runs the newest file)
- failed loads are recorded in `data/universe/verified_rejected.csv` (never deleted)
- `data/staging2/<SYM>_max.csv` → `load_close_open_ratio.py` → MySQL
  `close_open_ratio_chgpct` (PK `(symbol, trade_date)`, same key shape as
  `price_history`; stores the per-row `close / open` ratio for a
  primary-key `JOIN` against `price_history`).

**Pipeline B — on-demand / manual** (two independent feeds into MySQL):

- `ingest_api.py` → `data/staging/<SYM>.csv` → MySQL (single symbol, e.g. `--symbol AAPL --period 1y`)
- `loader_csv.py` ← `data/uploads/*.csv` → `data/processed/` (success) | `data/rejected/` (failure)

**Pipeline C — sampled snapshot** (standalone tables, no foreign keys):

- `data/sampled_184408.csv` → `load_sampled.py` → MySQL `sampled_market_data`
  (PK `(ticker_id, date)`; nullable `symbol` column, currently NULL, ready for
  a later `JOIN` against `instruments`; read by the Flask main page via
  `market_3d` with `source=sampled`).
- `data/sampled_184408.csv` → `load_change_y_binary.py` → MySQL
  `change_y_binary` (PK `(ticker_id, date)`, same key shape as
  `sampled_market_data`; stores the raw `change_y` column only). The binary
  0/1 conversion (`change_y > threshold -> 1`) is computed **at query time**
  by the `market_3d` logic-layer metric's `change_y_bin` Z channel on the
  main page (sampled source; threshold slider 0–100, must be ≥ 0).

Pipelines A and B upsert into `instruments` + `price_history` and write one
`ingest_log` row per run, all via `db.py` (Pipeline C also writes one
`ingest_log` row per run but targets only `sampled_market_data`).

Notes:
- `verify_tickers.py` and `app_presenter.py` are standalone — no imports from other project files.
- `app_streamlit.py` is the only presentation file that imports `db.py` directly (symbol list for its selectbox).
- UIs (Flask/Streamlit) are presentation-only; all SQL lives behind `logic_layer.py` / `db.py`.
