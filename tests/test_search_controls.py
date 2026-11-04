import asyncio
import unittest
from types import SimpleNamespace as S
from unittest.mock import AsyncMock

import discord
from discord.ui.view import ViewStore

from bot.search_views import EXPIRED, QueryModal, SearchView, component_error, handle_expired_search
from tests import test_web


class SearchControlTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = test_web.WebAppTests.asyncSetUp
    asyncTearDown = test_web.WebAppTests.asyncTearDown
    enable = test_web.WebAppTests.enable

    def interaction(self, item=None):
        response = S(
            is_done=lambda: False,
            send_message=AsyncMock(),
            send_modal=AsyncMock(),
            defer=AsyncMock(),
        )
        return S(
            type=discord.InteractionType.component,
            data={"custom_id": item.custom_id} if item else {},
            user=self.message.author,
            guild=self.guild,
            channel=self.channel,
            guild_id=10,
            channel_id=20,
            response=response,
            edit_original_response=AsyncMock(),
            followup=S(send=AsyncMock()),
            message=S(id=888, author=S(id=99), components=[]),
        )

    async def test_all_four_active_buttons_ack_before_search_work(self):
        self.enable()
        view = SearchView(self.bot, self.message, "cats", "cats")
        for name in ["search", "more", "focus", "sources"]:
            item = getattr(view, name)
            interaction = self.interaction(item)
            view.submit = AsyncMock()
            self.assertTrue(await view.interaction_check(interaction))
            await item.callback(interaction)
            if name in {"search", "focus"}:
                interaction.response.send_modal.assert_awaited_once()
                view.submit.assert_not_awaited()
                modal = interaction.response.send_modal.call_args.args[0]
                self.assertIsInstance(modal, QueryModal)
                modal.stop()
            else:
                interaction.response.defer.assert_awaited_once_with(ephemeral=True, thinking=True)
                view.submit.assert_awaited_once()
        view.stop()

    async def test_all_four_reject_other_owner_and_expiry(self):
        self.enable()
        view = SearchView(self.bot, self.message, "cats")
        for item in view.children:
            interaction = self.interaction(item)
            interaction.user = S(id=123)
            self.assertFalse(await view.interaction_check(interaction))
            self.assertTrue(interaction.response.send_message.call_args.kwargs["ephemeral"])
        view.deadline = 0
        interaction = self.interaction(view.focus)
        self.assertFalse(await view.interaction_check(interaction))
        self.assertEqual(interaction.response.send_message.call_args.args[0], EXPIRED)
        view.stop()

    async def test_actual_sdk_stopped_or_restart_buttons_receive_private_fallback(self):
        self.enable()
        for stopped in (True, False):
            view = SearchView(self.bot, self.message, "cats")
            store = ViewStore(self.bot._connection)
            store.add_view(view, message_id=888)
            self.bot.search_views.add(view)
            controls = [view.search, view.more, view.focus, view.sources]
            if stopped:
                view.stop()
            else:
                # A fresh process has no View store or retained search scopes.
                store = ViewStore(self.bot._connection)
                self.bot.search_views.clear()
            for item in controls:
                interaction = self.interaction(item)
                interaction.message.components = [S(children=controls)]
                store.dispatch_view(2, item.custom_id, interaction)
                await asyncio.sleep(0)
                interaction.response.send_message.assert_not_awaited()
                await handle_expired_search(self.bot, interaction)
                interaction.response.send_message.assert_awaited_once_with(EXPIRED, ephemeral=True)
                self.bot.provider.chat.assert_not_awaited()
            view.stop()
            self.bot.search_views.clear()

    async def test_fallback_ignores_active_foreign_and_unrelated_components(self):
        self.enable()
        view = SearchView(self.bot, self.message, "cats")
        self.bot.search_views.add(view)
        interaction = self.interaction(view.more)
        interaction.message.components = [S(children=[view.more])]
        await handle_expired_search(self.bot, interaction)
        interaction.response.send_message.assert_not_awaited()
        self.bot.search_views.clear()
        interaction.message.author.id = 123
        await handle_expired_search(self.bot, interaction)
        interaction.response.send_message.assert_not_awaited()
        interaction.message.author.id = 99
        interaction.message.components = [
            S(children=[S(custom_id=view.more.custom_id, label="Other action")])
        ]
        await handle_expired_search(self.bot, interaction)
        interaction.response.send_message.assert_not_awaited()
        view.stop()

    async def test_modal_valid_submit_defers_and_orphan_submit_gets_private_expiry(self):
        self.enable()
        view = SearchView(self.bot, self.message, "cats", "cats")
        modal = QueryModal(view)
        view.submit = AsyncMock()
        interaction = self.interaction()
        interaction.type = discord.InteractionType.modal_submit
        interaction.data = {"custom_id": modal.custom_id}
        await handle_expired_search(self.bot, interaction)
        interaction.response.send_message.assert_not_awaited()
        await modal.on_submit(interaction)
        interaction.response.defer.assert_awaited_once_with(ephemeral=True, thinking=True)
        view.submit.assert_awaited_once()
        modal.stop()
        await handle_expired_search(self.bot, interaction)
        interaction.response.send_message.assert_awaited_once_with(EXPIRED, ephemeral=True)
        view.stop()

    async def test_privacy_invalidation_closes_pending_modals(self):
        self.enable()
        view = SearchView(self.bot, self.message, "cats")
        modal = QueryModal(view)
        self.bot.search_views.add(view)
        self.bot.invalidate()
        self.assertTrue(modal.is_finished())
        self.assertFalse(self.bot.query_modals)

    async def test_deferred_recheck_failure_updates_private_status(self):
        self.enable()
        view = SearchView(self.bot, self.message, "cats")
        interaction = self.interaction()
        interaction.response.is_done = lambda: True
        interaction.response.type = discord.InteractionResponseType.deferred_channel_message
        self.bot.access_snapshot.side_effect = TimeoutError()
        await view.submit(interaction, "หาเพิ่ม", mode="more")
        interaction.edit_original_response.assert_awaited_once()
        self.bot.provider.chat.assert_not_awaited()
        view.stop()

    async def test_error_after_modal_open_never_overwrites_public_answer(self):
        interaction = self.interaction()
        interaction.response.is_done = lambda: True
        interaction.response.type = discord.InteractionResponseType.modal
        await component_error(interaction, "try again")
        interaction.edit_original_response.assert_not_awaited()
        interaction.followup.send.assert_awaited_once_with("try again", ephemeral=True)
