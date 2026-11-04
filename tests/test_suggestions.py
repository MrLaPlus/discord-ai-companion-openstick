import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord

from bot.app import Companion, Job
from bot.config import Config
from bot.suggestions import MemorySuggestion, preference


class SuggestionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.bot = Companion(Config("fake", "fake", database=Path(self.temp.name) / "db"))
        self.channel = MagicMock(spec=discord.TextChannel)
        self.channel.id = 20
        self.guild = SimpleNamespace(id=10)
        self.message = MagicMock(spec=discord.Message)
        self.message.guild, self.message.channel = self.guild, self.channel
        self.message.id, self.message.author = 1, SimpleNamespace(id=30)
        self.message.content = "ผมชอบเกมแมว"
        self.channel.fetch_message = AsyncMock(return_value=self.message)
        self.message.reply = AsyncMock(return_value=SimpleNamespace(edit=AsyncMock()))
        self.bot.store.set_channel(10, 20, readable=1, respond=1)
        self.bot.access_snapshot = AsyncMock(return_value=(self.channel, self.message.author, [20], 1))
        self.interaction = MagicMock(spec=discord.Interaction)
        self.interaction.guild_id, self.interaction.channel_id = 10, 20
        self.interaction.user = SimpleNamespace(id=30)
        self.interaction.message = SimpleNamespace(edit=AsyncMock())
        self.interaction.response = SimpleNamespace(send_message=AsyncMock(), defer=AsyncMock())
        self.interaction.followup = SimpleNamespace(send=AsyncMock())

    async def asyncTearDown(self):
        await self.bot.close()
        self.temp.cleanup()

    async def offer(self):
        await self.bot.suggest_memory(Job(self.message, "direct", self.message.content, self.bot.revision))
        return next(iter(self.bot.suggestion_views))

    def test_only_own_explicit_preferences_are_candidates(self):
        self.assertEqual(preference("ผมชอบเกมแมว"), "ผมชอบเกมแมว")
        self.assertEqual(preference("I prefer cats"), "I prefer cats")
        for text in [
            "เขาชอบแมว",
            "ผมชอบแมวไหม",
            "ผมชอบแมว ล้อเล่น",
            "ผมชอบ token abc",
            "ผมชอบรหัส123456",
            '"ผมชอบแมว"',
        ]:
            self.assertIsNone(preference(text))

    async def test_offer_is_not_saved_until_owner_accepts_and_is_one_shot(self):
        view = await self.offer()
        self.assertEqual(self.bot.store.own_memories(10, 30), [])
        self.assertEqual([b.label for b in view.children], ["จำไว้", "ไม่ต้องจำ"])
        await view.decide(self.interaction, True)
        self.assertEqual([m["content"] for m in self.bot.store.own_memories(10, 30)], ["ผมชอบเกมแมว"])
        await view.decide(self.interaction, True)
        self.assertEqual(len(self.bot.store.own_memories(10, 30)), 1)

    async def test_other_user_and_other_channel_cannot_confirm(self):
        view = await self.offer()
        self.interaction.user.id = 31
        await view.decide(self.interaction, True)
        self.interaction.user.id = 30
        self.interaction.channel_id = 21
        await view.decide(self.interaction, True)
        self.assertEqual(self.bot.store.own_memories(10, 30), [])
        self.assertEqual(self.interaction.response.send_message.await_count, 2)

    async def test_decline_expiry_edit_and_optout_do_not_save(self):
        view = await self.offer()
        await view.decide(self.interaction, False)
        self.assertEqual(self.bot.store.own_memories(10, 30), [])
        self.bot.suggestion_times.clear()
        view = await self.offer()
        view.deadline = 0
        await view.decide(self.interaction, True)
        self.assertEqual(self.bot.store.own_memories(10, 30), [])
        self.bot.invalidate()
        self.bot.suggestion_times.clear()
        view = await self.offer()
        self.channel.fetch_message.return_value = SimpleNamespace(content="ผมไม่ชอบเกมแมว")
        await view.decide(self.interaction, True)
        self.assertEqual(self.bot.store.own_memories(10, 30), [])
        self.bot.store.set_user(10, 30, optout=1)
        self.bot.invalidate()
        await view.decide(self.interaction, True)
        self.assertEqual(self.bot.store.own_memories(10, 30), [])

    async def test_disabled_and_throttled_suggestions_do_not_post(self):
        self.bot.store.set_meta("suggestions:10:30", False)
        await self.bot.suggest_memory(Job(self.message, "direct", self.message.content, 0))
        self.message.reply.assert_not_awaited()
        self.bot.store.set_meta("suggestions:10:30", True)
        await self.offer()
        await self.bot.suggest_memory(Job(self.message, "direct", self.message.content, 0))
        self.message.reply.assert_awaited_once()

    async def test_timeout_discards_candidate_and_removes_buttons(self):
        view = await self.offer()
        await view.on_timeout()
        self.assertEqual(view.fact, "")
        self.assertFalse(self.bot.suggestion_views)
        view.sent.edit.assert_awaited_once_with(view=None)

    async def test_source_change_during_permission_fetch_cannot_save(self):
        view = await self.offer()

        async def changed(message):
            self.bot.invalidate()
            return self.channel, self.message.author, [20], 1

        self.bot.access_snapshot.side_effect = changed
        await view.decide(self.interaction, True)
        self.assertEqual(self.bot.store.own_memories(10, 30), [])

    async def test_private_suggestion_remains_ephemeral(self):
        from bot.commands import SlashMessage

        interaction = MagicMock(spec=discord.Interaction)
        interaction.followup.send = AsyncMock()
        view = MemorySuggestion(self.bot, self.message, "ผมชอบเกมแมว")
        await SlashMessage(interaction, "ผมชอบเกมแมว").reply("จำไหม", view=view)
        self.assertTrue(interaction.followup.send.call_args.kwargs["ephemeral"])
        self.assertIs(interaction.followup.send.call_args.kwargs["view"], view)
        view.stop()
