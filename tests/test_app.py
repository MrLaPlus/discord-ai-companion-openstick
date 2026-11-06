import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import discord

from bot.app import Companion, Job
from bot.commands import SlashMessage, register
from bot.config import Config
from bot.provider import Answer


class AppTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.bot = Companion(
            Config("fake-discord", "fake-ai", database=Path(self.temp.name) / "db", auto_history=False)
        )
        self.bot._connection.user = SimpleNamespace(id=99)
        self.channel = MagicMock(spec=discord.TextChannel)
        self.channel.id = 20
        self.channel.name = "bot"
        self.guild = SimpleNamespace(id=10, get_channel=lambda cid: self.channel if cid == 20 else None)
        self.message = MagicMock(spec=discord.Message)
        self.message.id, self.message.guild, self.message.channel = 1, self.guild, self.channel
        self.message.author = SimpleNamespace(id=30, bot=False)
        self.message.content = "เล่นเกมแมว"
        self.message.created_at = datetime.now(timezone.utc)
        self.message.reply = AsyncMock()
        self.channel.fetch_message = AsyncMock(return_value=self.message)
        self.bot.provider = SimpleNamespace(chat=AsyncMock(return_value=Answer("จำได้ [S1]", 100)))
        self.bot.access_snapshot = AsyncMock(return_value=(self.channel, self.message.author, [20], 1))
        self.bot.store.set_channel(10, 20, readable=1, respond=1)
        self.bot.store.put_message(1, 10, 20, 30, self.message.content, self.message.created_at.timestamp())

    async def asyncTearDown(self):
        await self.bot.close()
        self.temp.cleanup()

    def job(self):
        return Job(self.message, "direct", self.message.content, self.bot.revision)

    async def test_board_question_requires_admin_and_sends_metrics_only_in_dm(self):
        self.message.content = 'บอร์ดเป็นไงบ้าง'
        self.message.author.guild_permissions = SimpleNamespace(manage_guild=False)
        self.message.author.send = AsyncMock()
        with patch('bot.board_status.board_assessment', new=AsyncMock(return_value='PRIVATE_METRICS')) as measure:
            await self.bot.process(self.job())
            measure.assert_not_awaited()
            self.message.author.send.assert_not_awaited()
            self.message.author.guild_permissions.manage_guild = True
            await self.bot.process(self.job())
            self.message.author.send.assert_awaited_once()
            self.assertIn('PRIVATE_METRICS', self.message.author.send.await_args.args[0])
            self.assertNotIn('PRIVATE_METRICS', self.message.reply.await_args.args[0])

    async def test_board_question_permission_revoked_before_dm(self):
        self.message.content = 'บอร์ดเป็นไงบ้าง'
        self.message.author.guild_permissions = SimpleNamespace(manage_guild=True)
        self.message.author.send = AsyncMock()
        revoked = SimpleNamespace(id=30, guild_permissions=SimpleNamespace(manage_guild=False))
        self.bot.access_snapshot.side_effect = [(self.channel, self.message.author, [20], 1), (self.channel, revoked, [20], 1)]
        with patch('bot.board_status.board_assessment', new=AsyncMock(return_value='PRIVATE_METRICS')):
            await self.bot.process(self.job())
        self.message.author.send.assert_not_awaited()

    async def test_discord_progress_slash_setting_is_private_and_per_user(self):
        register(self.bot)
        command = self.bot.tree.get_command('progress')
        self.assertIsNotNone(command)
        self.bot.in_scope = lambda guild: True
        interaction = SimpleNamespace(guild=self.guild, channel=self.channel, user=self.message.author,
            response=SimpleNamespace(is_done=lambda: False, send_message=AsyncMock()))
        await command.callback(interaction, enabled=False)
        self.assertEqual(self.bot.store.user(10, 30)['progress_enabled'], 0)
        self.assertEqual(self.bot.store.user(10, 31)['progress_enabled'], 1)
        self.assertTrue(interaction.response.send_message.await_args.kwargs['ephemeral'])
        await command.callback(interaction, enabled=True)
        self.assertEqual(self.bot.store.user(10, 30)['progress_enabled'], 1)

    async def test_natural_schedule_edit_routes_through_discord(self):
        from bot.schedule import create, parse_request
        req = parse_request('เตือนทุกวัน 23:55 กินยา', self.bot.config.timezone)
        create(self.bot.store, 'discord', 10, 20, 30, req)
        self.message.content = 'เลื่อนเตือนกินยาเป็น 23:56'
        await self.bot.process(self.job())
        self.assertIn('เลื่อนแล้ว', self.message.reply.await_args.args[0])
        self.bot.provider.chat.assert_not_awaited()

    async def test_real_pipeline_builds_scoped_prompt_and_sends_with_mentions_disabled(self):
        self.bot.store.put_message(2, 10, 999, 30, "PRIVATE_SECRET", self.message.created_at.timestamp())
        await self.bot.process(self.job())
        self.bot.provider.chat.assert_awaited_once()
        self.assertNotIn("PRIVATE_SECRET", str(self.bot.provider.chat.call_args))
        self.message.reply.assert_awaited_once()
        kwargs = self.message.reply.call_args.kwargs
        self.assertFalse(kwargs["mention_author"])
        self.assertFalse(kwargs["allowed_mentions"].everyone)

    async def test_capabilities_respect_discord_web_setting_without_calling_ai(self):
        self.message.content = "บอททำอะไรได้บ้าง"
        await self.bot.process(self.job())
        reply = self.message.reply.await_args.args[0]
        self.assertIn("ยังไม่ได้เปิดใช้", reply)
        self.assertIn("ยังไม่รองรับอ่านรูป", reply)
        self.assertNotIn("/tone 0–5", reply)
        self.bot.provider.chat.assert_not_awaited()
        self.assertEqual(self.bot.store.db.execute("SELECT count(*) FROM scheduled_jobs").fetchone()[0], 0)

    async def test_capabilities_distinguish_tool_permissions_and_memory_room(self):
        self.bot.config = replace(self.bot.config, mcp_enabled=True)
        self.bot.store.set_meta("mcp:10:20", "hybrid")
        self.bot.store.set_meta("tools:10:20:search", {"enabled": False})
        self.bot.store.set_channel(10, 20, readable=0, respond=1)
        self.message.content = "มีฟีเจอร์อะไรบ้าง"
        await self.bot.process(self.job())
        reply = self.message.reply.await_args.args[0]
        self.assertIn("ค้นเว็บ: ยังไม่ได้เปิดใช้หรือคุณไม่มีสิทธิ์", reply)
        self.assertIn("อ่านหน้าเว็บ HTTPS สาธารณะ", reply)
        self.assertIn("ให้แอดมินเปิดอ่านห้องก่อน", reply)
        self.bot.provider.chat.assert_not_awaited()

    async def test_scheduled_news_refuses_when_search_permission_denied(self):
        self.bot.config = replace(self.bot.config, mcp_enabled=True)
        self.bot.store.set_meta("mcp:10:20", "hybrid")
        self.bot.store.set_meta("tools:10:20:search", {"enabled": False})
        self.message.content = "รายงานข่าว AI ทุกวัน 07:00"
        await self.bot.process(self.job())
        self.assertIn("ไม่มีสิทธิ์", self.message.reply.await_args.args[0])
        self.assertEqual(self.bot.store.db.execute("SELECT count(*) FROM scheduled_jobs").fetchone()[0], 0)

    async def test_ai_prompt_reflects_individual_tool_permissions(self):
        self.bot.config = replace(self.bot.config, mcp_enabled=True)
        self.bot.store.set_meta("mcp:10:20", "hybrid")
        self.bot.store.set_meta("tools:10:20:search", {"enabled": False})
        self.message.content = "เล่าเรื่องแมว"
        self.bot.store.put_message(1, 10, 20, 30, self.message.content, self.message.created_at.timestamp())
        await self.bot.process(self.job())
        prompt = self.bot.provider.chat.await_args.args[0][0]["content"]
        self.assertIn("Search is unavailable here", prompt)
        self.assertIn("Public HTTPS reading is configured", prompt)
        self.assertIn("Never claim to have searched", prompt)

    async def test_help_command_uses_current_permissions(self):
        register(self.bot)
        interaction = MagicMock(spec=discord.Interaction)
        interaction.guild = self.guild
        interaction.channel = self.channel
        interaction.user = self.message.author
        interaction.response.is_done.return_value = False
        interaction.response.send_message = AsyncMock()
        await self.bot.tree.get_command("help").callback(interaction)
        text = interaction.response.send_message.await_args.args[0]
        self.assertIn("ยังไม่ได้เปิดใช้หรือคุณไม่มีสิทธิ์", text)
        self.assertIn("ตัวตั้งเวลาไม่พร้อม", text)
        self.assertTrue(interaction.response.send_message.await_args.kwargs["ephemeral"])

    async def test_clock_and_reminder_messages_do_not_call_ai(self):
        self.message.content = "ตอนนี้กี่โมง"
        await self.bot.process(self.job())
        self.assertIn("Asia/Bangkok", self.message.reply.await_args.args[0])
        self.message.reply.reset_mock()
        self.message.content = "เตือนทุกวัน 07:00 กินข้าว"
        await self.bot.process(self.job())
        self.assertIn("ตั้งเวลาแล้ว", self.message.reply.await_args.args[0])
        self.bot.provider.chat.assert_not_awaited()

    async def test_privacy_change_while_provider_runs_suppresses_delivery(self):
        async def changed(messages):
            self.bot.store.set_user(10, 30, optout=1)
            self.bot.store.erase_user(10, 30)
            self.bot.invalidate()
            return Answer("ข้อมูลเดิมห้ามส่ง", 100)

        self.bot.provider.chat.side_effect = changed
        await self.bot.process(self.job())
        self.message.reply.assert_not_awaited()

    async def test_permission_change_while_provider_runs_suppresses_delivery(self):
        self.bot.access_snapshot.side_effect = [
            (self.channel, self.message.author, [20], 1),
            (self.channel, self.message.author, [20], 1),
            (self.channel, self.message.author, [], 1),
        ]
        await self.bot.process(self.job())
        self.message.reply.assert_not_awaited()

    async def test_offline_source_edit_is_blocked_before_provider(self):
        self.bot.store.put_message(2, 10, 20, 30, "เก่า", self.message.created_at.timestamp())
        edited = MagicMock(spec=discord.Message)
        edited.content = "แก้แล้ว"
        self.channel.fetch_message.side_effect = lambda mid: self.message if mid == 1 else edited
        await self.bot.process(self.job())
        self.bot.provider.chat.assert_not_awaited()
        self.message.reply.assert_not_awaited()

    async def test_queued_job_invalidated_before_context_build(self):
        job = self.job()
        self.bot.invalidate()
        await self.bot.process(job)
        self.bot.provider.chat.assert_not_awaited()

    async def test_unrelated_message_edit_does_not_cancel_queued_question(self):
        job = self.job()
        await self.bot.on_raw_message_edit(
            SimpleNamespace(message_id=98765, channel_id=999, guild_id=10, data={"content": "unrelated"})
        )
        self.assertEqual(self.bot.revision, job.revision)
        await self.bot.process(job)
        self.message.reply.assert_awaited_once()

    async def test_private_queue_status_edit_does_not_invalidate_interaction_job(self):
        self.bot.store.claim(777)
        before = self.bot.revision
        await self.bot.on_raw_message_edit(SimpleNamespace(message_id=777, data={"content": "เข้าคิวแล้ว"}))
        self.assertEqual(self.bot.revision, before)

    async def test_cancelled_private_job_updates_original_status(self):
        interaction = MagicMock(spec=discord.Interaction)
        interaction.id, interaction.guild, interaction.channel = 777, self.guild, self.channel
        interaction.user = self.message.author
        interaction.created_at = datetime.now(timezone.utc)
        message = SlashMessage(interaction, "ก่อนบอทเข้าคุยอะไร")
        job = Job(message, "direct", message.content, self.bot.revision)
        self.bot.invalidate()
        await self.bot.process(job)
        interaction.edit_original_response.assert_awaited_once()
        self.assertIn("ถูกหยุด", interaction.edit_original_response.call_args.kwargs["content"])

    async def test_bot_and_webhook_messages_do_not_trigger_or_store(self):
        self.message.author.bot = True
        await self.bot.on_message(self.message)
        self.assertTrue(self.bot.queue.empty())

    async def test_enqueue_is_bounded_and_deduplicated(self):
        self.assertIn("เข้าคิว", await self.bot.enqueue(self.message, "direct", "hi"))
        self.bot.user_times.clear()
        self.assertIn("ประมวลผลแล้ว", await self.bot.enqueue(self.message, "direct", "hi"))
        self.assertEqual(self.bot.queue.qsize(), 1)
        self.bot.queue.get_nowait()
        self.bot.queue.task_done()

    async def test_guild_pause_blocks_new_channel(self):
        self.bot.store.set_meta("pause:10", True)
        self.bot.store.set_channel(10, 25, readable=1, respond=1)
        self.assertEqual(self.bot.settings(10, 25)["paused"], 1)

    async def test_slash_commands_serialize_without_discord_login(self):
        register(self.bot)
        names = {command.name for command in self.bot.tree.get_commands()}
        self.assertTrue({"settings", "memory", "privacy", "catchup", "status", "history"} <= names)
        for command in self.bot.tree.get_commands():
            self.assertIn("name", command.to_dict(self.bot.tree))

    async def test_two_guild_scope_and_separate_command_sync(self):
        self.bot.config = replace(self.bot.config, guild_id=10, additional_guild_ids=(20,))
        self.assertTrue(self.bot.in_scope(10))
        self.assertTrue(self.bot.in_scope(20))
        self.assertFalse(self.bot.in_scope(30))
        with (
            patch.object(self.bot.tree, "copy_global_to") as copy,
            patch.object(self.bot.tree, "sync", new_callable=AsyncMock) as sync,
        ):
            await self.bot.sync_scoped_commands()
        self.assertEqual([c.kwargs["guild"].id for c in copy.call_args_list], [10, 20])
        self.assertEqual([c.kwargs["guild"].id for c in sync.call_args_list], [10, 20])

    async def test_second_guild_room_does_not_change_first_guild_or_privacy(self):
        from bot.policy import trigger

        original = self.bot.settings(10, 20)
        self.bot.store.set_channel(20, 21, readable=1, respond=1, bot_room=1)
        self.bot.store.set_user(20, 30, optout=1)
        room = self.bot.settings(20, 21)
        self.assertEqual(self.bot.settings(10, 20), original)
        self.assertTrue(self.bot.store.user(20, 30)["optout"])
        self.assertFalse(self.bot.store.user(10, 30)["optout"])
        self.assertEqual(
            trigger(room, mention=False, reply=False, now=1000, timezone="Asia/Bangkok", interjections=[]),
            "room",
        )

    async def test_single_and_unrestricted_scope_keep_existing_sync_behavior(self):
        for primary, expected in ((10, [10]), (None, [])):
            self.bot.config = replace(self.bot.config, guild_id=primary, additional_guild_ids=())
            with (
                patch.object(self.bot.tree, "copy_global_to"),
                patch.object(self.bot.tree, "sync", new_callable=AsyncMock) as sync,
            ):
                await self.bot.sync_scoped_commands()
            calls = [c.kwargs["guild"].id for c in sync.call_args_list if "guild" in c.kwargs]
            self.assertEqual(calls, expected)
            self.assertEqual(sync.await_count, 1)

    async def test_admin_setting_checks_permission_at_runtime(self):
        register(self.bot)
        interaction = MagicMock(spec=discord.Interaction)
        interaction.guild, interaction.channel = self.guild, self.channel
        interaction.user.guild_permissions.manage_guild = False
        command = self.bot.tree.get_command("settings").get_command("channel")
        with self.assertRaises(discord.app_commands.CheckFailure):
            await command.callback(interaction, True, True, True)
        self.assertEqual(self.bot.settings(10, 20)["bot_room"], 0)

    async def test_catchup_does_not_fetch_interaction_id_as_message(self):
        interaction = MagicMock(spec=discord.Interaction)
        interaction.id, interaction.guild, interaction.channel = 456, self.guild, self.channel
        interaction.user = self.message.author
        interaction.created_at = datetime.now(timezone.utc)
        interaction.followup.send = AsyncMock()
        message = SlashMessage(interaction, "สรุปเกมแมว")
        await self.bot.process(Job(message, "direct", message.content, self.bot.revision, 0))
        interaction.followup.send.assert_awaited_once()
        self.assertTrue(interaction.followup.send.call_args.kwargs["ephemeral"])
        self.assertNotIn(456, [c.args[0] for c in self.channel.fetch_message.call_args_list])

    async def test_import_honors_optout_bots_and_older_cursor(self):
        self.bot.store.erase_channel(10, 20)
        self.bot.store.set_user(10, 31, optout=1)
        interaction = SimpleNamespace(edit_original_response=AsyncMock())
        self.channel.guild = self.guild
        normal = self.message
        opted = SimpleNamespace(
            id=2,
            author=SimpleNamespace(id=31, bot=False),
            webhook_id=None,
            content="不要保存",
            created_at=normal.created_at,
        )
        automated = SimpleNamespace(
            id=3,
            author=SimpleNamespace(id=99, bot=True),
            webhook_id=None,
            content="bot message",
            created_at=normal.created_at,
        )
        normal.webhook_id = None

        async def messages(**kwargs):
            self.assertEqual(kwargs["before"].id, 900)
            for item in (normal, opted, automated):
                yield item

        self.channel.history.side_effect = messages
        await self.bot.import_history(interaction, self.channel, 100, 7, 900)
        self.assertEqual([r["id"] for r in self.bot.store.recent(10, [20])], [1])
        self.assertFalse(self.bot.imports)

    async def test_clear_interrupts_inflight_import(self):
        self.message.webhook_id = None
        self.channel.guild = self.guild
        interaction = SimpleNamespace(edit_original_response=AsyncMock())

        async def messages(**kwargs):
            yield self.message
            self.bot.store.erase_channel(10, 20)
            self.bot.invalidate()
            yield self.message

        self.channel.history.side_effect = messages
        await self.bot.import_history(interaction, self.channel, 100, 7)
        self.assertEqual(self.bot.store.recent(10, [20]), [])
        self.assertIn("หยุดนำเข้า", interaction.edit_original_response.call_args.kwargs["content"])

    async def test_long_answer_is_sent_in_full_without_volunteered_sources(self):
        text = "ข้อความยาว 😺\n" * 500
        self.bot.provider.chat.return_value = Answer(text + " [S1]", 700)
        self.message.reply.return_value = SimpleNamespace(id=1000)
        self.channel.send = AsyncMock(return_value=SimpleNamespace(id=1001))
        await self.bot.process(self.job())
        parts = [self.message.reply.call_args.args[0]] + [c.args[0] for c in self.channel.send.call_args_list]
        self.assertEqual("".join(parts).strip(), text.strip())
        self.assertGreater(len(parts), 2)
        self.assertTrue(all(len(p.encode("utf-16-le")) // 2 <= 1900 for p in parts))
        self.assertNotIn("[S1]", "".join(parts))

    async def test_where_from_followup_uses_saved_refs_without_another_ai_call(self):
        self.message.reply.return_value = SimpleNamespace(id=1000)
        await self.bot.process(self.job())
        self.assertNotIn("discord.com", self.message.reply.call_args.args[0])
        original = SimpleNamespace(content=self.message.content)
        self.message.id, self.message.content = 2, "เอามาจากไหนครับ"
        self.message.reference = SimpleNamespace(message_id=1000)
        self.channel.fetch_message.side_effect = lambda mid: self.message if mid == 2 else original
        await self.bot.process(self.job())
        self.assertIn("https://discord.com/channels/10/20/1", self.message.reply.call_args.args[0])
        self.bot.provider.chat.assert_awaited_once()

    async def test_followup_pipeline_can_use_own_answer_and_erases_it_on_privacy_change(self):
        self.message.reply.return_value = SimpleNamespace(id=1000)
        self.bot.provider.chat.return_value = Answer("1. เล่นเกมแมว 2. ปลูกต้นไม้", 100)
        await self.bot.process(self.job())
        original = SimpleNamespace(content=self.message.content)
        self.message.id, self.message.content = 2, "อันที่สองล่ะ"
        self.channel.fetch_message.side_effect = lambda mid: self.message if mid == 2 else original
        await self.bot.process(self.job())
        self.assertIn("ปลูกต้นไม้", str(self.bot.provider.chat.call_args))
        self.assertTrue(self.bot.conversation.sessions)
        self.bot.store.set_user(10, 30, optout=1)
        self.bot.invalidate()
        self.assertFalse(self.bot.conversation.sessions)
        self.assertFalse(self.bot.history.sessions)

    async def test_private_answer_turn_is_excluded_from_normal_channel_followup(self):
        self.bot.conversation.add((10, 20, 30, True), "private", "PRIVATE_ANSWER_SECRET", {}, {}, [20], {500})
        await self.bot.process(self.job())
        self.assertNotIn("PRIVATE_ANSWER_SECRET", str(self.bot.provider.chat.call_args))

    async def test_generated_reply_edit_clears_derived_ram_turn(self):
        self.message.reply.return_value = SimpleNamespace(id=1000)
        await self.bot.process(self.job())
        self.assertTrue(self.bot.conversation.sessions)
        await self.bot.on_raw_message_edit(SimpleNamespace(message_id=1000, data={"content": "edited"}))
        self.assertFalse(self.bot.conversation.sessions)

    async def test_optout_requester_does_not_get_ram_retention(self):
        self.bot.store.set_user(10, 30, optout=1)
        await self.bot.process(self.job())
        self.assertFalse(self.bot.conversation.sessions)
        self.assertFalse(self.bot.suggestion_views)

    async def test_more_without_search_session_explains_without_ai(self):
        self.message.content = "หาเพิ่ม"
        self.bot.store.put_message(1, 10, 20, 30, self.message.content, self.message.created_at.timestamp())
        await self.bot.process(self.job())
        self.bot.provider.chat.assert_not_awaited()
        self.assertIn("ยังไม่มีการค้น", self.message.reply.call_args.args[0])

    async def test_state_change_during_private_status_cannot_restore_ram_or_search(self):
        async def changed(message, text):
            self.bot.invalidate()

        self.bot.private_status = changed
        await self.bot.process(self.job())
        self.assertFalse(self.bot.conversation.sessions)
        self.assertFalse(self.bot.history.sessions)

    async def test_latest_score_typo_does_not_use_unrelated_anime_context_or_ai(self):
        self.message.content = "สกอบอนแมนยูล่าสุด"
        self.bot.store.put_message(1, 10, 20, 30, self.message.content, self.message.created_at.timestamp())
        self.bot.store.put_message(
            2, 10, 20, 31, "คุยเรื่องอนิเมะที่ไม่เกี่ยวกับฟุตบอล", self.message.created_at.timestamp()
        )
        self.bot.history.hydrate = AsyncMock()
        await self.bot.process(self.job())
        self.bot.provider.chat.assert_not_awaited()
        self.bot.history.hydrate.assert_not_awaited()
        reply = self.message.reply.call_args.args[0]
        self.assertIn("สกอร์บอลแมนยู", reply)
        self.assertIn("ยังเช็กผลบอลสดไม่ได้", reply)
        self.assertNotIn("อนิเมะ", reply)

    async def test_renderer_guard_blocks_internal_output_even_from_an_adapter_double(self):
        self.bot.provider.chat.return_value = Answer("[Thinking Process]\nsynthetic private trace", 100)
        await self.bot.process(self.job())
        reply = self.message.reply.call_args.args[0]
        self.assertNotIn("Thinking Process", reply)
        self.assertNotIn("synthetic private", reply)
        self.assertNotIn("synthetic private", str(self.bot.conversation.sessions))

    async def test_clean_partial_answer_is_preserved_with_continuation_notice(self):
        self.bot.provider.chat.return_value = Answer("ข้อความคำตอบที่ยังไม่จบ", 100, True)
        await self.bot.process(self.job())
        reply = self.message.reply.call_args.args[0]
        self.assertIn("ข้อความคำตอบที่ยังไม่จบ", reply)
        self.assertIn("อาจยังไม่ครบ", reply)
