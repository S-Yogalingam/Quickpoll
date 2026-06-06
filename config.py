"""QuickPoll configuration.

All values can be overridden via environment variables (loaded from .env if present).
"""
from __future__ import annotations

import os

from dotenv import load_dotenv

load_dotenv()


# --- Server ---------------------------------------------------------------
PORT = int(os.getenv("PORT", "8090"))
HOST = os.getenv("HOST", "0.0.0.0")
SECRET_KEY = os.getenv("QUICKPOLL_SECRET_KEY", "dev-secret-change-me")

# --- MySQL ----------------------------------------------------------------
DB_CONFIG = {
    "host": os.getenv("MYSQL_HOST", "127.0.0.1"),
    "port": int(os.getenv("MYSQL_PORT", "3306")),
    "user": os.getenv("MYSQL_USER", "quickpoll"),
    "password": os.getenv("MYSQL_PASSWORD", "quickpoll_pw"),
    "database": os.getenv("MYSQL_DB", "quickpoll"),
    # MySQL stores everything in UTC for us; we coerce in Python.
    "autocommit": False,
    "use_pure": True,
}

# --- Cookies --------------------------------------------------------------
VOTER_COOKIE = "qp_voter"
COOKIE_MAX_AGE = 60 * 60 * 24 * 365  # 1 year
