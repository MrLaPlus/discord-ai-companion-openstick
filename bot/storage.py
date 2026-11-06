from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path


class Store:
    """Small, bounded synchronous queries; never hold a transaction across an await."""

    def __init__(self, path: Path | str):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.privacy_folder = path.parent if str(path) != ":memory:" else None
        self.db = sqlite3.connect(path, timeout=10)
        if str(path) != ":memory:":
            path.chmod(0o600)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA foreign_keys=ON")
        version = self.db.execute("PRAGMA user_version").fetchone()[0]
        if version not in (0, 1, 2):
            raise ValueError("Database schema is newer than this application")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS channels (
                guild INTEGER NOT NULL, channel INTEGER NOT NULL,
                readable INTEGER NOT NULL DEFAULT 0, respond INTEGER NOT NULL DEFAULT 0,
                bot_room INTEGER NOT NULL DEFAULT 0, share_group TEXT NOT NULL DEFAULT '',
                interject REAL NOT NULL DEFAULT 0, cooldown INTEGER NOT NULL DEFAULT 300,
                hourly INTEGER NOT NULL DEFAULT 3, tone INTEGER NOT NULL DEFAULT 1,
                quiet_start INTEGER NOT NULL DEFAULT -1, quiet_end INTEGER NOT NULL DEFAULT -1,
                paused INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(guild, channel)
            );
            CREATE TABLE IF NOT EXISTS users (
                guild INTEGER NOT NULL, user INTEGER NOT NULL,
                optout INTEGER NOT NULL DEFAULT 0, tone INTEGER NOT NULL DEFAULT 1,
                PRIMARY KEY(guild, user)
            );
            CREATE TABLE IF NOT EXISTS workflow_tasks (
                id TEXT PRIMARY KEY, platform TEXT NOT NULL, guild INTEGER NOT NULL,
                channel INTEGER NOT NULL, owner INTEGER NOT NULL, kind TEXT NOT NULL,
                topic TEXT NOT NULL, stage TEXT NOT NULL, status TEXT NOT NULL,
                evidence TEXT NOT NULL DEFAULT '[]', result TEXT NOT NULL DEFAULT '',
                created REAL NOT NULL, updated REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS workflow_owner ON workflow_tasks(platform,guild,channel,owner,updated);
            CREATE TABLE IF NOT EXISTS telegram_chat_turns (
                id INTEGER PRIMARY KEY AUTOINCREMENT, user INTEGER NOT NULL,
                question TEXT NOT NULL, answer TEXT NOT NULL, created REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS telegram_chat_user_time ON telegram_chat_turns(user,created);
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY, guild INTEGER NOT NULL, channel INTEGER NOT NULL,
                author INTEGER NOT NULL, content TEXT NOT NULL, created REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS messages_scope ON messages(guild, channel, created DESC);
            CREATE TABLE IF NOT EXISTS memories (
                id INTEGER PRIMARY KEY AUTOINCREMENT, guild INTEGER NOT NULL,
                channel INTEGER NOT NULL, owner INTEGER NOT NULL, kind TEXT NOT NULL,
                content TEXT NOT NULL, created REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS memories_scope ON memories(guild, channel, owner);
            CREATE TABLE IF NOT EXISTS attempts (
                id INTEGER PRIMARY KEY AUTOINCREMENT, created REAL NOT NULL,
                reserved INTEGER NOT NULL, actual INTEGER, status TEXT NOT NULL DEFAULT 'reserved'
            );
            CREATE INDEX IF NOT EXISTS attempts_time ON attempts(created);
            CREATE TABLE IF NOT EXISTS handled (
                id INTEGER PRIMARY KEY, created REAL NOT NULL, status TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS interjections (
                guild INTEGER NOT NULL, channel INTEGER NOT NULL, created REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        """)
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS scheduled_jobs (
                id TEXT PRIMARY KEY, platform TEXT NOT NULL, guild INTEGER NOT NULL,
                channel INTEGER NOT NULL, owner INTEGER NOT NULL, kind TEXT NOT NULL,
                body TEXT NOT NULL, daily INTEGER NOT NULL, next_at REAL NOT NULL,
                status TEXT NOT NULL DEFAULT 'active', created REAL NOT NULL,
                last_result TEXT NOT NULL DEFAULT '', last_due REAL
            );
            CREATE INDEX IF NOT EXISTS scheduled_due ON scheduled_jobs(status,next_at);
        """)
        if "transcript_enabled" not in {r[1] for r in self.db.execute("PRAGMA table_info(users)")}:
            with self.db:
                self.db.execute("ALTER TABLE users ADD COLUMN transcript_enabled INTEGER NOT NULL DEFAULT 1")
        if "progress_enabled" not in {r[1] for r in self.db.execute("PRAGMA table_info(users)")}:
            with self.db:
                self.db.execute("ALTER TABLE users ADD COLUMN progress_enabled INTEGER NOT NULL DEFAULT 1")
        scheduled_columns = {r[1] for r in self.db.execute("PRAGMA table_info(scheduled_jobs)")}
        if "weekday" not in scheduled_columns:
            with self.db:
                self.db.execute("ALTER TABLE scheduled_jobs ADD COLUMN weekday INTEGER")
        if "cached" not in {r[1] for r in self.db.execute("PRAGMA table_info(messages)")}:
            with self.db:
                self.db.execute("ALTER TABLE messages ADD COLUMN cached REAL NOT NULL DEFAULT 0")
                self.db.execute("UPDATE messages SET cached=created")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS answers (
                id INTEGER PRIMARY KEY, guild INTEGER NOT NULL, channel INTEGER NOT NULL,
                requester INTEGER NOT NULL, created REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS answer_refs (
                answer INTEGER NOT NULL REFERENCES answers(id) ON DELETE CASCADE,
                label TEXT NOT NULL, message INTEGER NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
                PRIMARY KEY(answer,label)
            );
            PRAGMA user_version=2;
        """)

        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS feedback (
                answer INTEGER PRIMARY KEY REFERENCES answers(id) ON DELETE CASCADE,
                guild INTEGER NOT NULL, channel INTEGER NOT NULL, requester INTEGER NOT NULL,
                category TEXT NOT NULL CHECK(category IN ('wrong','long','humor')),
                private INTEGER NOT NULL, created REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS feedback_scope ON feedback(guild,channel,created);
        """)
        columns = {r[1] for r in self.db.execute("PRAGMA table_info(attempts)")}
        with self.db:
            for name, declaration in {
                "category": "TEXT NOT NULL DEFAULT 'chat'",
                "reserved_cost": "REAL NOT NULL DEFAULT 0",
                "actual_cost": "REAL",
            }.items():
                if name not in columns:
                    self.db.execute(f"ALTER TABLE attempts ADD COLUMN {name} {declaration}")
        from .privacy import apply_ledger, seed_legacy_optouts

        seed_legacy_optouts(self.db, self.privacy_folder)
        apply_ledger(self.db, self.privacy_folder)

    def close(self):
        self.db.close()

    def channel(self, guild: int, channel: int) -> dict:
        row = self.db.execute(
            "SELECT * FROM channels WHERE guild=? AND channel=?", (guild, channel)
        ).fetchone()
        return (
            dict(row)
            if row
            else {
                "guild": guild,
                "channel": channel,
                "readable": 0,
                "respond": 0,
                "bot_room": 0,
                "share_group": "",
                "interject": 0,
                "cooldown": 300,
                "hourly": 3,
                "tone": 1,
                "quiet_start": -1,
                "quiet_end": -1,
                "paused": 0,
            }
        )

    def set_channel(self, guild: int, channel: int, **changes):
        if "tone" in changes and changes["tone"] not in range(5):
            raise ValueError("Tone must be 0 through 4")
        allowed = set(self.channel(guild, channel)) - {"guild", "channel"}
        if not changes or not changes.keys() <= allowed:
            raise ValueError("Unknown channel setting")
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO channels(guild,channel) VALUES (?,?)", (guild, channel))
            if "respond" in changes:
                self.db.execute(
                    "INSERT OR REPLACE INTO metadata VALUES (?,?)",
                    (f"response_disabled:{guild}:{channel}", json.dumps(not bool(changes["respond"]))),
                )
            if "tone" in changes:
                self.db.execute(
                    "INSERT OR REPLACE INTO metadata VALUES (?,?)",
                    (f"channel_tone_set:{guild}:{channel}", "true"),
                )
            self.db.execute(
                "UPDATE channels SET "
                + ",".join(f"{key}=?" for key in changes)
                + " WHERE guild=? AND channel=?",
                (*changes.values(), guild, channel),
            )

    def channels(self, guild: int) -> list[dict]:
        return [dict(r) for r in self.db.execute("SELECT * FROM channels WHERE guild=?", (guild,))]

    def user(self, guild: int, user: int) -> dict:
        row = self.db.execute("SELECT * FROM users WHERE guild=? AND user=?", (guild, user)).fetchone()
        return dict(row) if row else {"guild": guild, "user": user, "optout": 0, "tone": 1, "progress_enabled": 1, "transcript_enabled": 1}

    def set_user(self, guild: int, user: int, **changes):
        if "tone" in changes and changes["tone"] not in range(5):
            raise ValueError("Tone must be 0 through 4")
        if not changes or not changes.keys() <= {"optout", "tone", "progress_enabled", "transcript_enabled"}:
            raise ValueError("Unknown user setting")
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO users(guild,user) VALUES (?,?)", (guild, user))
            self.db.execute(
                "UPDATE users SET " + ",".join(f"{key}=?" for key in changes) + " WHERE guild=? AND user=?",
                (*changes.values(), guild, user),
            )
            if "optout" in changes:
                from .privacy import record

                record(self.privacy_folder, guild, user, optout=bool(changes["optout"]))
            if changes.get("optout"):
                key = f'tokenme:model:telegram:{user}' if guild == -1 else f'tokenme:model:discord:{guild}:{user}'
                self.db.execute('DELETE FROM metadata WHERE key=?', (key,))
                self.db.execute("DELETE FROM messages WHERE guild=? AND author=?", (guild, user))
                self.db.execute("DELETE FROM workflow_tasks WHERE guild=? AND owner=?", (guild, user))
                if guild == -1:
                    self.db.execute("DELETE FROM telegram_chat_turns WHERE user=?", (user,))
                self.db.execute("DELETE FROM memories WHERE guild=? AND owner=?", (guild, user))
                self.db.execute("DELETE FROM answers WHERE guild=? AND requester=?", (guild, user))

    def telegram_turns(self, user, limit=6, now=None, since=None):
        if self.user(-1, user)['optout']:
            return []
        now = time.time() if now is None else now
        cutoff = max(now - 7 * 86400, since) if since is not None else now - 7 * 86400
        rows = self.db.execute("SELECT question,answer FROM telegram_chat_turns WHERE user=? AND created>=? ORDER BY id DESC LIMIT ?", (user, cutoff, min(max(1, limit), 100))).fetchall()
        return [tuple(row) for row in reversed(rows)]

    def add_telegram_turn(self, user, question, answer, now=None):
        if self.user(-1, user)['optout']:
            return
        now = time.time() if now is None else now
        with self.db:
            self.db.execute("INSERT INTO telegram_chat_turns(user,question,answer,created) VALUES (?,?,?,?)", (user, question[:1000], answer[:1500], now))
            self.db.execute("DELETE FROM telegram_chat_turns WHERE user=? AND id NOT IN (SELECT id FROM telegram_chat_turns WHERE user=? ORDER BY id DESC LIMIT 100)", (user, user))

    def clear_telegram_turns(self, user):
        from .privacy import clear_telegram_chat
        clear_telegram_chat(self.privacy_folder, user)
        with self.db:
            self.db.execute("DELETE FROM telegram_chat_turns WHERE user=?", (user,))

    def put_message(
        self, message_id: int, guild: int, channel: int, author: int, content: str, created: float
    ):
        from .privacy import cutoff

        if (
            not content.strip()
            or self.user(guild, author)["optout"]
            or created <= cutoff(self.privacy_folder, guild, author)
        ):
            return
        with self.db:
            self.db.execute(
                "DELETE FROM answer_refs WHERE message=? AND EXISTS "
                "(SELECT 1 FROM messages WHERE id=? AND content<>?)",
                (message_id, message_id, content[:4000]),
            )
            self.db.execute(
                "INSERT INTO messages(id,guild,channel,author,content,created,cached) VALUES (?,?,?,?,?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET content=excluded.content,created=excluded.created,"
                "cached=excluded.cached",
                (message_id, guild, channel, author, content[:4000], created, time.time()),
            )
        # Assistant-turn text is kept only in bounded RAM, never in this database.

    def page_messages(self, guild: int, allowed: list[int], ids: tuple[int, ...]) -> list[dict]:
        if not allowed or not ids:
            return []
        cm, mm = ",".join("?" for _ in allowed), ",".join("?" for _ in ids)
        return [
            dict(r)
            for r in self.db.execute(
                f"SELECT * FROM messages WHERE guild=? AND channel IN ({cm}) AND id IN ({mm}) ORDER BY created DESC",
                (guild, *allowed, *ids),
            )
        ]

    def delete_message(self, message_id: int):
        with self.db:
            self.db.execute("DELETE FROM messages WHERE id=?", (message_id,))
            self.db.execute("DELETE FROM answers WHERE id=?", (message_id,))

    def erase_user(self, guild: int, user: int):
        from .privacy import record

        record(self.privacy_folder, guild, user, cutoff=time.time())
        with self.db:
            self.db.execute("DELETE FROM messages WHERE guild=? AND author=?", (guild, user))
            self.db.execute("DELETE FROM memories WHERE guild=? AND owner=?", (guild, user))
            self.db.execute("DELETE FROM answers WHERE guild=? AND requester=?", (guild, user))

        from .privacy import apply_ledger

        apply_ledger(self.db, self.privacy_folder)
        with self.db:
            model_key = f'tokenme:model:telegram:{user}' if guild == -1 else f'tokenme:model:discord:{guild}:{user}'
            self.db.execute('DELETE FROM metadata WHERE key=?', (model_key,))
            for key in (f"length:{guild}:{user}", f"gentle:{guild}:{user}", f"suggestions:{guild}:{user}"):
                self.db.execute("DELETE FROM metadata WHERE key=?", (key,))

    def erase_channel(self, guild: int, channel: int):
        with self.db:
            self.db.execute("DELETE FROM messages WHERE guild=? AND channel=?", (guild, channel))
            self.db.execute("DELETE FROM memories WHERE guild=? AND channel=?", (guild, channel))
            self.db.execute("DELETE FROM answers WHERE guild=? AND channel=?", (guild, channel))

    def recent(
        self,
        guild: int,
        channels: list[int],
        limit: int = 500,
        since: float = 0,
        until: float = 1e20,
        cached_since: float = 0,
        oldest_first: bool = False,
    ) -> list[dict]:
        if not channels:
            return []
        marks = ",".join("?" for _ in channels)
        return [
            dict(r)
            for r in self.db.execute(
                f"SELECT * FROM messages WHERE guild=? AND channel IN ({marks}) AND created>=? AND created<? "
                "AND cached>=? "
                f"ORDER BY created {'ASC' if oldest_first else 'DESC'} LIMIT ?",
                (guild, *channels, since, until, cached_since, limit),
            )
        ]

    def search(
        self,
        guild: int,
        channels: list[int],
        terms: set[str],
        since: float,
        until: float = 1e20,
        cached_since: float = 0,
    ) -> list[dict]:
        if not channels or not terms:
            return []
        marks = ",".join("?" for _ in channels)
        # Search retained history with Thai-compatible substrings, beyond the recent-context sample.
        keywords = sorted(terms)[:12]
        matches = " OR ".join("instr(lower(content),?)>0" for _ in keywords)
        return [
            dict(r)
            for r in self.db.execute(
                f"SELECT * FROM messages WHERE guild=? AND channel IN ({marks}) AND created>=? AND created<? "
                "AND cached>=? "
                f"AND ({matches}) ORDER BY created DESC LIMIT 20",
                (guild, *channels, since, until, cached_since, *keywords),
            )
        ]

    def save_answer(self, answer: int, guild: int, channel: int, requester: int, sources: dict[str, str]):
        if self.user(guild, requester)["optout"]:
            return
        with self.db:
            self.db.execute(
                "INSERT OR REPLACE INTO answers VALUES (?,?,?,?,?)",
                (answer, guild, channel, requester, time.time()),
            )
            for label, url in sources.items():
                mid = int(url.rsplit("/", 1)[-1])
                self.db.execute(
                    "INSERT OR IGNORE INTO answer_refs SELECT ?,?,id FROM messages WHERE id=? AND guild=?",
                    (answer, label, mid, guild),
                )

    def add_feedback(self, guild, channel, user, answer, category, *, private=False):
        if category not in {"wrong", "long", "humor"} or self.user(guild, user)["optout"]:
            raise ValueError("Feedback disabled")
        row = self.db.execute(
            "SELECT 1 FROM answers WHERE id=? AND guild=? AND channel=? AND requester=?",
            (answer, guild, channel, user),
        ).fetchone()
        if not row:
            raise ValueError("Not your answer")
        with self.db:
            self.db.execute(
                "INSERT INTO feedback VALUES (?,?,?,?,?,?,?) ON CONFLICT(answer) DO UPDATE SET category=excluded.category,created=excluded.created",
                (answer, guild, channel, user, category, int(private), time.time()),
            )

    def answer_sources(
        self, guild: int, channel: int, requester: int, reply: int | None = None
    ) -> dict[str, str]:
        if reply is None:
            row = self.db.execute(
                "SELECT id FROM answers WHERE guild=? AND channel=? AND requester=? "
                "ORDER BY created DESC LIMIT 1",
                (guild, channel, requester),
            ).fetchone()
            if not row:
                return {}
            reply = row[0]
        return {
            r[0]: f"https://discord.com/channels/{guild}/{r[1]}/{r[2]}"
            for r in self.db.execute(
                "SELECT r.label,m.channel,m.id FROM answer_refs r JOIN answers a ON a.id=r.answer "
                "JOIN messages m ON m.id=r.message WHERE a.id=? AND a.guild=? AND a.channel=?",
                (reply, guild, channel),
            )
        }

    def remember(self, guild: int, channel: int, owner: int, content: str, kind: str = "person") -> int:
        if kind not in {"person", "room"} or not content.strip() or len(content) > 500:
            raise ValueError("Memory must contain 1–500 characters")
        if kind == "person" and self.user(guild, owner)["optout"]:
            raise ValueError("Memory disabled by privacy setting")
        count = self.db.execute(
            "SELECT count(*) FROM memories WHERE guild=? AND owner=? AND kind=?",
            (guild, owner, kind),
        ).fetchone()[0]
        if count >= 100:
            raise ValueError("Memory limit reached (100); delete an old memory first")
        with self.db:
            result = self.db.execute(
                "INSERT INTO memories(guild,channel,owner,kind,content,created) VALUES (?,?,?,?,?,?)",
                (guild, channel, owner, kind, content.strip(), time.time()),
            )
        return result.lastrowid

    def memories(self, guild: int, channels: list[int], owner: int, include_room: bool = True) -> list[dict]:
        if not channels:
            return []
        marks = ",".join("?" for _ in channels)
        predicate = "(owner=? AND kind='person')"
        if include_room:
            predicate = "(" + predicate + " OR kind='room')"
        return [
            dict(r)
            for r in self.db.execute(
                f"SELECT * FROM memories WHERE guild=? AND channel IN ({marks}) AND {predicate} "
                "ORDER BY created DESC LIMIT 100",
                (guild, *channels, owner),
            )
        ]

    def own_memories(self, guild: int, owner: int) -> list[dict]:
        return [
            dict(r)
            for r in self.db.execute(
                "SELECT * FROM memories WHERE guild=? AND owner=? AND kind='person' ORDER BY id DESC LIMIT 100",
                (guild, owner),
            )
        ]

    def edit_memory(self, guild: int, owner: int, memory_id: int, text: str) -> bool:
        if not text.strip() or len(text) > 500:
            raise ValueError("Memory must contain 1–500 characters")
        with self.db:
            result = self.db.execute(
                "UPDATE memories SET content=?,created=? WHERE id=? AND guild=? AND owner=? AND kind='person'",
                (text.strip(), time.time(), memory_id, guild, owner),
            )
        return bool(result.rowcount)

    def forget(self, guild: int, owner: int, memory_id: int) -> bool:
        from .privacy import forget_item

        if any(r["id"] == memory_id for r in self.own_memories(guild, owner)):
            forget_item(self.privacy_folder, guild, owner, memory_id)
        with self.db:
            result = self.db.execute(
                "DELETE FROM memories WHERE id=? AND guild=? AND owner=? AND kind='person'",
                (memory_id, guild, owner),
            )
        return bool(result.rowcount)

    def claim(self, message_id: int) -> bool:
        with self.db:
            result = self.db.execute(
                "INSERT OR IGNORE INTO handled VALUES (?,?,'claimed')",
                (message_id, time.time()),
            )
        return bool(result.rowcount)

    def finish(self, message_id: int, status: str):
        with self.db:
            self.db.execute("UPDATE handled SET status=? WHERE id=?", (status, message_id))

    def mark_interjection(self, guild: int, channel: int, now: float):
        with self.db:
            self.db.execute("INSERT INTO interjections VALUES (?,?,?)", (guild, channel, now))

    def interjection_times(self, guild: int, channel: int, now: float) -> list[float]:
        return [
            r[0]
            for r in self.db.execute(
                "SELECT created FROM interjections WHERE guild=? AND channel=? AND created>?",
                (guild, channel, now - 86400),
            )
        ]

    def set_meta(self, key: str, value):
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO metadata VALUES (?,?)", (key, json.dumps(value)))

    def meta(self, key: str, default=None):
        row = self.db.execute("SELECT value FROM metadata WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def maintain(self, history_days: int, memory_days: int, max_messages: int):
        now = time.time()
        with self.db:
            self.db.execute("DELETE FROM workflow_tasks WHERE updated<?", (now - 7 * 86400,))
            self.db.execute("DELETE FROM telegram_chat_turns WHERE created<?", (now - 7 * 86400,))
            self.db.execute("DELETE FROM messages WHERE cached<?", (now - history_days * 86400,))
            self.db.execute("DELETE FROM answers WHERE created<?", (now - history_days * 86400,))
            self.db.execute("DELETE FROM memories WHERE created<?", (now - memory_days * 86400,))
            self.db.execute("DELETE FROM attempts WHERE created<?", (now - 172800,))
            self.db.execute("DELETE FROM handled WHERE created<?", (now - 7 * 86400,))
            self.db.execute("DELETE FROM interjections WHERE created<?", (now - 86400,))
            for key, value in self.db.execute(
                "SELECT key,value FROM metadata WHERE key LIKE 'jokes:%'"
            ).fetchall():
                active = [j for j in json.loads(value) if j.get("expires", 0) > now]
                self.db.execute("UPDATE metadata SET value=? WHERE key=?", (json.dumps(active), key))
            for row in self.db.execute("SELECT DISTINCT guild FROM messages").fetchall():
                self.db.execute(
                    "DELETE FROM messages WHERE guild=? AND id NOT IN "
                    "(SELECT id FROM messages WHERE guild=? ORDER BY created DESC LIMIT ?)",
                    (row[0], row[0], max_messages),
                )
