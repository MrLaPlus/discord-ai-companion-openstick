import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from bot.config import Config
from bot.context import build_context
from bot.history import HistoryLoader, plan_history, requested_channels
from bot.storage import Store


class FakeChannel:
    def __init__(self, cid, messages):
        self.id, self.name, self.messages = cid, f"room{cid}", messages
        self.calls = []

    async def history(self, **kwargs):
        self.calls.append(kwargs)
        items = sorted(self.messages, key=lambda m: (m.created_at, m.id), reverse=not kwargs["oldest_first"])
        items = [
            m
            for m in items
            if (
                not kwargs.get("after")
                or (
                    m.id > kwargs["after"].id
                    if hasattr(kwargs["after"], "id")
                    else m.created_at > kwargs["after"]
                )
            )
            and (
                not kwargs.get("before")
                or (
                    m.id < kwargs["before"].id
                    if hasattr(kwargs["before"], "id")
                    else m.created_at < kwargs["before"]
                )
            )
        ]
        for item in items[: kwargs["limit"]]:
            yield item


def message(mid, year=2025, author=30, bot=False):
    return SimpleNamespace(
        id=mid,
        author=SimpleNamespace(id=author, bot=bot),
        webhook_id=None,
        content="เคยนัดเล่นเกมแมว",
        created_at=datetime(year, 5, 1, tzinfo=timezone.utc),
    )


class HistoryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / "db")
        self.config = Config("fake-discord", "fake-ai")
        self.loader = HistoryLoader(self.store, self.config)

    async def asyncTearDown(self):
        self.store.close()
        self.temp.cleanup()

    async def load(self, channels, question="ก่อนบอทเข้าเคยคุยอะไร", active=lambda: True):
        return await self.loader.hydrate(
            guild=10,
            destination=20,
            channels=channels,
            question=question,
            active=active,
            joined=datetime(2026, 10, 27, tzinfo=timezone.utc),
        )

    async def test_prejoin_messages_are_fetched_without_manual_import(self):
        channel = FakeChannel(20, [message(1)])
        plan, report = await self.load([channel])
        self.assertEqual(report["stored"], 1)
        self.assertEqual(channel.calls[0]["before"].year, 2026)
        context = build_context(
            self.store,
            self.config,
            guild=10,
            channel=20,
            user=30,
            allowed=[20],
            question="ก่อนบอทเข้าเคยคุยอะไร",
            tone=1,
            history_plan=plan,
        )
        self.assertIn("นัดเล่นเกมแมว", str(context.messages))
        self.store.maintain(30, 180, 1000)
        self.assertEqual(len(self.store.recent(10, [20])), 1)

    async def test_optout_and_bot_messages_are_not_hydrated(self):
        self.store.set_user(10, 31, optout=1)
        channel = FakeChannel(20, [message(1), message(2, author=31), message(3, bot=True)])
        _, report = await self.load([channel])
        self.assertEqual(report["scanned"], 3)
        self.assertEqual([r["id"] for r in self.store.recent(10, [20])], [1])

    async def test_total_budget_and_cache_bound_requests(self):
        self.loader.config = replace(self.config, history_batch=2, history_total=3)
        channels = [FakeChannel(c, [message(c * 10 + i) for i in range(4)]) for c in (20, 21, 22)]
        _, report = await self.load(channels)
        self.assertEqual(report["scanned"], 3)
        self.assertTrue(report["partial"])
        self.assertEqual(len(channels[2].calls), 0)

    async def test_repeat_query_uses_cache(self):
        channel = FakeChannel(20, [message(1)])
        await self.load([channel])
        _, report = await self.load([channel])
        self.assertEqual(len(channel.calls), 1)
        self.assertEqual(report["scanned"], 0)

    async def test_no_access_or_explicit_foreign_channel_is_not_fetched(self):
        channel = FakeChannel(20, [message(1)])
        await self.load([channel], question="ห้อง <#999> เคยคุยอะไร")
        self.assertEqual(channel.calls, [])
        await self.load([channel], active=lambda: False)
        self.assertEqual(channel.calls, [])

    def test_date_year_and_thai_timezone_plans(self):
        now = datetime(2026, 10, 27, 12, tzinfo=timezone.utc)
        yesterday = plan_history("เมื่อวานคุยอะไร", "Asia/Bangkok", now=now)
        self.assertEqual(yesterday.after.day, 26)
        self.assertEqual(yesterday.after.hour, 0)
        self.assertEqual(yesterday.after.utcoffset().total_seconds(), 7 * 3600)
        date = plan_history("วันที่ 2025-05-01 คุยอะไร", "Asia/Bangkok", now=now)
        self.assertEqual((date.before - date.after).days, 1)
        buddhist = plan_history("ปี 2568 มีอะไร", "Asia/Bangkok", now=now)
        self.assertEqual(buddhist.after.year, 2025)

    async def page(self, channels, question="ก่อนบอทเข้าเกมแมว", key=(10, 20, 30, False)):
        return await self.loader.hydrate(
            guild=10,
            destination=20,
            channels=channels,
            question=question,
            active=lambda: True,
            joined=datetime(2026, 10, 27, tzinfo=timezone.utc),
            session_key=key,
        )

    async def test_more_advances_each_channel_by_id_without_reusing_cached_page(self):
        self.loader.config = replace(self.config, history_batch=2)
        channels = [FakeChannel(cid, [message(cid * 100 + i) for i in range(5)]) for cid in (20, 21)]
        first, _ = await self.page(channels)
        self.loader.commit(first)
        second, report = await self.page(channels, "หาเก่ากว่านี้")
        self.assertFalse(set(first.message_ids) & set(second.message_ids))
        self.assertEqual(len(second.message_ids), 4)
        self.assertEqual(channels[0].calls[-1]["before"].id, 2003)
        self.assertEqual(channels[1].calls[-1]["before"].id, 2103)
        self.assertTrue(report["more"])

    async def test_failed_answer_does_not_commit_cursor_and_sessions_are_private(self):
        self.loader.config = replace(self.config, history_batch=1)
        channels = [FakeChannel(20, [message(1), message(2), message(3)])]
        first, _ = await self.page(channels)
        self.loader.commit(first)
        second, _ = await self.page(channels, "หาเพิ่ม")
        retry, _ = await self.page(channels, "หาเพิ่ม")
        self.assertEqual(second.message_ids, retry.message_ids)
        for key in [(10, 20, 31, False), (10, 20, 30, True), (10, 21, 30, False)]:
            _, report = await self.page(channels, "หาเพิ่ม", key)
            self.assertTrue(report["no_session"])

    async def test_end_of_search_returns_empty_page_not_old_context(self):
        channels = [FakeChannel(20, [message(1)])]
        first, _ = await self.page(channels)
        self.loader.commit(first)
        second, report = await self.page(channels, "หาเพิ่ม")
        self.assertEqual(second.message_ids, ())
        self.assertTrue(report["exhausted"])
        context = build_context(
            self.store,
            self.config,
            guild=10,
            channel=20,
            user=30,
            allowed=[20],
            question="หาเพิ่ม",
            tone=1,
            history_plan=second,
        )
        self.assertEqual(context.sources, {})

    async def test_search_expiration_and_permission_loss_do_not_reintroduce_sources(self):
        first, _ = await self.page([FakeChannel(20, [message(1)]), FakeChannel(21, [message(2)])])
        self.loader.commit(first)
        second, _ = await self.page([FakeChannel(20, [])], "หาเพิ่ม")
        self.assertNotIn(21, second.session.cursors)
        first.session.touched -= 1801
        _, report = await self.page([], "หาเพิ่ม")
        self.assertTrue(report["no_session"])

    async def test_month_refinement_preserves_topic_and_sets_calendar_bounds(self):
        first, _ = await self.page([FakeChannel(20, [message(1)])])
        self.loader.commit(first)
        month, _ = await self.page([FakeChannel(20, [message(1)])], "เฉพาะเดือนนั้น")
        self.assertEqual((month.after.month, month.before.month), (5, 6))
        self.assertEqual(month.session.question, first.session.question)

    async def test_old_page_is_retrieved_even_beyond_recent_1200_rows(self):
        first, _ = await self.page([FakeChannel(20, [message(1)])])
        for i in range(2, 1250):
            self.store.put_message(
                i, 10, 20, 30, "newer unrelated", datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp()
            )
        context = build_context(
            self.store,
            self.config,
            guild=10,
            channel=20,
            user=30,
            allowed=[20],
            question="เกมแมว",
            tone=1,
            history_plan=first,
        )
        self.assertIn("เกมแมว", str(context.messages))
        # Match the actual excluded row, not an ordinary word in system instructions.
        self.assertNotIn("newer unrelated", str(context.messages))

    def test_channel_name_and_mention_resolution(self):
        channel = SimpleNamespace(id=20, name="💬・พิมพ์คุย")
        self.assertEqual(requested_channels("ห้องพิมพ์คุยเคยคุยอะไร", [channel]), {20})
        self.assertEqual(requested_channels("ห้อง <#21>", [channel]), {21})
        bot = SimpleNamespace(id=21, name="bot")
        self.assertEqual(requested_channels("bot ใช้ model อะไร", [bot]), set())
        self.assertEqual(requested_channels("ห้อง bot คุยอะไร", [bot]), {21})

    def test_natural_month_year_night_and_unspaced_date(self):
        now = datetime(2026, 10, 27, 12, tzinfo=timezone.utc)
        night = plan_history("เมื่อคืนคุยอะไร", "Asia/Bangkok", now=now)
        self.assertEqual(
            (night.after.day, night.after.hour, night.before.day, night.before.hour), (26, 18, 27, 6)
        )
        month = plan_history("เดือนที่แล้ว", "Asia/Bangkok", now=now)
        self.assertEqual((month.after.month, month.before.month), (9, 10))
        year = plan_history("ปีที่แล้ว", "Asia/Bangkok", now=now)
        self.assertEqual((year.after.year, year.before.year), (2025, 2026))
        date = plan_history("วันที่2025-05-01คุยอะไร", "Asia/Bangkok", now=now)
        self.assertEqual(date.after.day, 1)
