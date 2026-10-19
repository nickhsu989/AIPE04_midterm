"""config.py — single source of truth for all settings.

Loads .env (via python-dotenv) and exposes a CFG dict.
Only DB_USER / DB_PASSWORD are left blank for the user to fill in .env.

`.env` is read from the project directory (not the shell's current
directory), so every script behaves the same no matter where it is
started from. Directory settings are stored as absolute paths for the
same reason.
"""
import os

from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, ".env"))


def _dir(key, default):
    """Absolute path for a directory setting (relative values resolve
    against the project directory, absolute values are kept as-is)."""
    return os.path.normpath(os.path.join(BASE_DIR, os.getenv(key, default)))


CFG = {
    "DB_HOST": os.getenv("DB_HOST", "localhost"),
    "DB_PORT": int(os.getenv("DB_PORT", "3306")),
    "DB_NAME": os.getenv("DB_NAME", "finance_app"),
    "DB_USER": os.getenv("DB_USER", ""),
    "DB_PASSWORD": os.getenv("DB_PASSWORD", ""),
    "FTE_BIND_HOST": os.getenv("FTE_BIND_HOST", "127.0.0.1"),
    "FTE_UPLOAD_DIR": _dir("FTE_UPLOAD_DIR", "data/uploads"),
    "FTE_PROCESSED_DIR": _dir("FTE_PROCESSED_DIR", "data/processed"),
    "FTE_REJECTED_DIR": _dir("FTE_REJECTED_DIR", "data/rejected"),
    "FTE_MAIN_URL": os.getenv("FTE_MAIN_URL", "http://127.0.0.1:5000"),
    "FTE_STREAMLIT_URL": os.getenv("FTE_STREAMLIT_URL", "http://127.0.0.1:8501"),
    # Bulk-load fast path: 1 = use LOAD DATA LOCAL INFILE when the server
    # allows it (needs local_infile=ON), 0 = portable row-wise upserts only.
    "FTE_FAST_LOAD": os.getenv("FTE_FAST_LOAD", "0"),
    "BASE_DIR": BASE_DIR,
}
