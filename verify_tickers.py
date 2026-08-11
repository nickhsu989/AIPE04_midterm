"""verify_tickers.py — serial check that every symbol in tickerinventory.csv
exists on yfinance.

A symbol exists if yf.Ticker(sym).get_history_metadata() does not raise.
Rate limits get one retry after 30s; anything else means not found.

On start it reads its own output files to see how far it has already got
against the full list, then continues from there (per-iteration writes, so a
kill loses at most one symbol). Outputs are fixed-name files that never
overlap the main verified_*.csv outputs; delete them to re-run from scratch.

Outputs:
  verify_ok.csv    (exists)
  verify_bad.csv   (not found)
"""
import os
import time

import pandas as pd
import yfinance as yf
from yfinance.exceptions import YFRateLimitError

UNIVERSE_DIR = os.path.join(os.path.dirname(__file__), "data", "universe")
SRC = os.path.join(UNIVERSE_DIR, "tickerinventory.csv")
OUT_OK = os.path.join(UNIVERSE_DIR, "verify_ok.csv")
OUT_BAD = os.path.join(UNIVERSE_DIR, "verify_bad.csv")


def load(path):
    if os.path.exists(path):
        cols = pd.read_csv(path, header=None, dtype=str, keep_default_na=False)[0]
        return set(t for t in cols.str.strip().str.upper() if t and t != "SYMBOL")
    return set()


def main():
    tickers = sorted(set(pd.read_csv(SRC, header=None)[0].dropna().str.strip().str.upper()))
    done = load(OUT_OK) | load(OUT_BAD)
    todo = [t for t in tickers if t not in done]
    print(f"total={len(tickers)} already_done={len(done)} todo={len(todo)}", flush=True)
    print(f"OK  -> {OUT_OK}", flush=True)
    print(f"BAD -> {OUT_BAD}", flush=True)
    if not todo:
        print("nothing left to check", flush=True)
        return

    n_ok = n_bad = 0
    with open(OUT_OK, "a") as f_ok, open(OUT_BAD, "a") as f_bad:
        if os.path.getsize(OUT_OK) == 0:
            f_ok.write("symbol\n")
        if os.path.getsize(OUT_BAD) == 0:
            f_bad.write("symbol\n")
        for i, t in enumerate(todo, 1):
            try:
                yf.Ticker(t).get_history_metadata()
                f_ok.write(f"{t}\n")
                f_ok.flush()
                n_ok += 1
            except YFRateLimitError:
                time.sleep(30)
                try:
                    yf.Ticker(t).get_history_metadata()
                    f_ok.write(f"{t}\n")
                    f_ok.flush()
                    n_ok += 1
                except Exception:
                    f_bad.write(f"{t}\n")
                    f_bad.flush()
                    n_bad += 1
            except Exception:
                f_bad.write(f"{t}\n")
                f_bad.flush()
                n_bad += 1
            time.sleep(0.3)
            if i % 1000 == 0:
                print(f"{i}/{len(todo)} ok={n_ok} bad={n_bad}", flush=True)

    print(f"DONE verified={n_ok} unavailable={n_bad}", flush=True)


if __name__ == "__main__":
    raise SystemExit(main())