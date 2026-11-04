"""Owner-only, expiring web query confirmation; never derive a query from history."""

from __future__ import annotations

import asyncio
import logging
import re
import secrets
import time
from dataclasses import dataclass

import discord

log = logging.getLogger(__name__)
EXPIRED = "ปุ่มหรือหน้าค้นหานี้หมดอายุแล้วครับ ถามใหม่เพื่อใช้ปุ่มชุดใหม่ได้เลย"
LABELS = {"ค้นเว็บเพิ่ม 🌐", "หาเพิ่ม", "เจาะเรื่องนี้", "ดูที่มา"}
MODAL_PREFIX = "companion:query:"


async def component_error(interaction, text):
    try:
        if interaction.response.is_done():
            if interaction.response.type == discord.InteractionResponseType.deferred_channel_message:
                await interaction.edit_original_response(content=text)
            else:
                await interaction.followup.send(text, ephemeral=True)
        else:
            await interaction.response.send_message(text, ephemeral=True)
    except discord.HTTPException:
        log.warning("Search interaction acknowledgement unavailable")


async def handle_expired_search(bot, interaction):
    """Acknowledge orphaned controls without rebuilding any private search state."""
    if interaction.response.is_done():
        return
    data = interaction.data or {}
    custom_id = data.get("custom_id", "")
    if interaction.type == discord.InteractionType.modal_submit:
        if not custom_id.startswith(MODAL_PREFIX):
            return
        modal = bot.query_modals.get(custom_id)
        if modal is not None and not modal.is_finished():
            return
    elif interaction.type == discord.InteractionType.component:
        message = interaction.message
        if message is None or bot.user is None or message.author.id != bot.user.id:
            return
        # Legacy buttons used random IDs; match our own message and known labels too.
        if not re.fullmatch(r"[a-f0-9]{32}", custom_id):
            return
        recognized = any(
            getattr(item, "custom_id", None) == custom_id
            and (getattr(item, "label", None) in LABELS or getattr(item, "placeholder", None) == "เลือกแหล่งค้น")
            for row in message.components
            for item in row.children
        )
        if not recognized:
            return
        if any(
            not view.is_finished() and any(item.custom_id == custom_id for item in view.children)
            for view in bot.search_views
        ):
            return  # Registered View dispatch owns this interaction, including its immediate defer.
    else:
        return
    await component_error(interaction, EXPIRED)


@dataclass
class SearchMessage:
    interaction: discord.Interaction
    origin: object
    content: str
    private: bool
    status_private: bool = True

    @property
    def id(self):
        return self.interaction.id

    @property
    def guild(self):
        return self.interaction.guild

    @property
    def channel(self):
        return self.interaction.channel

    @property
    def author(self):
        return self.interaction.user

    @property
    def created_at(self):
        return self.interaction.created_at

    async def reply(self, content, **kwargs):
        options = {"allowed_mentions": discord.AllowedMentions.none()}
        if "view" in kwargs:
            options["view"] = kwargs["view"]
        if self.private:
            return await self.interaction.followup.send(content, ephemeral=True, wait=True, **options)
        return await self.channel.send(content, **options)


class QueryModal(discord.ui.Modal, title="คำค้นที่จะส่งไปเว็บสาธารณะ"):
    query = discord.ui.TextInput(label="ตรวจ/แก้คำค้นก่อนส่ง (ไม่ใส่ข้อมูลส่วนตัว)", max_length=400)

    def __init__(self, view, mode="web"):
        self.mode = mode
        super().__init__(timeout=300, custom_id=MODAL_PREFIX + secrets.token_hex(16))
        self.owner_view = view
        self.query.default = view.query
        view.bot.query_modals[self.custom_id] = self

    async def on_submit(self, interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        await self.owner_view.submit(interaction, str(self.query), mode=self.mode)

    async def on_error(self, interaction, error):
        log.warning("Search modal failed: %s", type(error).__name__)
        await component_error(interaction, "เปิดค้นต่อไม่สำเร็จครับ ลองถามใหม่ได้เลย")
        self.stop()


class SearchView(discord.ui.View):
    def __init__(self, bot, origin, question, query="", *, choose_sources=False):
        super().__init__(timeout=300)
        self.bot, self.origin, self.question, self.query = bot, origin, question, query
        from .conversation import scope

        self.context_key = scope(origin)
        self.revision = bot.revision
        self.deadline = time.monotonic() + 300
        self.consumed = False
        self.sent = None
        self.lookup_state = None
        self.history_state = None
        self.catchup_since = None
        if not choose_sources:
            self.remove_item(self.source_choice)

    def owned(self, interaction):
        return (
            interaction.user.id == self.origin.author.id
            and interaction.guild_id == self.origin.guild.id
            and interaction.channel_id == self.origin.channel.id
        )

    async def interaction_check(self, interaction):
        if not self.owned(interaction):
            await interaction.response.send_message("ให้คนที่ถามกดปุ่มนี้ครับ", ephemeral=True)
            return False
        if (
            self.consumed
            or self.is_finished()
            or time.monotonic() >= self.deadline
            or self.revision != self.bot.revision
        ):
            await interaction.response.send_message(EXPIRED, ephemeral=True)
            return False
        return True

    async def on_error(self, interaction, error, item):
        log.warning("Search control failed: %s", type(error).__name__)
        await component_error(interaction, "เปิดค้นต่อไม่สำเร็จครับ ลองถามใหม่ได้เลย")

    @discord.ui.button(label="ค้นเว็บเพิ่ม 🌐", style=discord.ButtonStyle.secondary)
    async def search(self, interaction, button):
        if not self.owned(interaction):
            await interaction.response.send_message("ให้คนที่ถามกดปุ่มนี้ครับ", ephemeral=True)
            return
        if self.consumed or self.is_finished() or time.monotonic() >= self.deadline:
            await interaction.response.send_message("ปุ่มหมดอายุแล้วครับ ถามใหม่ได้เลย", ephemeral=True)
            return
        # Always show the exact outgoing query. Ambiguous/private follow-ups start blank.
        await interaction.response.send_modal(QueryModal(self))

    @discord.ui.select(
        placeholder="เลือกแหล่งค้น",
        options=[
            discord.SelectOption(label="เว็บ", value="web"),
            discord.SelectOption(label="ประวัติดิส (ส่วนตัว)", value="history"),
            discord.SelectOption(label="ทั้งคู่ (ส่วนตัว)", value="both"),
        ],
        row=1,
    )
    async def source_choice(self, interaction, select):
        if not self.owned(interaction):
            await interaction.response.send_message("ให้คนที่ถามเลือกครับ", ephemeral=True)
            return
        mode = select.values[0]
        if mode == "history":
            await interaction.response.defer(ephemeral=True, thinking=True)
            await self.submit(interaction, self.question, mode=mode)
        else:
            await interaction.response.send_modal(QueryModal(self, mode))

    @discord.ui.button(label="หาเพิ่ม", style=discord.ButtonStyle.secondary, row=0)
    async def more(self, interaction, button):
        if not self.owned(interaction):
            await interaction.response.send_message("ให้คนที่ถามกดครับ", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        await self.submit(interaction, "หาเพิ่ม", mode="more")

    @discord.ui.button(label="เจาะเรื่องนี้", style=discord.ButtonStyle.secondary, row=0)
    async def focus(self, interaction, button):
        if not self.owned(interaction):
            await interaction.response.send_message("ให้คนที่ถามกดครับ", ephemeral=True)
            return
        await interaction.response.send_modal(QueryModal(self))

    @discord.ui.button(label="ดูที่มา", style=discord.ButtonStyle.secondary, row=0)
    async def sources(self, interaction, button):
        if not self.owned(interaction):
            await interaction.response.send_message("ให้คนที่ถามกดครับ", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        await self.submit(interaction, "เอามาจากไหน", mode="sources")

    async def submit(self, interaction, query, *, mode="web"):
        try:
            async with asyncio.timeout(12):
                await self._submit(interaction, query, mode=mode)
        except (TimeoutError, discord.HTTPException):
            await component_error(interaction, "ตรวจสิทธิ์หรือข้อความต้นทางไม่สำเร็จในรอบนี้ครับ ลองถามใหม่ได้เลย")

    async def _submit(self, interaction, query, *, mode="web"):
        if (
            not self.owned(interaction)
            or self.consumed
            or self.is_finished()
            or time.monotonic() >= self.deadline
            or self.revision != self.bot.revision
            or (mode in {"web", "both"} and not self.bot.web_allowed(self.origin))
            or await self.bot.access_snapshot(self.origin) is None
        ):
            await interaction.edit_original_response(content="สิทธิ์/ข้อมูลเปลี่ยนหรือปุ่มหมดอายุแล้วครับ ถามใหม่ได้เลย")
            return
        if isinstance(self.origin, discord.Message):
            current = await self.origin.channel.fetch_message(self.origin.id)
            if current.content != self.origin.content:
                await interaction.edit_original_response(content="ข้อความต้นทางเปลี่ยนแล้วครับ ถามใหม่ได้เลย")
                return
        # Recheck after awaited REST calls, then claim without an await (double-click safe).
        if self.consumed or self.revision != self.bot.revision:
            await interaction.edit_original_response(content=EXPIRED)
            return
        self.consumed = True
        self.stop()
        self.bot.search_views.discard(self)
        question = self.question if mode == "both" else query
        message = SearchMessage(interaction, self.origin, question, True)
        if mode == "sources":
            from types import SimpleNamespace

            answer_id = getattr(self.sent, "id", None)
            message.reference = SimpleNamespace(
                message_id=answer_id if isinstance(answer_id, int) else self.origin.id
            )
        from .conversation import scope

        old_key, new_key = self.context_key, scope(message)
        if old_key != new_key and not self.bot.store.user(message.guild.id, message.author.id)["optout"]:
            if old_key in self.bot.web.references:
                self.bot.web.references[new_key] = list(self.bot.web.references[old_key])
            lookup = self.bot.web.lookup(old_key)
            if lookup:
                self.bot.web.lookups[new_key] = dict(lookup)
            if old_key in self.bot.history.sessions:
                from copy import deepcopy

                state = deepcopy(self.bot.history.sessions[old_key])
                state.key = new_key
                self.bot.history.sessions[new_key] = state
        if mode == "more" and not self.bot.store.user(message.guild.id, message.author.id)["optout"]:
            from copy import deepcopy

            if self.lookup_state:
                self.bot.web.lookups[new_key] = deepcopy(self.lookup_state)
            if self.history_state:
                state = deepcopy(self.history_state)
                state.key = new_key
                self.bot.history.sessions[new_key] = state
        for mapping in (self.bot.web.references, self.bot.web.lookups, self.bot.history.sessions):
            if new_key in mapping:
                mapping.move_to_end(new_key)
            while len(mapping) > 200:
                mapping.popitem(last=False)
        status = await self.bot.enqueue(
            message,
            "web-button",
            question,
            catchup_since=self.catchup_since if mode == "more" else None,
            confirmed_query=query if mode in {"web", "both"} else None,
            lookup_mode=mode if mode in {"web", "both", "history"} else None,
        )
        await interaction.edit_original_response(content=status)

    async def on_timeout(self):
        self.query = self.question = ""
        self.bot.search_views.discard(self)
        if self.sent:
            try:
                await self.sent.edit(view=None)
            except discord.HTTPException:
                pass
