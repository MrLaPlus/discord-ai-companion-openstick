import sqlite3
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import discord

from bot.backup import restore
from bot.config import Config
from bot.operations import Operations, automatic_backup
from bot.quota import Quota
from bot.storage import Store


class OperationsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "db.sqlite3"
        self.store = Store(self.path)
        self.config = Config("fake", "fake", database=self.path)
        self.owner = SimpleNamespace(send=AsyncMock())
        self.guild = SimpleNamespace(id=10, name="Test server")
        self.bot = SimpleNamespace(
            store=self.store,
            config=self.config,
            quota=Quota(self.store, self.config),
            guilds=[self.guild],
            in_scope=lambda gid: True,
            workers=[],
            provider=SimpleNamespace(last_status="not called"),
            user=SimpleNamespace(id=99),
            fetch_guild=AsyncMock(return_value=SimpleNamespace(owner_id=30)),
            fetch_user=AsyncMock(return_value=self.owner),
        )
        self.ops = Operations(self.bot)
        self.now = 1800000000.0

    async def asyncTearDown(self):
        self.store.close()
        self.temp.cleanup()

    async def test_scheduler_creates_integrity_checked_restorable_backup_and_not_every_tick(self):
        self.store.remember(10, 20, 30, "remember me")
        await self.ops.tick(self.now)
        first = self.store.meta("last_auto_backup")
        saved = self.path.parent / "backups" / first["file"]
        self.assertEqual(saved.stat().st_mode & 0o777, 0o600)
        await self.ops.tick(self.now + 60)
        self.assertEqual(first, self.store.meta("last_auto_backup"))
        restored = self.path.parent / "restored.sqlite3"
        restore(saved, restored)
        with sqlite3.connect(restored) as db:
            self.assertEqual(db.execute("SELECT content FROM memories").fetchone()[0], "remember me")
            self.assertEqual(db.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        self.owner.send.assert_not_awaited()

    async def test_prune_only_auto_files_after_a_success_and_keep_manual_backups(self):
        old = automatic_backup(self.path, 7, self.now - 8 * 86400)
        manual = old.parent / "manual.sqlite3"
        manual.write_bytes(b"manual untouched")
        recent = automatic_backup(self.path, 7, self.now)
        self.assertFalse(old.exists())
        self.assertTrue(recent.exists())
        self.assertEqual(manual.read_bytes(), b"manual untouched")

    async def test_backup_failure_preserves_previous_and_alerts_owner_with_retry_throttle(self):
        await self.ops.tick(self.now)
        previous = self.store.meta("last_auto_backup")
        with patch(
            "bot.operations.automatic_backup", side_effect=OSError("secret must not be sent")
        ) as create:
            await self.ops.tick(self.now + 86400)
            await self.ops.tick(self.now + 86430)
            self.assertEqual(create.call_count, 1)
        self.assertEqual(previous, self.store.meta("last_auto_backup"))
        self.assertTrue(self.store.meta("auto_backup_error"))
        self.owner.send.assert_awaited_once()
        self.assertNotIn("secret", str(self.owner.send.call_args))
        await self.ops.tick(self.now + 87001)
        self.assertFalse(self.store.meta("auto_backup_error"))

    async def test_provider_fault_quota_threshold_and_disabled_alerts(self):
        self.bot.config = replace(self.config, backup_hours=0)
        self.bot.provider.last_status = "401"
        await self.ops.tick(self.now)
        self.owner.send.assert_awaited_once()
        await self.ops.tick(self.now + 30)
        self.owner.send.assert_awaited_once()
        self.store.set_meta("alerts:10", {"enabled": False, "channel": 0})
        self.ops.fault(10, "job_error")
        await self.ops.tick(self.now + 60)
        self.owner.send.assert_awaited_once()
        self.store.set_meta("alerts:10", {"enabled": True, "channel": 0})
        self.bot.provider.last_status = "200"
        self.store.set_meta(
            "provider_limits", {"checked": self.now + 90, "requests_day": {"limit": 100, "remaining": 19}}
        )
        self.ops.failures.clear()
        await self.ops.tick(self.now + 90)
        self.assertIn("80%", self.owner.send.call_args.args[0])

    async def test_notification_failure_is_visible_and_can_retry(self):
        self.bot.config = replace(self.config, backup_hours=0)
        self.bot.provider.last_status = "403"
        response = SimpleNamespace(status=403, reason="forbidden")
        self.owner.send.side_effect = discord.Forbidden(response, "closed DMs")
        await self.ops.tick(self.now)
        self.assertFalse(self.store.meta("last_operator_alert")["sent"])
        await self.ops.tick(self.now + 30)
        self.owner.send.assert_awaited_once()
        self.owner.send.side_effect = None
        await self.ops.tick(self.now + 601)
        self.assertTrue(self.store.meta("last_operator_alert")["sent"])

    async def test_quota_percent_uses_same_conservative_accounting_as_reservation(self):
        self.bot.quota.config = replace(self.config, rpm=10, rpd=10, tpm=10000, tpd=10000)
        for _ in range(8):
            attempt = self.bot.quota.reserve(100, self.now)
            self.bot.quota.finish(attempt, 200, "ok")
        usage = self.bot.quota.utilization(self.now)
        self.assertEqual(usage["requests_60"], 80)
        self.assertEqual(usage["tokens_60"], 16)
