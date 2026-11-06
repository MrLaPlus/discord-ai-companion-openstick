import sqlite3
import tempfile
import time
import unittest
from pathlib import Path

from bot.backup import backup, restore
from bot.health import healthy
from bot.storage import Store


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "db.sqlite3"
        self.store = Store(self.path)

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def message(self, mid=1, guild=10, channel=20, author=30, content="ชอบเกมแมว", age=0):
        self.store.put_message(mid, guild, channel, author, content, time.time() - age)

    def test_progress_setting_migrates_legacy_users_and_persists(self):
        path = Path(self.temp.name) / 'legacy.sqlite3'
        with sqlite3.connect(path) as db:
            db.execute('CREATE TABLE users (guild INTEGER NOT NULL, user INTEGER NOT NULL, optout INTEGER NOT NULL DEFAULT 0, tone INTEGER NOT NULL DEFAULT 1, PRIMARY KEY(guild,user))')
            db.execute('INSERT INTO users VALUES (10, 30, 0, 2)')
        migrated = Store(path)
        try:
            self.assertEqual(migrated.user(10, 30)['tone'], 2)
            self.assertEqual(migrated.user(10, 30)['progress_enabled'], 1)
            self.assertEqual(migrated.user(10, 30)['transcript_enabled'], 1)
            migrated.set_user(10, 30, progress_enabled=0, transcript_enabled=0)
        finally:
            migrated.close()
        migrated = Store(path)
        try:
            self.assertEqual(migrated.user(10, 30)['progress_enabled'], 0)
            self.assertEqual(migrated.user(10, 30)['transcript_enabled'], 0)
            self.assertEqual(migrated.user(10, 31)['progress_enabled'], 1)
            self.assertEqual(migrated.user(10, 31)['transcript_enabled'], 1)
        finally:
            migrated.close()

    def test_memory_and_settings_survive_restart(self):
        mid = self.store.remember(10, 20, 30, "เรียกเราว่าแมว")
        self.store.set_channel(10, 20, bot_room=1, readable=1)
        self.store.close()
        self.store = Store(self.path)
        self.assertEqual(self.store.own_memories(10, 30)[0]["id"], mid)
        self.assertEqual(self.store.channel(10, 20)["bot_room"], 1)

    def test_guild_channel_and_person_isolation(self):
        self.message()
        self.message(2, guild=11)
        self.message(3, channel=21)
        self.store.remember(10, 20, 30, "ถูกคน")
        self.store.remember(10, 20, 31, "คนอื่น")
        self.store.remember(11, 20, 30, "อีกเซิร์ฟเวอร์")
        self.assertEqual([r["id"] for r in self.store.recent(10, [20])], [1])
        self.assertEqual([r["content"] for r in self.store.memories(10, [20], 30)], ["ถูกคน"])
        self.assertEqual(self.store.memories(10, [21], 30), [])

    def test_owner_cannot_edit_or_delete_another_memory(self):
        mid = self.store.remember(10, 20, 30, "ต้นฉบับ")
        self.assertFalse(self.store.edit_memory(10, 31, mid, "ปลอม"))
        self.assertFalse(self.store.forget(11, 30, mid))
        self.assertTrue(self.store.edit_memory(10, 30, mid, "แก้แล้ว"))
        self.assertTrue(self.store.forget(10, 30, mid))

    def test_optout_erases_and_blocks_reimport(self):
        self.message()
        self.store.remember(10, 20, 30, "ไม่เก็บแล้ว")
        self.store.set_user(10, 30, optout=1)
        self.store.erase_user(10, 30)
        self.message(2)
        self.assertEqual(self.store.recent(10, [20]), [])
        self.assertEqual(self.store.own_memories(10, 30), [])
        with self.assertRaises(ValueError):
            self.store.remember(10, 20, 30, "ห้ามเก็บ")

    def test_delete_message_and_channel(self):
        self.message()
        self.message(2, channel=21)
        self.store.remember(10, 20, 30, "จำห้องนี้")
        self.store.delete_message(1)
        self.assertEqual(self.store.recent(10, [20]), [])
        self.store.erase_channel(10, 20)
        self.assertEqual(self.store.own_memories(10, 30), [])
        self.assertEqual(len(self.store.recent(10, [21])), 1)

    def test_claim_is_durable_at_most_once(self):
        self.assertTrue(self.store.claim(123))
        self.store.close()
        self.store = Store(self.path)
        self.assertFalse(self.store.claim(123))

    def test_retention_and_message_cap(self):
        self.message(1, age=40 * 86400)
        self.message(2, age=50)
        self.message(3)
        self.store.maintain(30, 180, 1)
        self.assertEqual([r["id"] for r in self.store.recent(10, [20])], [3])

    def test_settings_identifiers_are_allowlisted(self):
        with self.assertRaises(ValueError):
            self.store.set_channel(10, 20, **{"readable=1; DELETE FROM messages;--": 1})

    def test_backup_restores_wal_data_and_optout(self):
        self.message()
        self.store.set_user(10, 30, optout=1)
        self.store.set_meta("heartbeat", time.time())
        self.assertTrue(healthy(self.path))
        saved = Path(self.temp.name) / "backup.sqlite3"
        backup(self.path, saved)
        self.assertFalse(healthy(saved))
        restored = Path(self.temp.name) / "restored.sqlite3"
        restore(saved, restored)
        other = Store(restored)
        try:
            self.assertEqual(len(other.recent(10, [20])), 0)
            self.assertEqual(other.user(10, 30)["optout"], 1)
        finally:
            other.close()
        with self.assertRaises(ValueError):
            restore(saved, self.path)

    def test_health_requires_fresh_connected_heartbeat(self):
        self.assertFalse(healthy(self.path))
        self.store.set_meta("heartbeat", time.time() - 60)
        self.assertFalse(healthy(self.path))
        self.store.set_meta("heartbeat", time.time())
        self.assertTrue(healthy(self.path))

    def test_optout_persists_atomic_deletion_without_separate_erase_call(self):
        self.message()
        self.store.remember(10, 20, 30, "ส่วนตัว")
        self.store.set_user(10, 30, optout=1)
        self.store.close()
        self.store = Store(self.path)
        self.assertEqual(self.store.recent(10, [20]), [])
        self.assertEqual(self.store.own_memories(10, 30), [])

    def test_local_database_is_owner_read_write_only(self):
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)

    def test_version_one_database_migrates_without_losing_messages(self):
        old = Path(self.temp.name) / "old.sqlite3"
        with sqlite3.connect(old) as db:
            db.executescript(
                "CREATE TABLE messages(id INTEGER PRIMARY KEY,guild INTEGER,channel INTEGER,"
                "author INTEGER,content TEXT,created REAL); PRAGMA user_version=1;"
            )
            db.execute("INSERT INTO messages VALUES (1,10,20,30,'เก่า',?)", (time.time(),))
        migrated = Store(old)
        try:
            self.assertEqual(migrated.db.execute("PRAGMA user_version").fetchone()[0], 2)
            self.assertEqual(migrated.recent(10, [20])[0]["content"], "เก่า")
        finally:
            migrated.close()

    def test_answer_provenance_survives_restart_and_cascades_on_source_change(self):
        self.message()
        refs = {"S1": "https://discord.com/channels/10/20/1"}
        self.store.save_answer(100, 10, 20, 30, refs)
        self.store.close()
        self.store = Store(self.path)
        self.assertEqual(self.store.answer_sources(10, 20, 30), refs)
        self.assertEqual(self.store.answer_sources(11, 20, 30), {})
        self.assertEqual(self.store.answer_sources(10, 21, 30, 100), {})
        self.message(content="แก้ต้นทางแล้ว")
        self.assertEqual(self.store.answer_sources(10, 20, 30, 100), {})

    def test_same_source_refresh_preserves_provenance_but_optout_removes_it(self):
        self.message()
        refs = {"S1": "https://discord.com/channels/10/20/1"}
        self.store.save_answer(100, 10, 20, 30, refs)
        self.message()
        self.assertEqual(self.store.answer_sources(10, 20, 30), refs)
        self.store.set_user(10, 30, optout=1)
        self.assertEqual(self.store.answer_sources(10, 20, 30), {})
