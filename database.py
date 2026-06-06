"""MySQL connection helper.

Per-request connections via a context manager (the skill's recommended pattern —
avoids stale module-level connections across Flask's debug reloader).
"""
from __future__ import annotations

from contextlib import contextmanager

import mysql.connector

import config


def get_connection():
    return mysql.connector.connect(**config.DB_CONFIG)


@contextmanager
def get_cursor(dictionary: bool = True, commit: bool = False):
    """Yield (conn, cursor). Commits on success when commit=True, always closes.

    Usage:
        with get_cursor(commit=True) as (conn, cur):
            cur.execute("INSERT ...", (...))
    """
    conn = get_connection()
    cur = conn.cursor(dictionary=dictionary)
    try:
        yield conn, cur
        if commit:
            conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()
