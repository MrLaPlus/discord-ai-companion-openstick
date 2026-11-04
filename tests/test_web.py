import json
import tempfile
import time
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.app import Job
from bot.config import Config
from bot.context import build_context
from bot.conversation import scope
from bot.provider import Answer, Provider, ProviderError
from bot.rendering import render_answer
from bot.search_views import SearchMessage, SearchView
from bot.storage import Store
from bot.web_tools import (
    TOOL_FIELDS,
    ToolError,
    WebPage,
    WebTools,
    checked_url,
    explicit_query,
    needs_query_confirmation,
)
from tests import test_app as fixtures
from tests.test_provider import Response, Session


class WebUnitTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / "db")
        self.config = Config("fake", "fake", mcp_enabled=True)
        self.web = WebTools(self.config, self.store)

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def test_public_url_boundary(self):
        for url in [
            "http://example.com",
            "https://127.0.0.1",
            "https://[::1]",
            "https://169.254.169.254/latest",
            "https://10.0.0.1",
            "https://localhost",
            "https://example.com:9000",
            "https://a:b@example.com",
            "https://example.com/?token=private",
            "https://discord.com/channels/1/2/3",
        ]:
            with self.subTest(url=url), self.assertRaises(ToolError):
                checked_url(url)
        self.assertEqual(checked_url("https://example.com/a#b"), "https://example.com/a")

    def test_queries_never_derive_private_context(self):
        for q in [
            "ค้นเว็บเรื่องที่คุยในห้อง",
            "ค้นหา <@123> วันนี้",
            "เบอร์โทร 0812345678",
            "ค้นหา name@example.com",
            "อันนั้นราคาเท่าไหร่",
            "ค้นเรื่องโรคของเพื่อน",
        ]:
            self.assertTrue(needs_query_confirmation(q), q)
        self.assertFalse(needs_query_confirmation("สกอร์บอลแมนยูล่าสุด"))
        self.assertIn("Manchester United", explicit_query("สกอบอนแมนยูล่าสุด"))

    def test_sdk_json_text_and_structured_envelopes(self):
        data = {"ok": True, "results": []}
        result = SimpleNamespace(
            structured_content=None,
            is_error=False,
            content=[SimpleNamespace(type="text", text=json.dumps(data))],
        )
        self.assertEqual(WebTools.result_data(result), data)
        result.structured_content = data
        self.assertEqual(WebTools.result_data(result), data)
        result.is_error = True
        with self.assertRaises(ToolError):
            WebTools.result_data(result)

    def test_tool_quota_is_durable_and_content_free(self):
        for _ in range(6):
            self.web.reserve(10, 30, "search")
        with self.assertRaises(ToolError):
            WebTools(self.config, self.store).reserve(10, 30, "search")
        usage = self.store.meta("mcp_usage")
        self.assertEqual(len(usage), 6)
        self.assertEqual(len(usage[0][1]), 64)
        self.assertNotIn(30, usage[0])

    def test_disabled_personal_tool_cap_admits_beyond_thirty_without_reset(self):
        unlimited = WebTools(replace(self.config, mcp_user_daily=0), self.store)
        for index in range(31):
            with patch("bot.web_tools.time.time", return_value=100000 + index * 61):
                unlimited.reserve(10, 30, "search")
        with patch("bot.web_tools.time.time", return_value=102000):
            with self.assertRaises(ToolError):
                WebTools(replace(self.config, mcp_user_daily=30), self.store).reserve(10, 30, "search")
            unlimited.reserve(10, 30, "search")
        self.assertEqual(len(self.store.meta("mcp_usage")), 32)

    def test_ram_sources_scope_expiry_and_bounds(self):
        a, b = (1, 2, 3, True), (1, 2, 4, True)
        self.web.save_sources(a, {8}, {"W1": "https://example.com"})
        self.assertTrue(self.web.saved_sources(a, 8))
        self.assertFalse(self.web.saved_sources(b, 8))
        self.web.references[a][0]["created"] = time.monotonic() - 1801
        self.assertFalse(self.web.saved_sources(a, 8))
        for n in range(201):
            self.web.save_sources((1, 2, n, False), {n}, {})
        self.assertEqual(len(self.web.references), 200)
        self.web.clear()
        self.assertFalse(self.web.references)

    def test_web_labels_hidden_until_asked(self):
        refs = {"W1": "https://example.com/match"}
        text = "ชนะ 2–1 [W1] [อ่าน](https://example.com/match)"
        clean = render_answer(text, {}, web_sources=refs)
        self.assertNotIn("W1", clean)
        self.assertNotIn("https:", clean)
        self.assertIn(refs["W1"], render_answer(text, {}, True, web_sources=refs))

    def test_web_context_has_no_discord_history_or_person_memories(self):
        self.store.set_channel(1, 2, readable=1, respond=1)
        self.store.put_message(1, 1, 2, 3, "PRIVATE_OLD_ANIME", time.time())
        self.store.remember(1, 2, 3, "PRIVATE_PERSON_FACT")
        context = build_context(
            self.store,
            self.config,
            guild=1,
            channel=2,
            user=3,
            allowed=[],
            question="สกอร์บอลแมนยูล่าสุด",
            tone=1,
            web_pages=[WebPage("Match", "https://example.com", "October 28 match result", "now")],
        )
        # Person memories must not contaminate fresh public lookups either.
        self.assertNotIn("PRIVATE_OLD_ANIME", str(context.messages))
        self.assertNotIn("PRIVATE_PERSON_FACT", str(context.messages))
        self.assertTrue(context.web_sources)

    def test_page_fits_partly_used_quota_with_long_metadata_and_thai(self):
        url = "https://www.toolszone.net/th/tools/base64-encoder-decoder"
        config = replace(self.config, tpm=4500, output_tokens=768)
        context = build_context(
            self.store, config, guild=1, channel=2, user=3, allowed=[],
            question=url, tone=4, web_kind="page",
            web_pages=[WebPage("หัวข้อ" * 100, url + "?q=" + "a" * 500,
                               'เนื้อหา "ทดสอบ" ' * 500, "now")],
        )
        self.assertTrue(context.web_sources)
        self.assertIn("เนื้อหา", str(context.messages))
        estimate = sum(len(m["content"].encode()) + 32 for m in context.messages) + config.output_tokens
        self.assertLessEqual(estimate, config.tpm)


class WebAppTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixtures.AppTests.asyncSetUp
    asyncTearDown = fixtures.AppTests.asyncTearDown
    job = fixtures.AppTests.job

    def enable(self):
        self.bot.config = replace(self.bot.config, mcp_enabled=True)
        self.bot.web = WebTools(self.bot.config, self.bot.store)
        self.bot.store.set_meta("mcp:10:20", "hybrid")
        self.bot.web.call = AsyncMock(
            return_value=[
                WebPage(
                    "Match result",
                    "https://example.com/match",
                    "28 October: fixture, not a completed match",
                    "now",
                )
            ]
        )
        self.message.content = "สกอร์บอลแมนยูล่าสุด"
        self.message.reply.return_value = SimpleNamespace(id=880)
        self.bot.provider.chat.return_value = Answer("ยังเป็นโปรแกรมแข่ง ไม่ใช่ผลจบเกม [W1]", 100)

    async def test_latest_score_uses_real_route_not_legacy_refusal_or_anime(self):
        self.enable()
        self.bot.history.hydrate = AsyncMock()
        self.bot.store.put_message(2, 10, 20, 30, "PRIVATE_OLD_ANIME", time.time())
        await self.bot.process(self.job())
        self.assertEqual(self.bot.web.call.await_count, 1)
        self.assertIn("Manchester United", self.bot.web.call.call_args_list[0].args[1]["query"])
        self.bot.history.hydrate.assert_not_awaited()
        self.assertNotIn("PRIVATE_OLD_ANIME", str(self.bot.provider.chat.call_args))
        self.assertNotIn("W1", self.message.reply.call_args.args[0])
        self.assertTrue(self.bot.web.saved_sources(scope(self.message), 880))

    async def test_private_derived_query_requires_modal_no_egress(self):
        self.enable()
        self.message.content = "ค้นเว็บเรื่องที่คุยในห้องเมื่อกี้"
        await self.bot.process(self.job())
        self.bot.web.call.assert_not_awaited()
        self.bot.provider.chat.assert_not_awaited()
        view = self.message.reply.call_args.kwargs["view"]
        self.assertEqual(view.query, "")

    async def test_search_failure_does_not_call_ai_or_invent_score(self):
        self.enable()
        self.bot.web.call.side_effect = ToolError("ค้นเว็บไม่สำเร็จ")
        with self.assertRaises(ToolError):
            await self.bot.process(self.job())
        self.bot.provider.chat.assert_not_awaited()

    async def test_revision_change_during_search_blocks_page_fetch_and_delivery(self):
        self.enable()

        async def changed(*args, **kwargs):
            self.bot.invalidate()
            return [WebPage("x", "https://example.com", "x", "now")]

        self.bot.web.call.side_effect = changed
        await self.bot.process(self.job())
        self.assertEqual(self.bot.web.call.await_count, 1)
        self.message.reply.assert_not_awaited()

    async def test_interjection_never_triggers_external_search(self):
        self.enable()
        self.assertFalse(
            await self.bot.process_web(
                Job(self.message, "interject", self.message.content, 0), self.message, [20], 1
            )
        )
        self.bot.web.call.assert_not_awaited()

    async def test_disabled_channel_never_egresses(self):
        self.enable()
        self.bot.store.set_meta("mcp:10:20", "off")
        await self.bot.process(self.job())
        self.bot.web.call.assert_not_awaited()

    async def test_optout_keeps_no_web_evidence(self):
        self.enable()
        self.bot.store.set_user(10, 30, optout=1)
        await self.bot.process(self.job())
        self.assertFalse(self.bot.web.references)
        self.assertFalse(self.bot.conversation.sessions)

    async def test_capabilities_are_from_runtime_config(self):
        self.enable()
        self.message.content = "เอ็งมี mcp dugduggo อยู่นิ"
        await self.bot.process(self.job())
        reply = self.message.reply.call_args.args[0]
        self.assertIn("Tavily", reply)
        self.assertIn("DuckDuckGo", reply)
        self.assertIn("อ่านหน้าเว็บ HTTPS", reply)
        self.assertNotIn("YouTube Transcript", reply)
        self.bot.provider.chat.assert_not_awaited()

    async def test_link_capability_question_is_accurate_without_external_call(self):
        self.enable()
        self.message.content = "อ่านลิงก์ได้ไหม"
        await self.bot.process(self.job())
        reply = self.message.reply.call_args.args[0]
        self.assertIn("อ่านหน้าเว็บ HTTPS", reply)
        self.assertIn("JavaScript", reply)
        self.bot.web.call.assert_not_awaited()
        self.bot.provider.chat.assert_not_awaited()

    async def test_source_followup_retrieves_web_links_without_ai(self):
        self.enable()
        await self.bot.process(self.job())
        self.bot.provider.chat.reset_mock()
        self.message.content = "เอามาจากไหน"
        await self.bot.process(self.job())
        self.assertIn("https://example.com/match", self.message.reply.call_args.args[0])
        self.bot.provider.chat.assert_not_awaited()

    async def test_button_wrong_owner_expiry_and_duplicate_submit(self):
        self.enable()
        view = SearchView(self.bot, self.message, "latest score", "Manchester United latest score")
        interaction = SimpleNamespace(
            user=SimpleNamespace(id=999),
            guild=self.guild,
            channel=self.channel,
            guild_id=10,
            channel_id=20,
            edit_original_response=AsyncMock(),
        )
        await view.submit(interaction, "public query")
        self.assertFalse(view.consumed)
        interaction.user.id = 30
        view.deadline = 0
        await view.submit(interaction, "public query")
        self.assertFalse(view.consumed)
        view.deadline = time.monotonic() + 100
        self.bot.enqueue = AsyncMock(return_value="queued")
        await view.submit(interaction, "public query")
        await view.submit(interaction, "public query")
        self.bot.enqueue.assert_awaited_once()
        self.assertEqual(self.bot.enqueue.call_args.kwargs["confirmed_query"], "public query")

    async def test_button_public_output_and_private_status_are_separate(self):
        interaction = SimpleNamespace(channel=self.channel, followup=SimpleNamespace(send=AsyncMock()))
        public = SearchMessage(interaction, self.message, "public query", False)
        await public.reply("answer")
        self.channel.send.assert_awaited_once()
        interaction.followup.send.assert_not_awaited()
        private = SearchMessage(interaction, self.message, "public query", True)
        await private.reply("answer")
        self.assertTrue(interaction.followup.send.call_args.kwargs["ephemeral"])

    async def test_public_planner_only_sees_current_utterance(self):
        from bot.quota import Quota

        session = Session(
            Response(
                data={
                    "choices": [
                        {"message": {"content": '{"action":"search","value":"Manchester United result"}'}}
                    ],
                    "usage": {"total_tokens": 100},
                }
            )
        )
        provider = Provider(self.bot.config, Quota(self.bot.store, self.bot.config), session)
        plan = await provider.plan_public("Manchester United score today")
        self.assertEqual(plan[0], "search")
        payload = session.calls[0][1]["json"]
        self.assertEqual(payload["messages"][-1]["content"], "Manchester United score today")
        self.assertEqual(len(payload["messages"]), 2)
        session.response = Response(
            data={"choices": [{"message": {"content": '{"action":"shell","value":"rm"}'}}]}
        )
        with self.assertRaises(ProviderError):
            await provider.plan_public("hello")


class WebTransportTests(unittest.IsolatedAsyncioTestCase):
    async def test_response_size_and_encoding_guard_precedes_sdk_parsing(self):
        import httpx2

        from bot.web_tools import LimitedTransport

        for headers, body in [
            ({"content-length": "999999"}, b"x"),
            ({"content-encoding": "gzip"}, b"x"),
            ({}, b"A" * 131073),
        ]:
            async with httpx2.AsyncClient(
                transport=LimitedTransport(
                    httpx2.MockTransport(lambda req: httpx2.Response(200, headers=headers, content=body))
                )
            ) as client:
                with self.assertRaises(ToolError):
                    await client.get("https://example.com")

    async def test_tool_allowlist_and_forged_arguments_never_reach_network(self):
        with tempfile.TemporaryDirectory() as folder:
            store = Store(Path(folder) / "db")
            web = WebTools(Config("fake", "fake", mcp_enabled=True), store)
            for name, args in [
                ("shell", {"cmd": "x"}),
                ("search", {"query": "x", "extra": "forged"}),
                ("fetch_content", {"url": "https://127.0.0.1"}),
                ("search", {"query": "password=private"}),
            ]:
                with self.assertRaises(ToolError):
                    await web.call(name, args, guild=1, user=1)
            self.assertFalse(store.meta("mcp_usage"))
            store.close()

    async def test_health_without_search_pin_reports_unverified(self):
        from unittest.mock import patch

        listing = SimpleNamespace(tools=[SimpleNamespace(name=name, input_schema={}) for name in TOOL_FIELDS])
        client = SimpleNamespace(list_tools=AsyncMock(return_value=listing))
        manager = AsyncMock()
        manager.__aenter__.return_value = client
        with tempfile.TemporaryDirectory() as folder:
            store = Store(Path(folder) / "db")
            web = WebTools(Config("fake", "fake", mcp_enabled=True), store)
            with patch("bot.web_tools.Client", return_value=manager):
                self.assertEqual((await web.check_health())["status"], "unverified")
            self.assertIsNone(store.meta("mcp_web_schema_usb_search_fetch_v2"))
            store.close()

    async def test_schema_drift_blocks_execution_and_circuit_breaks(self):
        from unittest.mock import patch

        listing = SimpleNamespace(
            tools=[
                SimpleNamespace(
                    name=name,
                    input_schema={
                        "type": "object",
                        "properties": {k: {"type": v} for k, v in fields.items()},
                        "required": ["query" if name in {"search", "rss_news"} else "url"],
                    },
                )
                for name, fields in TOOL_FIELDS.items()
            ]
        )
        client = SimpleNamespace(
            list_tools=AsyncMock(return_value=listing), session=SimpleNamespace(call_tool=AsyncMock())
        )
        manager = AsyncMock()
        manager.__aenter__.return_value = client
        with tempfile.TemporaryDirectory() as folder:
            store = Store(Path(folder) / "db")
            web = WebTools(Config("fake", "fake", mcp_enabled=True), store)
            store.set_meta("mcp_web_schema_usb_search_fetch_v2", "reviewed-old-schema")
            with patch("bot.web_tools.Client", return_value=manager):
                for _ in range(3):
                    with self.assertRaises(ToolError):
                        await web.call("search", {"query": "public query"}, guild=1, user=1)
            client.session.call_tool.assert_not_awaited()
            self.assertGreater(web.pause_until, time.monotonic())
            store.close()


class SDKErrorTests(unittest.TestCase):
    def test_only_single_safe_tool_error_is_unwrapped(self):
        from bot.web_tools import safe_tool_error

        original = ToolError("safe transcript notice")
        group = ExceptionGroup("sdk", [ExceptionGroup("task", [original])])
        self.assertIs(safe_tool_error(group), original)
        self.assertIsNone(safe_tool_error(ExceptionGroup("sdk", [ValueError("raw body")])))
