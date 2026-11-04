"""Short-lived consent proposals and expiring, administrator-approved room jokes."""

from __future__ import annotations

import re
import secrets
import time

import discord

from .panel import guard


def retained(store, guild, channel):
    return [
        j
        for j in store.meta(f"jokes:{guild}:{channel}", [])
        if j["expires"] > time.time()
        and not store.user(guild, j["subject"])["optout"]
        and not store.user(guild, j["author"])["optout"]
    ]


class JokeProposal(discord.ui.View):
    def __init__(self, bot, interaction, text, subject):
        super().__init__(timeout=300)
        self.bot, self.guild, self.channel = bot, interaction.guild_id, interaction.channel_id
        self.author, self.subject = interaction.user.id, subject
        self.text = text
        self.consent = subject == self.author
        self.approved = False
        self.deadline = time.monotonic() + 300
        self.revision = bot.revision
        self.saved = False

    async def act(self, i, action):
        await i.response.defer(ephemeral=True)
        try:
            if (
                self.saved
                or self.is_finished()
                or time.monotonic() >= self.deadline
                or self.revision != self.bot.revision
            ):
                raise ValueError("ข้อเสนอหมดอายุหรือข้อมูลเปลี่ยน")
            await guard(self.bot, i, admin=action == "approve", channel=self.channel)
            if i.guild_id != self.guild or i.channel_id != self.channel:
                raise ValueError("คนละห้อง")
            if action == "consent" and i.user.id != self.subject:
                raise ValueError("เฉพาะคนที่ถูกแซว")
            if (
                self.bot.store.user(self.guild, self.subject)["optout"]
                or self.bot.store.user(self.guild, self.author)["optout"]
            ):
                raise ValueError("เจ้าตัวปิดความจำ")
            if self.revision != self.bot.revision or self.saved:
                raise ValueError("ข้อเสนอหมดอายุ")
            if action == "consent":
                self.consent = True
            else:
                self.approved = True
            if self.consent and self.approved:
                rows = retained(self.bot.store, self.guild, self.channel)
                if len(rows) >= 20:
                    raise ValueError("เต็ม 20 มุกแล้ว ลบรายการเก่าก่อน")
                self.saved = True
                rows.append(
                    {
                        "id": secrets.token_hex(4),
                        "text": self.text,
                        "author": self.author,
                        "subject": self.subject,
                        "created": time.time(),
                        "expires": time.time() + 30 * 86400,
                        "consent": True,
                        "approved": True,
                    }
                )
                self.bot.store.set_meta(f"jokes:{self.guild}:{self.channel}", rows)
                self.stop()
                self.bot.control_views.discard(self)
                await i.edit_original_response(content="บันทึกมุกประจำห้องแล้ว อายุ 30 วัน ลบด้วย /joke remove ได้ครับ")
                self.text = ""
            else:
                await i.edit_original_response(content="รับทราบแล้ว ยังรอเจ้าตัวยินยอม/แอดมินอนุมัติอีกขั้นครับ")
        except (ValueError, discord.HTTPException):
            await i.edit_original_response(content="ยังบันทึกไม่ได้ ตรวจสิทธิ์ ความยินยอม หรือข้อเสนอหมดอายุครับ")

    @discord.ui.button(label="เจ้าตัวยินยอม", style=discord.ButtonStyle.success)
    async def consent_button(self, i, b):
        await self.act(i, "consent")

    @discord.ui.button(label="แอดมินอนุมัติ", style=discord.ButtonStyle.primary)
    async def approve(self, i, b):
        await self.act(i, "approve")

    async def on_timeout(self):
        self.text = ""
        self.bot.control_views.discard(self)


def validate_text(text, subject):
    mentioned = {int(x) for x in re.findall(r"<@!?(\d+)>", text)}
    if mentioned - {subject} or re.search(r"<@&|@everyone|@here", text):
        raise ValueError("มุกหนึ่งรายการระบุคนที่ยินยอมได้คนเดียว ไม่แท็กบทบาทหรือทั้งห้อง")
    if not text.strip() or len(text) > 240:
        raise ValueError("มุกต้องมี 1–240 ตัวอักษร")
