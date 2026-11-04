"""Conservative first-person preference suggestions, confirmed by the same person."""

from __future__ import annotations

import re
import time

import discord

from .policy import read_access


def preference(text: str) -> str | None:
    # Only explicit first-person preferences/names; no inference from other people's history.
    if len(text) > 300 or any(
        s in text for s in ("?", "ไหม", "หรือเปล่า", "สมมติ", "ล้อเล่น", '"', "“", "```", "\n")
    ):
        return None
    match = re.fullmatch(
        r"(?:ผม|ฉัน|เรา|กู|เค้า|หนู)\s*(?:ไม่ชอบ|ชอบ|อยากให้เรียกว่า|ใช้ชื่อว่า)\s*.{1,100}"
        r"|(?:I (?:like|prefer|dislike)|call me) .{1,100}",
        text.strip(),
        re.I,
    )
    if not match:
        return None
    # Do not propose identifiers, secrets or sensitive personal categories automatically.
    if re.search(
        r"\d{4}|https?://|@|รหัส|คีย์|โทเค็น|ที่อยู่|โทรศัพท์|โรค|ศาสนา|การเมือง|เพศ|"
        r"password|token|secret|address|diagnos|religio|politic|sexual",
        text,
        re.I,
    ):
        return None
    return match[0].strip()


class MemorySuggestion(discord.ui.View):
    def __init__(self, bot, message, fact: str):
        super().__init__(timeout=300)
        self.bot, self.origin, self.fact = bot, message, fact
        self.revision = bot.revision
        self.deadline = time.monotonic() + 300
        self.consumed = False
        self.sent = None

    async def decide(self, interaction: discord.Interaction, save: bool):
        if (
            interaction.user.id != self.origin.author.id
            or interaction.guild_id != self.origin.guild.id
            or interaction.channel_id != self.origin.channel.id
        ):
            await interaction.response.send_message("ปุ่มนี้ให้เจ้าของข้อมูลเลือกเองครับ", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        if self.consumed or self.is_finished() or time.monotonic() >= self.deadline:
            await interaction.followup.send("ข้อเสนอนี้หมดอายุหรือเลือกไปแล้วครับ", ephemeral=True)
            return
        revision = self.bot.revision
        snapshot = await self.bot.access_snapshot(self.origin)
        valid = (
            snapshot is not None
            and revision == self.bot.revision == self.revision
            and self.bot.settings(interaction.guild_id, interaction.channel_id)["readable"]
            and not self.bot.store.user(interaction.guild_id, interaction.user.id)["optout"]
            and self.bot.store.meta(f"suggestions:{interaction.guild_id}:{interaction.user.id}", True)
            and read_access(snapshot[0], snapshot[1])
        )
        if not valid:
            await interaction.followup.send("ข้อมูลหรือสิทธิ์เปลี่ยนแล้ว ลอง /remember แทนได้ครับ", ephemeral=True)
            return
        if isinstance(self.origin, discord.Message):
            try:
                source = await self.origin.channel.fetch_message(self.origin.id)
            except (discord.NotFound, discord.Forbidden):
                await interaction.followup.send("ข้อความต้นทางไม่อยู่แล้วครับ", ephemeral=True)
                return
            if source.content != self.origin.content or revision != self.bot.revision:
                await interaction.followup.send("ข้อความต้นทางเปลี่ยนแล้วครับ", ephemeral=True)
                return
        # No await between the final validation, one-shot claim and SQLite write.
        if self.consumed or self.is_finished() or revision != self.bot.revision:
            return
        self.consumed = True
        try:
            mid = (
                self.bot.store.remember(
                    interaction.guild_id, interaction.channel_id, interaction.user.id, self.fact
                )
                if save
                else None
            )
        except ValueError:
            await interaction.followup.send("ยังบันทึกไม่ได้ ลองจัดการความจำด้วย /memory ครับ", ephemeral=True)
        else:
            if save:
                self.bot.invalidate()
            await interaction.followup.send(
                f"จำแล้วครับ #{mid}; ลบด้วย /forget ได้" if save else "ได้ครับ ไม่จำเรื่องนี้",
                ephemeral=True,
            )
        finally:
            self.stop()
            self.bot.suggestion_views.discard(self)
            try:
                await interaction.message.edit(view=None)
            except discord.HTTPException:
                pass

    @discord.ui.button(label="จำไว้", style=discord.ButtonStyle.primary)
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.decide(interaction, True)

    @discord.ui.button(label="ไม่ต้องจำ", style=discord.ButtonStyle.secondary)
    async def reject(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.decide(interaction, False)

    async def on_timeout(self):
        self.fact = ""
        self.bot.suggestion_views.discard(self)
        if self.sent:
            try:
                await self.sent.edit(view=None)
            except discord.HTTPException:
                pass
