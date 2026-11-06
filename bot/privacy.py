"""Minimal deletion journal, outside rotating database backups; never stores chat text."""

from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager


@contextmanager
def ledger(folder):
    path = folder / "privacy-ledger.sqlite3" if folder is not None else None
    db = sqlite3.connect(path if path else ":memory:", timeout=10)
    if path:
        path.chmod(0o600)
    try:
        db.executescript("""
            CREATE TABLE IF NOT EXISTS forgotten_workflow_tasks (id TEXT PRIMARY KEY);
            CREATE TABLE IF NOT EXISTS telegram_chat_erased (user INTEGER PRIMARY KEY, cutoff REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS erased (guild INTEGER, user INTEGER, cutoff REAL DEFAULT 0,
                optout INTEGER DEFAULT 0, PRIMARY KEY(guild,user));
            CREATE TABLE IF NOT EXISTS cancelled_schedules (id TEXT PRIMARY KEY);
            CREATE TABLE IF NOT EXISTS schedule_claims (id TEXT, due REAL, next_at REAL, PRIMARY KEY(id,due));
            CREATE TABLE IF NOT EXISTS removed_jokes (guild INTEGER, channel INTEGER, joke TEXT, PRIMARY KEY(guild,channel,joke));
            CREATE TABLE IF NOT EXISTS forgotten (guild INTEGER, user INTEGER, memory INTEGER,
                PRIMARY KEY(guild,user,memory));
        """)
        if "cutoff" not in {r[1] for r in db.execute("PRAGMA table_info(forgotten)")}:
            with db:
                db.execute("ALTER TABLE forgotten ADD COLUMN cutoff REAL NOT NULL DEFAULT 0")
        yield db
    finally:
        db.close()


def record(folder, guild, user, *, cutoff=None, optout=None):
    with ledger(folder) as db, db:
        db.execute("INSERT OR IGNORE INTO erased(guild,user) VALUES (?,?)", (guild, user))
        if cutoff is not None:
            db.execute(
                "UPDATE erased SET cutoff=max(cutoff,?) WHERE guild=? AND user=?", (cutoff, guild, user)
            )
        if optout is not None:
            db.execute("UPDATE erased SET optout=? WHERE guild=? AND user=?", (int(optout), guild, user))
            if optout:
                db.execute(
                    "UPDATE erased SET cutoff=max(cutoff,?) WHERE guild=? AND user=?",
                    (time.time(), guild, user),
                )


def cutoff(folder, guild, user):
    with ledger(folder) as db:
        row = db.execute("SELECT cutoff FROM erased WHERE guild=? AND user=?", (guild, user)).fetchone()
    return row[0] if row else 0


def forget_item(folder, guild, user, memory):
    with ledger(folder) as db, db:
        db.execute(
            "INSERT INTO forgotten VALUES (?,?,?,?) ON CONFLICT(guild,user,memory) DO UPDATE SET cutoff=max(cutoff,excluded.cutoff)",
            (guild, user, memory, time.time()),
        )


def claim_schedule(folder, job_id, due, next_at):
    with ledger(folder) as db, db:
        result = db.execute("INSERT OR IGNORE INTO schedule_claims VALUES (?,?,?)", (job_id, due, next_at))
        return bool(result.rowcount)


def clear_telegram_chat(folder, user, when=None):
    with ledger(folder) as db, db:
        db.execute("INSERT INTO telegram_chat_erased VALUES (?,?) ON CONFLICT(user) DO UPDATE SET cutoff=max(cutoff,excluded.cutoff)", (user, time.time() if when is None else when))


def cancel_schedule(folder, job_id):
    with ledger(folder) as db, db:
        db.execute("INSERT OR IGNORE INTO cancelled_schedules VALUES (?)", (job_id,))


def forget_workflow_task(folder, task_id):
    with ledger(folder) as db, db:
        db.execute("INSERT OR IGNORE INTO forgotten_workflow_tasks VALUES (?)", (task_id,))


def forget_joke(folder, guild, channel, joke):
    with ledger(folder) as db, db:
        db.execute("INSERT OR IGNORE INTO removed_jokes VALUES (?,?,?)", (guild, channel, joke))


def apply_ledger(db, folder):
    with ledger(folder) as journal:
        rows = journal.execute("SELECT guild,user,cutoff,optout FROM erased").fetchall()
        telegram_erased = journal.execute("SELECT user,cutoff FROM telegram_chat_erased").fetchall()
        removed = set(journal.execute("SELECT guild,channel,joke FROM removed_jokes").fetchall())
        items = journal.execute("SELECT guild,user,memory,cutoff FROM forgotten").fetchall()
        cancelled = journal.execute("SELECT id FROM cancelled_schedules").fetchall()
        forgotten_tasks = journal.execute("SELECT id FROM forgotten_workflow_tasks").fetchall()
        claims = journal.execute("SELECT id,due,next_at FROM schedule_claims ORDER BY due").fetchall()
    with db:
        has_schedule = bool(db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='scheduled_jobs'").fetchone())
        if has_schedule:
            db.executemany("DELETE FROM scheduled_jobs WHERE id=?", cancelled)
            for job_id, due, next_at in claims:
                db.execute("UPDATE scheduled_jobs SET next_at=?,status=CASE WHEN daily=1 THEN 'active' ELSE 'finished' END,last_due=?,last_result='uncertain' WHERE id=? AND next_at<=? AND status IN ('active','sending')", (next_at, due, job_id, due))
        has_workflows = bool(db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='workflow_tasks'").fetchone())
        if has_workflows:
            db.executemany("DELETE FROM workflow_tasks WHERE id=?", forgotten_tasks)
        has_telegram_chat = bool(db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='telegram_chat_turns'").fetchone())
        if has_telegram_chat:
            for user, until in telegram_erased:
                db.execute("DELETE FROM telegram_chat_turns WHERE user=? AND created<=?", (user, until))
        for guild, user, until, optout in rows:
            model_key = f'tokenme:model:telegram:{user}' if guild == -1 else f'tokenme:model:discord:{guild}:{user}'
            entry = db.execute('SELECT value FROM metadata WHERE key=?', (model_key,)).fetchone()
            if entry:
                try:
                    item = json.loads(entry[0])
                    created = float(item.get('created', 0)) if isinstance(item, dict) else 0
                except (ValueError, TypeError):
                    created = 0
                if optout or created <= until:
                    db.execute('DELETE FROM metadata WHERE key=?', (model_key,))
            if has_workflows:
                db.execute("DELETE FROM workflow_tasks WHERE guild=? AND owner=? AND (created<=? OR ?)", (guild, user, until, optout))
            if has_schedule:
                db.execute("DELETE FROM scheduled_jobs WHERE guild=? AND owner=? AND (created<=? OR ?)", (guild, user, until, optout))
            if has_telegram_chat and guild == -1:
                db.execute("DELETE FROM telegram_chat_turns WHERE user=? AND (created<=? OR ?)", (user, until, optout))
            db.execute("INSERT OR IGNORE INTO users(guild,user) VALUES (?,?)", (guild, user))
            db.execute("UPDATE users SET optout=? WHERE guild=? AND user=?", (optout, guild, user))
            for table, author in (("messages", "author"), ("memories", "owner"), ("answers", "requester")):
                db.execute(
                    f"DELETE FROM {table} WHERE guild=? AND {author}=? AND (created<=? OR ?)",
                    (guild, user, until, optout),
                )
        for key, value in db.execute("SELECT key,value FROM metadata WHERE key LIKE 'jokes:%'").fetchall():
            _, guild, channel = key.split(":")
            jokes = [
                j
                for j in json.loads(value)
                if (int(guild), int(channel), j["id"]) not in removed
                and not any(
                    g == int(guild) and u in {j["author"], j["subject"]} and (opt or j["created"] <= until)
                    for g, u, until, opt in rows
                )
            ]
            db.execute("UPDATE metadata SET value=? WHERE key=?", (json.dumps(jokes), key))
        for guild, user, memory, until in items:
            db.execute(
                "DELETE FROM memories WHERE id=? AND guild=? AND owner=? AND created<=?",
                (memory, guild, user, until),
            )


def copy_ledger(source_folder, destination_folder):
    """Carry the retained erasure journal when recovering into a new directory."""
    if source_folder.resolve() == destination_folder.resolve():
        return
    with ledger(source_folder) as source:
        rows = source.execute("SELECT guild,user,cutoff,optout FROM erased").fetchall()
        items = source.execute("SELECT guild,user,memory,cutoff FROM forgotten").fetchall()
        jokes = source.execute("SELECT guild,channel,joke FROM removed_jokes").fetchall()
        cancelled = source.execute("SELECT id FROM cancelled_schedules").fetchall()
        forgotten_tasks = source.execute("SELECT id FROM forgotten_workflow_tasks").fetchall()
        claims = source.execute("SELECT id,due,next_at FROM schedule_claims").fetchall()
        telegram_erased = source.execute("SELECT user,cutoff FROM telegram_chat_erased").fetchall()
    with ledger(destination_folder) as target, target:
        target.executemany("INSERT INTO telegram_chat_erased VALUES (?,?) ON CONFLICT(user) DO UPDATE SET cutoff=max(cutoff,excluded.cutoff)", telegram_erased)
        target.executemany("INSERT OR IGNORE INTO forgotten_workflow_tasks VALUES (?)", forgotten_tasks)
        target.executemany("INSERT OR IGNORE INTO cancelled_schedules VALUES (?)", cancelled)
        target.executemany("INSERT OR IGNORE INTO schedule_claims VALUES (?,?,?)", claims)
        for row in rows:
            target.execute(
                "INSERT INTO erased VALUES (?,?,?,?) ON CONFLICT(guild,user) DO UPDATE SET cutoff=max(cutoff,excluded.cutoff),optout=max(optout,excluded.optout)",
                row,
            )
        target.executemany(
            "INSERT INTO forgotten VALUES (?,?,?,?) ON CONFLICT(guild,user,memory) DO UPDATE SET cutoff=max(cutoff,excluded.cutoff)",
            items,
        )
        target.executemany("INSERT OR IGNORE INTO removed_jokes VALUES (?,?,?)", jokes)


def seed_legacy_optouts(db, folder):
    """Import pre-journal opt-outs without overriding newer retained consent changes."""
    users = db.execute("SELECT guild,user FROM users WHERE optout=1").fetchall()
    with ledger(folder) as journal, journal:
        for guild, user in users:
            journal.execute("INSERT OR IGNORE INTO erased VALUES (?,?,?,1)", (guild, user, time.time()))
