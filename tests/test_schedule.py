import asyncio
import discord
import tempfile
import time
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

from bot.backup import backup, restore
from bot.schedule import Scheduler, cancel, create, listing, parse_request, request_reply, synchronized
from bot.storage import Store
from bot.web_tools import WebPage


class ScheduleTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'db'
        self.store = Store(self.path)
        self.addCleanup(self.store.close)
        self.zone = 'Asia/Bangkok'
        self.now = datetime(2026, 11, 5, 6, 55, tzinfo=ZoneInfo(self.zone)).timestamp()
        self.telegram = SimpleNamespace(allowed={123}, send=AsyncMock(), api=AsyncMock(return_value={}))
        self.bot = SimpleNamespace(store=self.store, config=SimpleNamespace(timezone=self.zone, mcp_enabled=True), telegram=self.telegram)
        self.scheduler = Scheduler(self.bot)

    def add(self, text='เตือนทุกวัน 07:00 กินข้าว', owner=123):
        req = parse_request(text, self.zone, self.now)
        return create(self.store, 'telegram', -1, owner, owner, req, self.now)

    async def test_natural_news_and_weekly_reminder(self):
        news = parse_request('ทุกเช้า 7 โมง ส่งข่าว AI ที่สำคัญ 3 เรื่อง', self.zone, self.now)
        self.assertEqual((news['kind'], news['body'], news['daily']), ('news', 'AI', True))
        weekly = parse_request('ทุกวันศุกร์ 07:00 เตือนเช็กพื้นที่บอร์ด', self.zone, self.now)
        self.assertEqual(weekly['weekday'], 4)
        self.assertEqual(weekly['body'], 'เช็กพื้นที่บอร์ด')
        job = create(self.store, 'telegram', -1, 123, 123, weekly, self.now)
        await self.scheduler.tick(weekly['next_at'], synced=True)
        row = self.store.db.execute('SELECT * FROM scheduled_jobs WHERE id=?', (job,)).fetchone()
        self.assertEqual(row['next_at'], weekly['next_at'] + 7 * 86400)
        self.assertIn('ทุกวันศุกร์', listing(self.store, 'telegram', -1, 123, self.zone))
        self.assertIn('ระบุเวลา', request_reply(self.store, 'telegram', -1, 123, 123, 'ทุกวันศุกร์เตือนเช็กพื้นที่บอร์ด', self.zone, self.now))

    async def test_reschedule_owned_job_and_old_backup_cannot_restore_it(self):
        from bot.schedule_edit import edit_reply
        job = self.add('เตือนทุกวัน 07:00 กินยา')
        snap = Path(self.tmp.name) / 'edit-before.sqlite3'
        backup(self.path, snap)
        result = edit_reply(self.store, 'telegram', -1, 123, 123, 'เลื่อนเตือนกินยาเป็น 8 โมง', self.zone, self.now)
        self.assertIn('เลื่อนแล้ว', result)
        row = self.store.db.execute("SELECT * FROM scheduled_jobs WHERE status='active'").fetchone()
        self.assertNotEqual(row['id'], job)
        self.assertEqual(datetime.fromtimestamp(row['next_at'], ZoneInfo(self.zone)).hour, 8)
        dest = Path(self.tmp.name) / 'edit-restored' / 'db'
        dest.parent.mkdir()
        restore(snap, dest, privacy_folder=Path(self.tmp.name))
        restored = Store(dest)
        try:
            self.assertEqual(restored.db.execute('SELECT count(*) FROM scheduled_jobs WHERE id=?', (job,)).fetchone()[0], 0)
        finally:
            restored.close()

    async def test_reschedule_cannot_modify_other_user_or_ambiguous(self):
        from bot.schedule_edit import edit_reply
        self.add('เตือนทุกวัน 07:00 กินยา', owner=456)
        reply = edit_reply(self.store, 'telegram', -1, 123, 123, 'เลื่อนเตือนกินยาเป็น 8 โมง', self.zone, self.now)
        self.assertIn('ไม่พบ', reply)
        selected = self.add('เตือนทุกวัน 07:00 กินยา')
        self.add('เตือนทุกวัน 07:00 กินยาหลังอาหาร')
        reply = edit_reply(self.store, 'telegram', -1, 123, 123, 'เลื่อนเตือนกินยาเป็น 8 โมง', self.zone, self.now)
        self.assertIn('หลายรายการ', reply)
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM scheduled_jobs WHERE status='active'").fetchone()[0], 3)
        reply = edit_reply(self.store, 'telegram', -1, 123, 123, f'เลื่อนเตือนกินยา รหัส {selected[:8]} เป็น 08:00', self.zone, self.now)
        self.assertIn('เลื่อนแล้ว', reply)
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM scheduled_jobs WHERE status='active'").fetchone()[0], 3)
        self.assertIn('ข้ามเฉพาะ', edit_reply(self.store, 'telegram', -1, 123, 123, 'หยุดรายงานข่าววันเสาร์', self.zone, self.now))

    async def test_failed_replacement_does_not_leave_old_job_active(self):
        from bot.schedule_edit import edit_reply
        job = self.add('เตือนทุกวัน 07:00 กินยา')
        self.store.db.execute("CREATE TRIGGER reject_new_schedule BEFORE INSERT ON scheduled_jobs BEGIN SELECT RAISE(ABORT, 'injected failure'); END")
        reply = edit_reply(self.store, 'telegram', -1, 123, 123, 'เลื่อนเตือนกินยาเป็น 08:00', self.zone, self.now)
        self.assertIn('ยกเลิกรายการเดิมแล้ว', reply)
        self.assertIsNone(self.store.db.execute('SELECT id FROM scheduled_jobs WHERE id=?', (job,)).fetchone())

    async def test_skip_saturday_once_and_repeating(self):
        from bot.schedule_edit import edit_reply
        saturday = datetime(2026, 11, 7, 6, 55, tzinfo=ZoneInfo(self.zone)).timestamp()
        request = parse_request('รายงานข่าว AI ทุกวัน 07:00', self.zone, saturday)
        create(self.store, 'telegram', -1, 123, 123, request, saturday)
        result = edit_reply(self.store, 'telegram', -1, 123, 123, 'ข้ามรายงานข่าว AI วันเสาร์นี้', self.zone, saturday)
        self.assertIn('ข้ามวันเสาร์', result)
        await self.scheduler.tick(saturday + 300, synced=True)
        self.telegram.send.assert_not_awaited()
        row = self.store.db.execute("SELECT * FROM scheduled_jobs WHERE status='active'").fetchone()
        self.assertEqual(row['last_result'], 'skipped')
        self.assertEqual(row['next_at'], saturday + 300 + 86400)
        result = edit_reply(self.store, 'telegram', -1, 123, 123, 'หยุดรายงานข่าว AI ทุกวันเสาร์', self.zone, saturday + 600)
        self.assertIn('ข้ามทุกวันเสาร์', result)
        next_saturday = saturday + 7 * 86400 + 300
        with self.store.db:
            self.store.db.execute("UPDATE scheduled_jobs SET next_at=? WHERE status='active'", (next_saturday,))
        await self.scheduler.tick(next_saturday, synced=True)
        self.telegram.send.assert_not_awaited()
        row = self.store.db.execute("SELECT * FROM scheduled_jobs WHERE status='active'").fetchone()
        self.assertGreater(row['next_at'], next_saturday)
        self.assertEqual(row['last_result'], 'skipped')

    async def test_task_overview_is_owner_and_discord_channel_scoped(self):
        from bot.task_overview import task_overview
        request = parse_request('เตือนทุกวัน 07:00 กินยา', self.zone, self.now)
        create(self.store, 'telegram', -1, 123, 123, request, self.now)
        create(self.store, 'telegram', -1, 456, 456, {**request, 'body': 'OTHER_PRIVATE'}, self.now)
        create(self.store, 'discord', 1, 10, 123, request, self.now)
        create(self.store, 'discord', 1, 11, 123, {**request, 'body': 'OTHER_CHANNEL'}, self.now)
        telegram = task_overview(self.store, 'telegram', -1, 123, 123, self.zone)
        discord = task_overview(self.store, 'discord', 1, 10, 123, self.zone)
        self.assertIn('กินยา', telegram)
        self.assertNotIn('OTHER_PRIVATE', telegram)
        self.assertIn('กินยา', discord)
        self.assertNotIn('OTHER_CHANNEL', discord)
        self.assertIn('ไม่มีรายการ', task_overview(self.store, 'discord', 1, 10, 456, self.zone))

    async def test_due_once_daily_and_no_duplicate(self):
        job = self.add()
        due = self.now + 300
        await self.scheduler.tick(due, synced=True)
        await self.scheduler.tick(due + 5, synced=True)
        self.telegram.send.assert_awaited_once()
        row = self.store.db.execute('SELECT * FROM scheduled_jobs WHERE id=?', (job,)).fetchone()
        self.assertEqual(row['status'], 'active')
        self.assertEqual(row['next_at'], due + 86400)
        self.assertEqual(row['last_result'], 'sent')

    async def test_offline_grace_and_missed(self):
        one = self.add('เตือนวันนี้ 07:00 กินข้าว')
        due = self.now + 300
        await self.scheduler.tick(due + 899, synced=True)
        self.telegram.send.assert_awaited_once()
        self.telegram.send.reset_mock()
        two = self.add('เตือนวันนี้ 07:00 กินยา')
        await self.scheduler.tick(due + 901, synced=True)
        self.telegram.send.assert_not_awaited()
        row = self.store.db.execute('SELECT * FROM scheduled_jobs WHERE id=?', (two,)).fetchone()
        self.assertEqual(row['status'], 'finished')
        self.assertEqual(row['last_result'], 'missed')
        self.assertIn('เคยพลาด', listing(self.store, 'telegram', -1, 123, self.zone))

    async def test_grace_is_rechecked_after_slow_permission_check(self):
        self.add('เตือนวันนี้ 07:00 กินข้าว')
        due = self.now + 300
        with patch('bot.schedule.time.time', side_effect=[due + 899, due + 899, due + 901]):
            await self.scheduler.tick(synced=True)
        self.telegram.send.assert_not_awaited()
        self.assertEqual(self.store.db.execute('SELECT last_result FROM scheduled_jobs').fetchone()[0], 'missed')

    async def test_unsynced_and_unauthorized_do_not_send(self):
        self.add()
        await self.scheduler.tick(self.now + 300, synced=False)
        self.telegram.allowed = set()
        await self.scheduler.tick(self.now + 301, synced=True)
        self.telegram.send.assert_not_awaited()

    async def test_kernel_clock_sync_fails_closed(self):
        with patch('bot.schedule.ctypes.CDLL') as library:
            library.return_value.ntp_gettime.return_value = 0
            self.assertTrue(await synchronized())
            library.return_value.ntp_gettime.return_value = 5
            self.assertFalse(await synchronized())
            library.return_value.ntp_gettime.return_value = -1
            self.assertFalse(await synchronized())
        with patch('bot.schedule.ctypes.CDLL', side_effect=OSError):
            self.assertFalse(await synchronized())

    async def test_telegram_offline_then_recovers_within_grace(self):
        self.add('เตือนวันนี้ 07:00 กินยา')
        self.telegram.api.side_effect = RuntimeError('offline')
        await self.scheduler.tick(self.now + 300, synced=True)
        self.telegram.send.assert_not_awaited()
        self.telegram.api.side_effect = None
        await self.scheduler.tick(self.now + 360, synced=True)
        self.telegram.send.assert_awaited_once()

    async def test_cancel_during_connection_check_prevents_send(self):
        job = self.add('เตือนวันนี้ 07:00 กินยา')
        async def cancel_while_checking(*args, **kwargs):
            cancel(self.store, 'telegram', -1, 123, job[:8])
            return {}
        self.telegram.api.side_effect = cancel_while_checking
        await self.scheduler.tick(self.now + 300, synced=True)
        self.telegram.send.assert_not_awaited()

    async def test_cancel_is_owner_scoped_and_survives_restore(self):
        job = self.add()
        snap = Path(self.tmp.name) / 'snapshot.sqlite3'
        backup(self.path, snap)
        self.assertFalse(cancel(self.store, 'telegram', -1, 456, job[:8]))
        self.assertTrue(cancel(self.store, 'telegram', -1, 123, job[:8]))
        dest = Path(self.tmp.name) / 'restore' / 'db'
        dest.parent.mkdir()
        restore(snap, dest, privacy_folder=Path(self.tmp.name))
        restored = Store(dest)
        try:
            self.assertEqual(restored.db.execute('SELECT count(*) FROM scheduled_jobs').fetchone()[0], 0)
        finally:
            restored.close()

    async def test_sent_job_not_replayed_after_old_backup_restore(self):
        self.add('เตือนวันนี้ 07:00 กินข้าว')
        snap = Path(self.tmp.name) / 'snapshot.sqlite3'
        backup(self.path, snap)
        await self.scheduler.tick(self.now + 300, synced=True)
        dest = Path(self.tmp.name) / 'restore' / 'db'
        dest.parent.mkdir()
        restore(snap, dest, privacy_folder=Path(self.tmp.name))
        restored = Store(dest)
        try:
            self.assertEqual(restored.db.execute('SELECT status FROM scheduled_jobs').fetchone()[0], 'finished')
        finally:
            restored.close()

    async def test_reopening_store_preserves_sent_and_missed_status(self):
        first = self.add('เตือนวันนี้ 07:00 กินข้าว')
        await self.scheduler.tick(self.now + 300, synced=True)
        second = self.add('เตือนวันนี้ 07:00 กินยา')
        await self.scheduler.tick(self.now + 1201, synced=True)
        reopened = Store(self.path)
        try:
            states = {r['id']: r['last_result'] for r in reopened.db.execute('SELECT id,last_result FROM scheduled_jobs')}
            self.assertEqual(states[first], 'sent')
            self.assertEqual(states[second], 'missed')
        finally:
            reopened.close()

    async def test_daily_news_calls_web_and_reports_source(self):
        self.bot.web = SimpleNamespace(call=AsyncMock(return_value=[WebPage('Title', 'https://example.com', 'News', 'now')]))
        self.bot.provider = SimpleNamespace(chat=AsyncMock(return_value=SimpleNamespace(text='สรุปข่าว')))
        self.add('รายงานข่าว AI ทุกวัน 07:00')
        await self.scheduler.tick(self.now + 300, synced=True)
        self.assertIn('https://example.com', self.telegram.send.await_args.args[1])

    async def test_news_failure_reports_no_verified_news(self):
        self.bot.web = SimpleNamespace(call=AsyncMock(side_effect=RuntimeError('private error')))
        self.add('รายงานข่าว AI ทุกวัน 07:00')
        await self.scheduler.tick(self.now + 300, synced=True)
        text = self.telegram.send.await_args.args[1]
        self.assertIn('ไม่สำเร็จ', text)
        self.assertNotIn('private error', text)

    async def test_crash_after_claim_is_not_replayed(self):
        from bot.privacy import claim_schedule
        job = self.add('เตือนวันนี้ 07:00 กินยา')
        due = self.now + 300
        self.assertTrue(claim_schedule(self.store.privacy_folder, job, due, due))
        await self.scheduler.tick(due, synced=True)
        self.telegram.send.assert_not_awaited()
        row = self.store.db.execute('SELECT status,last_result FROM scheduled_jobs WHERE id=?', (job,)).fetchone()
        self.assertEqual(tuple(row), ('finished', 'uncertain'))

    async def test_discord_checks_permissions_and_disables_mentions(self):
        channel = MagicMock(spec=discord.TextChannel)
        channel.id = 20
        channel.permissions_for.return_value = SimpleNamespace(view_channel=True, send_messages=True)
        channel.send = AsyncMock()
        guild = SimpleNamespace(id=10, owner_id=1, roles=[], fetch_channels=AsyncMock(return_value=[channel]),
                                fetch_member=AsyncMock(return_value=object()),
                                get_channel=lambda cid: channel if cid == 20 else None)
        self.bot.is_ready = lambda: True
        self.bot.in_scope = lambda gid: True
        self.bot.get_guild = lambda gid: guild
        self.bot.fetch_guild = AsyncMock(return_value=guild)
        self.bot.user = SimpleNamespace(id=99)
        self.bot.settings = lambda g, c: {'respond': True, 'paused': False}
        request = parse_request('เตือนวันนี้ 07:00 ข้อความ @everyone', self.zone, self.now)
        create(self.store, 'discord', 10, 20, 123, request, self.now)
        await self.scheduler.tick(self.now + 300, synced=True)
        channel.send.assert_awaited_once()
        self.assertFalse(channel.send.await_args.kwargs['allowed_mentions'].everyone)

    async def test_discord_revoked_permissions_does_not_send(self):
        channel = MagicMock(spec=discord.TextChannel)
        channel.id = 20
        channel.permissions_for.return_value = SimpleNamespace(view_channel=False, send_messages=True)
        channel.send = AsyncMock()
        guild = SimpleNamespace(id=10, owner_id=1, roles=[], fetch_channels=AsyncMock(return_value=[channel]),
                                fetch_member=AsyncMock(return_value=object()),
                                get_channel=lambda cid: channel)
        self.bot.is_ready = lambda: True
        self.bot.in_scope = lambda gid: True
        self.bot.get_guild = lambda gid: guild
        self.bot.fetch_guild = AsyncMock(return_value=guild)
        self.bot.user = SimpleNamespace(id=99)
        self.bot.settings = lambda g, c: {'respond': True, 'paused': False}
        request = parse_request('เตือนวันนี้ 07:00 ข้อความ', self.zone, self.now)
        create(self.store, 'discord', 10, 20, 123, request, self.now)
        await self.scheduler.tick(self.now + 300, synced=True)
        channel.send.assert_not_awaited()

    async def test_discord_scheduled_news_rechecks_search_permission(self):
        channel = MagicMock(spec=discord.TextChannel)
        channel.id = 20
        channel.permissions_for.return_value = SimpleNamespace(view_channel=True, send_messages=True)
        channel.send = AsyncMock()
        guild = SimpleNamespace(id=10, owner_id=1, roles=[], fetch_channels=AsyncMock(return_value=[channel]),
                                fetch_member=AsyncMock(return_value=object()), get_channel=lambda cid: channel)
        self.bot.is_ready = lambda: True
        self.bot.in_scope = lambda gid: True
        self.bot.get_guild = lambda gid: guild
        self.bot.fetch_guild = AsyncMock(return_value=guild)
        self.bot.user = SimpleNamespace(id=99)
        self.bot.settings = lambda g, c: {'respond': True, 'paused': False}
        self.bot.tool_allowed = lambda *args: False
        self.bot.web = SimpleNamespace(call=AsyncMock())
        request = parse_request('รายงานข่าว AI ทุกวัน 07:00', self.zone, self.now)
        create(self.store, 'discord', 10, 20, 123, request, self.now)
        await self.scheduler.tick(self.now + 300, synced=True)
        self.bot.web.call.assert_not_awaited()
        self.assertIn('ไม่สำเร็จ', channel.send.await_args.args[0])

    def test_thai_time_parser_and_clock(self):
        parsed = parse_request('เตือนฉันพรุ่งนี้ 07:00 ให้กินยา', self.zone, self.now)
        self.assertEqual(parsed['body'], 'กินยา')
        self.assertEqual(parsed['next_at'], self.now + 86700)
        self.assertEqual(parse_request('ปลุกทุกวัน 07:00', self.zone, self.now)['body'], 'ได้เวลาตื่นแล้ว')
        self.assertIn('06:55', request_reply(self.store, 'telegram', -1, 123, 123, '/time', self.zone, self.now))
        self.assertIn('วันพฤหัสบดี', request_reply(self.store, 'telegram', -1, 123, 123, 'วันนี้วันอะไรครับ', self.zone, self.now))
        self.assertIsNone(request_reply(self.store, 'telegram', -1, 123, 123, 'สวัสดี', self.zone, self.now))
        self.assertIsNone(request_reply(self.store, 'telegram', -1, 123, 123, 'รายงานข่าว AI ล่าสุด', self.zone, self.now))
        with self.assertRaises(ValueError):
            parse_request('เตือนทุกวัน 25:00 ไม่ถูก', self.zone, self.now)
