import tempfile
import time
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

from bot.backup import backup, restore
from bot.config import Config
from bot.conversation import Conversation, scope
from bot.conversation_tools import catchup_start, repair_question
from bot.search_views import SearchView
from bot.storage import Store
from tests import test_app as fixtures


class FollowupUnitTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / "db")
        self.config = Config("fake", "fake")

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def test_catchup_local_lastnight_and_explicit_bounds(self):
        now = datetime(2026, 10, 28, 14, 30, tzinfo=ZoneInfo("Asia/Bangkok"))
        start = catchup_start("ตั้งแต่เมื่อคืนพลาดอะไรบ้าง", "Asia/Bangkok", now)
        self.assertEqual(
            datetime.fromtimestamp(start, now.tzinfo), datetime(2026, 10, 27, 18, tzinfo=now.tzinfo)
        )
        self.assertEqual(
            catchup_start("วันนี้พลาดอะไร", "Asia/Bangkok", now), now.replace(hour=0, minute=0).timestamp()
        )
        self.assertEqual(catchup_start("พลาดอะไร 2 ชั่วโมง", "Asia/Bangkok", now), now.timestamp() - 7200)
        self.assertIsNone(catchup_start("คุยเล่นกัน", "Asia/Bangkok", now))
        with self.assertRaises(ValueError):
            catchup_start("พลาดอะไร 999 ชั่วโมง", "Asia/Bangkok", now)
        with self.assertRaises(ValueError):
            catchup_start("พลาดอะไรตั้งแต่ 2026-02-30", "Asia/Bangkok", now)

    def test_catchup_clock_month_and_latest_date_correction(self):
        now = datetime(2026, 10, 28, 14, 30, tzinfo=ZoneInfo("Asia/Bangkok"))
        self.assertEqual(
            catchup_start("พลาดอะไรตั้งแต่วันนี้ 09:30", "Asia/Bangkok", now),
            now.replace(hour=9, minute=30).timestamp(),
        )
        self.assertEqual(
            catchup_start("สรุปแชทตั้งแต่เดือนที่แล้ว", "Asia/Bangkok", now),
            datetime(2026, 9, 1, tzinfo=now.tzinfo).timestamp(),
        )
        fixed = "เมื่อวานพลาดอะไร\nคำแก้ความเข้าใจล่าสุดจากผู้ถาม: หมายถึงวันนี้"
        self.assertEqual(catchup_start(fixed, "Asia/Bangkok", now), now.replace(hour=0, minute=0).timestamp())
        with self.assertRaises(ValueError):
            catchup_start("พลาดอะไร 24:99", "Asia/Bangkok", now)
        with self.assertRaises(ValueError):
            catchup_start("พลาดอะไร 1120 ชั่วโมง", "Asia/Bangkok", now)
        corrected = "สรุปแชทตั้งแต่09:30\nคำแก้ความเข้าใจล่าสุดจากผู้ถาม: หมายถึง10:00"
        self.assertEqual(
            catchup_start(corrected, "Asia/Bangkok", now), now.replace(hour=10, minute=0).timestamp()
        )

    def test_correction_only_own_scope_and_requested_answer(self):
        cache = Conversation(self.config)
        cache.add((1, 2, 3, False), "เล่าเรื่องแมนยู", "ตัวละครอนิเมะ", {}, {}, [2], {10, 11})
        own = cache.get((1, 2, 3, False), [2])
        fixed = repair_question("หมายถึงทีมฟุตบอล", own, 11)
        self.assertIn("แมนยู", fixed)
        self.assertIn("ทีมฟุตบอล", fixed)
        self.assertEqual(repair_question("หมายถึงฟุตบอล", cache.get((1, 2, 4, False), [2])), "")
        self.assertEqual(repair_question("หมายถึงฟุตบอล", own, 99), "")
        self.assertIsNone(repair_question("ฉันดูฟุตบอล", own))
        self.assertEqual(repair_question("หมายถึงฟุตบอล", cache.get((1, 2, 3, True), [2])), "")

    def test_feedback_has_one_category_per_answer_and_erases_with_user(self):
        self.store.save_answer(100, 1, 2, 3, {})
        self.store.add_feedback(1, 2, 3, 100, "wrong")
        self.store.add_feedback(1, 2, 3, 100, "long")
        self.assertEqual(self.store.db.execute("SELECT category FROM feedback").fetchall()[0][0], "long")
        with self.assertRaises(ValueError):
            self.store.add_feedback(1, 2, 4, 100, "wrong")
        with self.assertRaises(ValueError):
            self.store.add_feedback(1, 99, 3, 100, "wrong")
        with self.assertRaises(ValueError):
            self.store.add_feedback(1, 2, 3, 100, "free_text")
        saved = Path(self.temp.name) / "backup"
        backup(self.store.path, saved)
        self.store.erase_user(1, 3)
        self.assertFalse(self.store.db.execute("SELECT * FROM feedback").fetchall())
        target = Path(self.temp.name) / "restored"
        restore(saved, target)
        restored = Store(target)
        self.assertFalse(restored.db.execute("SELECT * FROM feedback").fetchall())
        restored.close()


class FollowupAppTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixtures.AppTests.asyncSetUp
    asyncTearDown = fixtures.AppTests.asyncTearDown

    def job(self):
        self.bot.store.put_message(1, 10, 20, 30, self.message.content, self.message.created_at.timestamp())
        return fixtures.AppTests.job(self)

    async def test_correction_reuses_own_question_without_web_or_other_user_context(self):
        self.bot.conversation.add(scope(self.message), "อธิบายแมนยู", "ตัวละครอนิเมะ", {}, {}, [20], {5, 6})
        self.bot.conversation.add(
            (10, 20, 999, False), "OTHER_QUESTION_SECRET", "OTHER_ANSWER_SECRET", {}, {}, [20], {7}
        )
        self.message.content = "หมายถึงทีมฟุตบอล ไม่ใช่อนิเมะ"
        self.bot.web.call = AsyncMock()
        await self.bot.process(self.job())
        prompt = str(self.bot.provider.chat.call_args)
        self.assertIn("แมนยู", prompt)
        self.assertIn("ทีมฟุตบอล", prompt)
        self.assertIn("correcting your previous interpretation", prompt)
        self.assertNotIn("OTHER_QUESTION_SECRET", prompt)
        self.bot.web.call.assert_not_awaited()

    async def test_missing_or_opted_out_correction_asks_for_full_question(self):
        self.message.content = "หมายถึงแมนยู"
        await self.bot.process(self.job())
        self.bot.provider.chat.assert_not_awaited()
        self.assertIn("คำถามเต็ม", self.message.reply.call_args.args[0])

    async def test_public_fresh_correction_requires_query_review(self):
        from dataclasses import replace

        self.bot.config = replace(self.bot.config, mcp_enabled=True)
        self.bot.store.set_meta("mcp:10:20", "hybrid")
        self.bot.conversation.add(scope(self.message), "สกอร์แมนยูล่าสุด", "อนิเมะ...", {}, {}, [20], {5})
        self.message.content = "หมายถึงแมนยูทีมบอล"
        self.bot.web.call = AsyncMock()
        await self.bot.process(self.job())
        self.bot.web.call.assert_not_awaited()
        self.bot.provider.chat.assert_not_awaited()
        self.assertIn("ตรวจคำค้น", self.message.reply.call_args.args[0])

    async def test_natural_catchup_has_window_and_no_unrelated_memory(self):
        self.bot.store.remember(10, 20, 30, "UNRELATED_PRIVATE_MEMORY")
        self.message.content = "ตั้งแต่เมื่อคืนพลาดอะไรบ้าง"
        self.bot.store.put_message(1, 10, 20, 30, self.message.content, self.message.created_at.timestamp())
        self.bot.conversation.add(
            scope(self.message), "OLD_DIALOGUE", "OLD_DIALOGUE_ANSWER", {}, {}, [20], {8}
        )
        await self.bot.process(self.job())
        prompt = str(self.bot.provider.chat.call_args)
        self.assertIn("unresolved items", prompt)
        self.assertNotIn("UNRELATED_PRIVATE_MEMORY", prompt)
        self.assertNotIn("OLD_DIALOGUE_ANSWER", prompt)
        self.assertIn("สรุปช่วง", self.message.reply.call_args.args[0])
        self.assertIn("Asia/Bangkok", self.message.reply.call_args.args[0])

    async def test_summary_more_button_preserves_window_privately(self):
        self.bot.enqueue = AsyncMock(return_value="queued")
        view = SearchView(self.bot, self.message, "สรุปช่วงที่ไม่อยู่")
        view.catchup_since = time.time() - 7200
        i = SimpleNamespace(
            id=7,
            user=self.message.author,
            guild=self.guild,
            channel=self.channel,
            guild_id=10,
            channel_id=20,
            edit_original_response=AsyncMock(),
        )
        await view.submit(i, "หาเพิ่ม", mode="more")
        self.assertEqual(self.bot.enqueue.call_args.kwargs["catchup_since"], view.catchup_since)
        self.assertTrue(self.bot.enqueue.call_args.args[0].private)

    async def test_feedback_owner_expiry_optout_and_private_ack(self):
        from bot.feedback import submit

        self.bot.store.save_answer(888, 10, 20, 30, {})
        view = SearchView(self.bot, self.message, "เกมแมว")
        view.sent = SimpleNamespace(id=888)
        interaction = SimpleNamespace(
            user=SimpleNamespace(id=999),
            guild_id=10,
            channel_id=20,
            response=SimpleNamespace(defer=AsyncMock()),
            edit_original_response=AsyncMock(),
        )
        await submit(view, interaction, "wrong")
        self.assertFalse(self.bot.store.db.execute("SELECT 1 FROM feedback").fetchone())
        interaction.user.id = 30
        await submit(view, interaction, "wrong")
        self.assertEqual(self.bot.store.db.execute("SELECT count(*) FROM feedback").fetchone()[0], 1)
        self.assertTrue(interaction.response.defer.call_args.kwargs["ephemeral"])
        self.assertTrue(interaction.response.defer.call_args.kwargs["thinking"])
        view.deadline = 0
        await submit(view, interaction, "long")
        self.assertEqual(self.bot.store.db.execute("SELECT category FROM feedback").fetchone()[0], "wrong")
        self.bot.store.set_user(10, 30, optout=1)
        self.assertFalse(self.bot.store.db.execute("SELECT * FROM feedback").fetchall())
        view.stop()

    async def test_admin_report_rejects_invisible_rooms_and_never_links_private_answers(self):
        from bot.feedback import report

        self.bot.store.save_answer(100, 10, 20, 30, {})
        self.bot.store.add_feedback(10, 20, 30, 100, "wrong", private=True)
        self.bot.store.save_answer(101, 10, 99, 30, {})
        self.bot.store.add_feedback(10, 99, 30, 101, "long")
        self.guild.fetch_channels = AsyncMock(return_value=[self.channel])
        self.channel.permissions_for.return_value = SimpleNamespace(
            view_channel=True, read_message_history=True
        )
        interaction = SimpleNamespace(guild=self.guild, guild_id=10)
        with patch(
            "bot.feedback.guard",
            new=AsyncMock(return_value=(self.message.author, self.channel, [self.channel])),
        ):
            text = await report(self.bot, interaction)
        self.assertIn("ตอบมั่ว: 1", text)
        self.assertNotIn("ยาวไป: 1", text)
        self.assertNotIn("/100", text)
        self.assertNotIn("/101", text)


class ProviderQuotaTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / "db")

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def test_only_actual_headers_are_shown_and_missing_fields_are_unknown(self):
        from bot.provider_limits import capture, display, duration, utilization

        report = capture(
            self.store,
            {
                "X-RateLimit-Limit-Requests": "1000",
                "x-ratelimit-remaining-requests": "900",
                "x-ratelimit-limit-tokens": "8000",
                "x-ratelimit-remaining-tokens": "7000",
                "x-ratelimit-reset-tokens": "7.66s",
                "Authorization": "DO_NOT_STORE",
            },
            "model",
        )
        self.assertEqual(report["requests_day"]["limit"], 1000)
        self.assertEqual(report["tokens_minute"]["remaining"], 7000)
        self.assertEqual(duration("2m59.56s"), 179.56)
        self.assertIsNone(duration("secret-value"))
        self.assertNotIn("DO_NOT_STORE", str(self.store.meta("provider_limits")))
        self.assertIn("1,000", display(report))
        self.assertAlmostEqual(utilization(self.store, report["checked"])["tokens_minute"], 12.5)
        self.assertNotIn("tokens_minute", utilization(self.store, report["checked"] + 8))
        report = capture(self.store, {}, "model")
        self.assertIn("ยังไม่มีข้อมูล", display(report))
        self.assertNotIn("8,000", display(report))
        self.assertFalse(utilization(self.store, report["checked"]))

    def test_unknown_usage_keeps_reservation_and_actual_usage_releases_it(self):
        from dataclasses import replace

        from bot.quota import Quota, QuotaExceeded

        quota = Quota(self.store, replace(Config("fake", "fake"), tpm=1000, tpd=1000))
        pending = quota.reserve(700)
        with self.assertRaises(QuotaExceeded):
            quota.reserve(301)
        quota.finish(pending, None, "unknown")
        with self.assertRaises(QuotaExceeded):
            quota.reserve(301)
        quota.finish(pending, 100, "ok")
        self.assertEqual(quota.remaining_tokens(), 900)
        quota.reserve(301)


class QuotaWaitTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixtures.AppTests.asyncSetUp
    asyncTearDown = fixtures.AppTests.asyncTearDown
    job = fixtures.AppTests.job

    async def test_short_pause_retries_only_ai_operation_and_rechecks_access(self):
        from bot.provider import Answer
        from bot.quota import QuotaExceeded

        operation = AsyncMock(side_effect=[QuotaExceeded(11), Answer("answer", 100)])
        self.bot.web.call = AsyncMock()
        with patch("bot.app.asyncio.sleep", new=AsyncMock()) as sleep:
            answer = await self.bot.quota_retry(self.job(), operation, tone=1)
        self.assertEqual(answer.text, "answer")
        self.assertEqual(operation.await_count, 2)
        sleep.assert_awaited_once_with(11.2)
        self.bot.access_snapshot.assert_awaited_once()
        self.bot.web.call.assert_not_awaited()
        self.message.reply.assert_not_awaited()

    async def test_privacy_change_during_wait_blocks_retry_even_without_gateway_event(self):
        from bot.app import StaleRequest
        from bot.quota import QuotaExceeded

        operation = AsyncMock(side_effect=QuotaExceeded(11))

        async def changed(seconds):
            self.bot.store.set_user(10, 30, optout=1)

        with patch("bot.app.asyncio.sleep", new=AsyncMock(side_effect=changed)):
            with self.assertRaises(StaleRequest):
                await self.bot.quota_retry(self.job(), operation, tone=1)
        self.assertEqual(operation.await_count, 1)

    async def test_long_or_daily_admission_limit_is_not_blindly_retried(self):
        from bot.quota import QuotaExceeded

        for error in [QuotaExceeded(26), QuotaExceeded(11, daily=True)]:
            operation = AsyncMock(side_effect=error)
            with patch("bot.app.asyncio.sleep", new=AsyncMock()) as sleep:
                with self.assertRaises(QuotaExceeded):
                    await self.bot.quota_retry(self.job(), operation, tone=1)
            self.assertEqual(operation.await_count, 1)
            sleep.assert_not_awaited()

    async def test_internal_capacity_never_claims_provider_quota_or_countdown(self):
        import asyncio

        from bot.quota import QuotaExceeded

        self.bot.provider.chat.side_effect = QuotaExceeded(11, daily=True)
        worker = asyncio.create_task(self.bot.worker())
        self.bot.workers.append(worker)
        self.bot.queue.put_nowait(self.job())
        await asyncio.wait_for(self.bot.queue.join(), 2)
        text = self.message.reply.call_args.args[0]
        self.assertNotIn("โควตา", text)
        self.assertNotIn("วินาที", text)
        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)

    async def test_actual_sdk_component_defer_creates_new_private_message(self):
        import discord
        from discord.webhook.async_ import async_context

        from bot.feedback import submit

        self.bot.store.save_answer(888, 10, 20, 30, {})
        view = SearchView(self.bot, self.message, "games")
        view.sent = SimpleNamespace(id=888)
        state = SimpleNamespace(http=SimpleNamespace(proxy=None, proxy_auth=None))
        interaction = SimpleNamespace(
            type=discord.InteractionType.component,
            id=7,
            token="fake",
            _state=state,
            _session=None,
            user=self.message.author,
            guild_id=10,
            channel_id=20,
            edit_original_response=AsyncMock(),
        )
        interaction.response = discord.InteractionResponse(interaction)
        adapter = SimpleNamespace(
            create_interaction_response=AsyncMock(
                return_value={
                    "interaction": {
                        "id": "7",
                        "response_message_loading": True,
                        "response_message_ephemeral": True,
                    }
                }
            )
        )
        token = async_context.set(adapter)
        try:
            await submit(view, interaction, "wrong")
        finally:
            async_context.reset(token)
        payload = adapter.create_interaction_response.call_args.kwargs["params"].payload
        self.assertEqual(payload["type"], discord.InteractionResponseType.deferred_channel_message.value)
        self.assertEqual(payload["data"]["flags"], 64)
        interaction.edit_original_response.assert_awaited_once()
        self.assertTrue(self.bot.store.db.execute("SELECT 1 FROM feedback WHERE answer=888").fetchone())
        view.stop()
