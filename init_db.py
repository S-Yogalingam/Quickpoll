"""Create the QuickPoll schema in MySQL.

Run once before starting the app (run.sh does this automatically):
    ./venv/bin/python init_db.py
"""
from __future__ import annotations

import os

from database import get_connection


def init_db() -> None:
    here = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(here, "schema.sql"), "r", encoding="utf-8") as fh:
        ddl = fh.read()

    # Strip SQL line comments first so they don't get glued onto the next
    # statement when we split on ';' (which would corrupt the DDL ordering).
    lines = [ln for ln in ddl.splitlines() if not ln.strip().startswith("--")]
    sql = "\n".join(lines)

    conn = get_connection()
    cur = conn.cursor()
    try:
        # mysql-connector executes one statement at a time; split on ';'.
        for stmt in [s.strip() for s in sql.split(";")]:
            if stmt:
                cur.execute(stmt)
        conn.commit()
        print("==> QuickPoll schema ready (polls, options, votes).")
    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    init_db()
