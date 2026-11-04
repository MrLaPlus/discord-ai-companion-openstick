"""Private categorical feedback: no copied questions, answers or free-text reports."""

from __future__ import annotations

import time

import discord

from .panel import guard
from .policy import read_access

LABELS = {"wrong": "ตอบมั่ว", "long": "ยาวไป", "humor": "ไม่ฮา"}


async def submit(view, interaction, category):
    await interaction.response.defer(ephemeral=True, thinking=True)
    try:
        if (
            category not in LABELS
            or not view.owned(interaction)
            or view.is_finished()
            or time.monotonic() >= view.deadline
            or view.revision != view.bot.revision
        ):
            raise ValueError("ปุ่มหมดอายุหรือไม่ใช่ของคุณ")
        snapshot = await view.bot.access_snapshot(view.origin)
        if snapshot is None or view.revision != view.bot.revision:
            raise ValueError("สิทธิ์เปลี่ยน")
        answer = getattr(view.sent, "id", None)
        if not isinstance(answer, int):
            raise ValueError("ไม่พบคำตอบ")
        if view.bot.store.user(interaction.guild_id, interaction.user.id)["optout"]:
            raise ValueError("ปิดเก็บข้อมูลอยู่ จึงไม่บันทึก feedback")
        view.bot.store.add_feedback(
            interaction.guild_id,
            interaction.channel_id,
            interaction.user.id,
            answer,
            category,
            private=view.context_key[3],
        )
        await interaction.edit_original_response(
            content="ส่งข้อเสนอแนะให้แอดมินแล้วครับ เก็บเฉพาะประเภทกับรหัสคำตอบ ไม่เก็บข้อความเพิ่ม และไม่เปิดเผยเนื้อหาคำตอบส่วนตัว"
        )
    except (ValueError, discord.HTTPException):
        await interaction.edit_original_response(content="บันทึกไม่ได้ครับ ปุ่มอาจหมดอายุ สิทธิ์เปลี่ยน หรือคุณปิดเก็บข้อมูลอยู่")


async def report(bot, interaction):
    revision = bot.revision
    member, _, channels = await guard(bot, interaction, admin=True, all_channels=True)
    if revision != bot.revision:
        raise ValueError("สิทธิ์หรือข้อมูลเปลี่ยนระหว่างเปิดรายงานครับ ลองใหม่ได้")
    allowed = [c.id for c in channels if isinstance(c, discord.TextChannel) and read_access(c, member)]
    if not allowed:
        return "ยังไม่มีข้อเสนอแนะจากห้องที่คุณอ่านได้ครับ"
    marks = ",".join("?" for _ in allowed)
    rows = bot.store.db.execute(
        f"SELECT category,count(*) FROM feedback WHERE guild=? AND channel IN ({marks}) GROUP BY category",
        (interaction.guild_id, *allowed),
    ).fetchall()
    recent = bot.store.db.execute(
        f"SELECT answer,channel,category,private FROM feedback WHERE guild=? AND channel IN ({marks}) ORDER BY created DESC LIMIT 8",
        (interaction.guild_id, *allowed),
    ).fetchall()
    lines = ["ข้อเสนอแนะจากห้องที่คุณอ่านได้ (ไม่เก็บเนื้อหาแชท)"]
    lines += [f"{LABELS[r[0]]}: {r[1]}" for r in rows]
    lines += [
        f"{LABELS[r[2]]}: "
        + (
            "คำตอบส่วนตัว — แสดงเฉพาะประเภท"
            if r[3]
            else f"<https://discord.com/channels/{interaction.guild_id}/{r[1]}/{r[0]}>"
        )
        for r in recent
    ]
    return "\n".join(lines) if rows else "ยังไม่มีข้อเสนอแนะจากห้องที่คุณอ่านได้ครับ"
