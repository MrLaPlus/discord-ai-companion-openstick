from __future__ import annotations

import argparse
import os
import sqlite3
import time
from contextlib import closing
from pathlib import Path

from .health import healthy


def backup(source: Path, destination: Path, *, timeout: float = 30):
    if not source.is_file() or destination.exists():
        raise ValueError("Source must exist and destination must not exist")
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Reserve the filename atomically; two backup jobs must never overwrite each other's snapshot.
    descriptor = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(descriptor)
    deadline = time.monotonic() + timeout

    def progress(status, remaining, total):
        if time.monotonic() > deadline:
            raise TimeoutError("Backup exceeded its time budget")

    # SQLite backup API provides a coherent snapshot, including committed WAL data.
    with closing(sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True)) as src:
        with closing(sqlite3.connect(destination)) as dst, dst:
            src.backup(dst, pages=256, progress=progress)
            if dst.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("Backup integrity check failed")
            if dst.execute("PRAGMA user_version").fetchone()[0] not in (1, 2):
                raise ValueError("Unsupported backup schema")
            dst.execute("UPDATE metadata SET value='0' WHERE key='heartbeat'")
    os.chmod(destination, 0o600)


def restore(source: Path, destination: Path, *, privacy_folder: Path | None = None):
    if any(
        p.exists() for p in (destination, Path(str(destination) + "-wal"), Path(str(destination) + "-shm"))
    ):
        raise ValueError(
            "Restore only to a NEW database path, with the bot stopped; preserve the old database"
        )
    if healthy(destination):
        raise ValueError("Stop the bot before restoring")
    backup(source, destination)
    from .privacy import apply_ledger, copy_ledger

    privacy_folder = privacy_folder or (
        source.parent.parent if source.parent.name == "backups" else source.parent
    )
    copy_ledger(privacy_folder, destination.parent)
    with closing(sqlite3.connect(destination)) as db:
        db.execute("PRAGMA foreign_keys=ON")
        apply_ledger(db, destination.parent)


def recovery_drill(source: Path, folder: Path) -> dict:
    """Exercise the restore path on a disposable copy, never replace the live database."""
    import tempfile

    with tempfile.TemporaryDirectory(prefix="recovery-", dir=folder) as temporary:
        destination = Path(temporary) / "restored.sqlite3"
        restore(source, destination)
        # Restore into another folder must explicitly use the retained live deletion journal.
        from .privacy import apply_ledger

        with closing(sqlite3.connect(destination)) as db:
            db.execute("PRAGMA foreign_keys=ON")
            apply_ledger(db, folder)
            if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("Recovery integrity failed")
            tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if not {"users", "messages", "memories", "metadata", "answers"} <= tables:
                raise ValueError("Recovery schema incomplete")
            db.execute("SELECT count(*) FROM messages").fetchone()
            version = db.execute("PRAGMA user_version").fetchone()[0]
    return {"ok": True, "schema": version, "created": time.time()}


def main():
    from dotenv import load_dotenv

    load_dotenv()
    parser = argparse.ArgumentParser(description="Safe SQLite backup / restore to a new path")
    parser.add_argument("action", choices=["create", "restore"])
    parser.add_argument("file", type=Path, help="Backup file")
    parser.add_argument(
        "--database", type=Path, default=Path(os.environ.get("DATABASE_PATH", "data/bot.sqlite3"))
    )
    parser.add_argument(
        "--privacy-folder",
        type=Path,
        help="Retained privacy-ledger.sqlite3 directory; must accompany restored backups",
    )
    args = parser.parse_args()
    try:
        if args.action == "create":
            backup(args.database, args.file)
        else:
            restore(args.file, args.database, privacy_folder=args.privacy_folder or args.database.parent)
        print("Backup integrity verified." if args.action == "create" else "Restored to new database path.")
    except (ValueError, sqlite3.Error) as error:
        print(str(error))
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
