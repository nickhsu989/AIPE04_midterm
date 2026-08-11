"""config.py — single source of truth for all settings.

Loads .env (via python-dotenv) and exposes a CFG dict.
Only DB_USER / DB_PASSWORD are left blank for the user to fill in .env.
"""
import os

from dotenv import load_dotenv

load_dotenv()

CFG = {
    "DB_HOST": os.getenv("DB_HOST", "localhost"),
    "DB_PORT": int(os.getenv("DB_PORT", "3306")),
    "DB_NAME": os.getenv("DB_NAME", "finance_app"),
    "DB_USER": os.getenv("DB_USER", ""),
    "DB_PASSWORD": os.getenv("DB_PASSWORD", ""),
    "FTE_BIND_HOST": os.getenv("FTE_BIND_HOST", "127.0.0.1"),
    "FTE_STAGING_DIR": os.getenv("FTE_STAGING_DIR", "data/staging"),
    "FTE_UPLOAD_DIR": os.getenv("FTE_UPLOAD_DIR", "data/uploads"),
    "FTE_PROCESSED_DIR": os.getenv("FTE_PROCESSED_DIR", "data/processed"),
    "FTE_REJECTED_DIR": os.getenv("FTE_REJECTED_DIR", "data/rejected"),
    "FTE_MAIN_URL": os.getenv("FTE_MAIN_URL", "http://127.0.0.1:5000"),
    "FTE_STREAMLIT_URL": os.getenv("FTE_STREAMLIT_URL", "http://127.0.0.1:8501"),
}
