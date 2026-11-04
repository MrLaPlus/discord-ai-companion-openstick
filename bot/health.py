from __future__ import annotations

import os
import sqlite3
import time
from contextlib import closing
from pathlib import Path


def healthy(path: Path) -> bool:
    try:
        with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=2)) as db:
            row = db.execute("SELECT value FROM metadata WHERE key='heartbeat'").fetchone()
            return bool(row and 0 <= time.time() - float(row[0]) < 45)
    except (sqlite3.Error, ValueError, OSError):
        return False


if __name__ == "__main__":
    raise SystemExit(0 if healthy(Path(os.environ.get("DATABASE_PATH", "data/bot.sqlite3"))) else 1)
