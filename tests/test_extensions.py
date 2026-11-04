import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from bot.config import Config
from bot.context import build_context
from bot.conversation import scope
from bot.provider import Answer
from bot.storage import Store
from bot.web_tools import ToolError, WebPage, WebTools
from tests import test_app as fixtures


class ExtensionUnitTests(unittest.TestCase):
    def test_level_four_persists_and_three_does_not_enable_chaos(self):
        with tempfile.TemporaryDirectory() as folder:
            store = Store(Path(folder) / "db")
            store.set_channel(1, 2, tone=4)
            store.set_user(1, 3, tone=4)
            self.assertEqual(store.channel(1, 2)["tone"], 4)
            self.assertEqual(min(store.channel(1, 2)["tone"], store.user(1, 3)["tone"]), 4)
            store.set_user(1, 3, tone=0)
            self.assertEqual(min(store.channel(1, 2)["tone"], store.user(1, 3)["tone"]), 0)
            with self.assertRaises(ValueError):
                store.set_user(1, 3, tone=5)
            config = Config("fake", "fake")
            for level in (3, 4):
                c = build_context(
                    store, config, guild=1, channel=2, user=3, allowed=[], question="เล่นมุกหน่อย", tone=level
                )
                self.assertEqual("Mode4:" in c.messages[0]["content"], level == 4)
            store.close()


class ExtensionAppTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixtures.AppTests.asyncSetUp
    asyncTearDown = fixtures.AppTests.asyncTearDown
    job = fixtures.AppTests.job

    def enable(self):
        self.bot.config = replace(self.bot.config, mcp_enabled=True, auto_respond_channels=True)
        self.bot.web = WebTools(self.bot.config, self.bot.store)
        self.bot.web.call = AsyncMock(
            return_value=[WebPage("Source", "https://example.com", "Public facts", "now")]
        )
        self.bot.provider.chat.return_value = Answer("ข้อมูลที่อ่านได้ [W1]", 100)

    async def test_unknown_room_conversation_does_not_enable_history_storage(self):
        self.enable()
        self.bot.store.erase_channel(10, 20)
        self.bot.store.db.execute("DELETE FROM channels WHERE guild=10 AND channel=20")
        self.bot.store.db.commit()
        self.bot.access_snapshot.return_value = (self.channel, self.message.author, [], 1)
        self.assertTrue(self.bot.settings(10, 20)["respond"])
        self.assertFalse(self.bot.settings(10, 20)["readable"])
        await self.bot.process(self.job())
        self.message.reply.assert_awaited_once()
        self.assertFalse(self.bot.store.db.execute("SELECT 1 FROM messages").fetchone())
        self.assertTrue(self.bot.conversation.get(scope(self.message), []))

    async def test_personal_room_settings_do_not_disable_automatic_response(self):
        self.enable()
        self.bot.store.set_channel(10, 25, tone=4)
        self.assertTrue(self.bot.settings(10, 25)["respond"])
        self.bot.store.set_channel(10, 25, respond=0)
        self.assertFalse(self.bot.settings(10, 25)["respond"])
        self.bot.store.set_channel(10, 25, respond=1)
        self.assertTrue(self.bot.settings(10, 25)["respond"])
        self.bot.store.set_channel(10, 25, paused=1)
        self.assertTrue(self.bot.settings(10, 25)["paused"])

    async def test_direct_tool_routing(self):
        self.enable()
        self.message.content = "สรุป https://example.com/article"
        self.bot.web.save_lookup(
            scope(self.message), "old search", [WebPage("A", "https://old.example", "old", "now")]
        )
        await self.bot.process(self.job())
        self.bot.web.call.assert_awaited_once()
        self.assertEqual(self.bot.web.call.call_args.args[0], "fetch_content")
        self.assertEqual(self.bot.web.call.call_args.args[1], {"url": "https://example.com/article"})
        prompt = self.bot.provider.chat.call_args.args[0][0]["content"]
        self.assertIn("bounded extract of one public web page", prompt)
        self.assertNotIn("Never claim you opened a page", prompt)
        self.assertIn("https://example.com", self.message.reply.call_args.args[0])
        self.assertIsNone(self.bot.web.lookup(scope(self.message)))

    async def test_private_http_and_multiple_links_never_call_tools(self):
        self.enable()
        for question in (
            "อ่าน https://127.0.0.1/",
            "อ่าน https://example.com/?code=abc",
            "อ่าน http://example.com/",
            "เทียบ https://one.example/a https://two.example/b",
        ):
            with self.subTest(question=question):
                self.message.content = question
                with self.assertRaises(ToolError):
                    await self.bot.process(self.job())
                self.bot.web.call.assert_not_awaited()
        self.bot.provider.chat.assert_not_awaited()

    async def test_plain_page_failure_uses_only_one_browser_fallback(self):
        self.enable()
        self.message.content = "อ่าน https://example.com"
        self.bot.web.call.side_effect = ToolError("unavailable")
        with self.assertRaises(ToolError):
            await self.bot.process(self.job())
        self.bot.web.call.assert_awaited_once()
        self.assertEqual(self.bot.web.call.call_args.args[0], "fetch_content")
        self.bot.provider.chat.assert_not_awaited()

    async def test_empty_page_reports_no_readable_text(self):
        self.enable()
        self.message.content = "อ่าน https://example.com"
        self.bot.web.call.return_value = [WebPage("empty", "https://example.com", " ", "now")]
        with self.assertRaisesRegex(ToolError, "ไม่พบข้อความ"):
            await self.bot.process(self.job())
        self.bot.provider.chat.assert_not_awaited()

    async def test_news_feed_miss_uses_search_without_third_call(self):
        self.enable()
        self.message.content = "ข่าว qwen ล่าสุด"
        self.bot.web.call.return_value = []
        await self.bot.process(self.job())
        self.bot.web.call.assert_awaited_once()
        self.assertEqual(self.bot.web.call.call_args.args[0], "search")
        self.bot.provider.chat.assert_not_awaited()

    async def test_transcript_failure_never_fabricates_video_summary(self):
        self.enable()
        self.message.content = "สรุป https://youtu.be/dQw4w9WgXcQ"
        self.bot.web.call.side_effect = ToolError("no captions")
        with self.assertRaises(ToolError):
            await self.bot.process(self.job())
        self.bot.provider.chat.assert_not_awaited()

    async def test_permission_change_prevents_browser_fallback(self):
        self.enable()
        self.message.content = "อ่าน https://example.com"

        async def changed(*args, **kwargs):
            self.bot.invalidate()
            return [WebPage("page", "https://example.com", "content", "now")]

        self.bot.web.call.side_effect = changed
        await self.bot.process(self.job())
        self.bot.web.call.assert_awaited_once()
        self.bot.provider.chat.assert_not_awaited()
        self.message.reply.assert_not_awaited()

    async def test_can_chat_without_read_history_permission(self):
        self.enable()
        self.guild.me = SimpleNamespace(id=99)
        self.channel.permissions_for.return_value = SimpleNamespace(read_message_history=False)
        self.bot.access_snapshot.return_value = (self.channel, self.message.author, [], 1)
        await self.bot.process(self.job())
        self.channel.fetch_message.assert_not_awaited()
        self.message.reply.assert_not_awaited()
        self.channel.send.assert_awaited_once()

    async def test_fresh_acl_checks_visibility_and_send_without_requiring_history(self):
        from bot.app import Companion

        self.enable()
        self.guild.roles = []
        self.guild.owner_id = 30
        self.guild.fetch_channels = AsyncMock(return_value=[self.channel])
        member = SimpleNamespace(id=99)
        self.guild.fetch_member = AsyncMock(
            side_effect=lambda uid: self.message.author if uid == 30 else member
        )
        self.bot.fetch_guild = AsyncMock(return_value=SimpleNamespace(owner_id=30, roles=[]))
        permissions = SimpleNamespace(view_channel=True, send_messages=True, read_message_history=False)
        self.channel.permissions_for.return_value = permissions
        snapshot = await Companion.access_snapshot(self.bot, self.message)
        self.assertIsNotNone(snapshot)
        self.assertEqual(snapshot[2], [])
        permissions.send_messages = False
        self.assertIsNone(await Companion.access_snapshot(self.bot, self.message))
        permissions.send_messages = True
        permissions.view_channel = False
        self.assertIsNone(await Companion.access_snapshot(self.bot, self.message))

    async def test_pause_does_not_accidentally_lower_new_room_ceiling(self):
        self.enable()
        self.assertEqual(self.bot.settings(10, 26)["tone"], 4)
        self.bot.store.set_channel(10, 26, paused=1)
        self.bot.store.set_channel(10, 26, paused=0)
        self.assertEqual(self.bot.settings(10, 26)["tone"], 4)
        self.bot.store.set_channel(10, 26, tone=2)
        self.assertEqual(self.bot.settings(10, 26)["tone"], 2)
