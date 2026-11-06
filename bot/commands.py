from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime

import discord
from discord import app_commands

from .provider_limits import summary as provider_quota
from .web_tools import TOOL_FIELDS


@dataclass
class SlashMessage:
    interaction: discord.Interaction
    content: str
    private: bool = True

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
    def created_at(self) -> datetime:
        return self.interaction.created_at

    async def reply(self, content: str, **kwargs):
        return await self.interaction.followup.send(
            content,
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
            wait=True,
            **({"view": kwargs["view"]} if "view" in kwargs else {}),
            **({"file": kwargs["file"]} if "file" in kwargs else {}),
        )


def register(bot):
    async def ensure(interaction: discord.Interaction, admin: bool = False):
        if (
            not interaction.guild
            or not bot.in_scope(interaction.guild.id)
            or not isinstance(interaction.channel, discord.TextChannel)
        ):
            raise app_commands.CheckFailure("รองรับเฉพาะ text channel ในเซิร์ฟเวอร์ที่กำหนดครับ")
        if admin and not interaction.user.guild_permissions.manage_guild:
            raise app_commands.CheckFailure("ต้องมีสิทธิ์ Manage Server ครับ")

    async def respond(interaction, text):
        kwargs = {"ephemeral": True, "allowed_mentions": discord.AllowedMentions.none()}
        text = text.encode("utf-16-le")[:3800].decode("utf-16-le", errors="ignore")
        if interaction.response.is_done():
            await interaction.followup.send(text, **kwargs)
        else:
            await interaction.response.send_message(text, **kwargs)

    def target(interaction, channel):
        result = channel or interaction.channel
        if result.guild.id != interaction.guild.id:
            raise ValueError("Channel must belong to this server")
        return result

    @bot.tree.error
    async def command_error(interaction, error):
        original = getattr(error, "original", error)
        if isinstance(original, (ValueError, app_commands.CheckFailure)):
            text = str(original)
        elif isinstance(original, discord.HTTPException):
            text = "Discord ปฏิเสธคำขอ ให้ตรวจสิทธิ์หรือรอแล้วลองใหม่ครับ"
        else:
            logging.getLogger(__name__).error("Command failed: %s", type(original).__name__)
            text = "คำสั่งไม่สำเร็จ ให้แอดมินตรวจ log ครับ"
        try:
            await respond(interaction, text)
        except discord.HTTPException:
            pass

    settings = app_commands.Group(
        name="settings",
        description="ตั้งค่าบอท / Bot settings",
        guild_only=True,
        default_permissions=discord.Permissions(manage_guild=True),
    )
    memory = app_commands.Group(name="memory", description="ความจำของคุณ / Your memories", guild_only=True)
    history = app_commands.Group(
        name="history",
        description="นำเข้าประวัติ / Import history",
        guild_only=True,
        default_permissions=discord.Permissions(manage_guild=True),
    )
    room = app_commands.Group(
        name="room-memory",
        description="ความจำห้อง / Room notes",
        guild_only=True,
        default_permissions=discord.Permissions(manage_guild=True),
    )
    mcp = app_commands.Group(
        name="mcp",
        description="เครื่องมือเว็บ / Public web tools",
        guild_only=True,
        default_permissions=discord.Permissions(manage_guild=True),
    )

    @mcp.command(name="channel", description="เปิดค้นเว็บห้องนี้: off/button/auto/hybrid")
    async def mcp_channel(
        interaction: discord.Interaction,
        mode: str = "hybrid",
        channel: discord.TextChannel | None = None,
    ):
        await ensure(interaction, True)
        if mode not in {"off", "button", "auto", "hybrid"}:
            raise ValueError("เลือก off, button, auto หรือ hybrid ครับ")
        if not bot.config.mcp_enabled and mode != "off":
            raise ValueError("ให้ผู้ดูแลเครื่องเปิด MCP_ENABLED และ sidecar ก่อนครับ")
        channel = target(interaction, channel)
        bot.store.set_meta(f"mcp:{interaction.guild.id}:{channel.id}", mode)
        bot.invalidate()
        await respond(interaction, f"ค้นเว็บห้องนี้: {mode}; ส่งเฉพาะคำค้นสาธารณะ ไม่ส่งประวัติแชทไปค้น")

    @mcp.command(name="status", description="สถานะและเครื่องมือที่อนุญาต; ไม่แสดงคีย์")
    async def mcp_status(interaction: discord.Interaction):
        await ensure(interaction, True)
        await respond(
            interaction,
            json.dumps(
                {
                    "enabled": bot.config.mcp_enabled,
                    "mode": bot.web_mode(interaction.guild.id, interaction.channel.id),
                    "last_status": bot.web.last_status,
                    "tools": sorted(TOOL_FIELDS),
                },
                ensure_ascii=False,
            ),
        )

    @bot.tree.command(name="panel", description="หน้าควบคุมบอทแบบส่วนตัว / Bot control panel")
    @app_commands.guild_only()
    async def panel(interaction: discord.Interaction):
        from .panel import show_panel

        await interaction.response.defer(ephemeral=True)
        await show_panel(bot, interaction)

    @bot.tree.command(name="feedback", description="แอดมินดูข้อเสนอแนะคำตอบแบบไม่เก็บแชทเพิ่ม")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    async def feedback_command(interaction: discord.Interaction):
        from .feedback import report

        await interaction.response.defer(ephemeral=True)
        await respond(interaction, await report(bot, interaction))

    @bot.tree.command(name="stop", description="หยุดฉุกเฉิน ยกเลิกคิวและงานที่กำลังทำ / Emergency stop")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    async def emergency(interaction: discord.Interaction, whole_server: bool = False):
        from .panel import guard

        await interaction.response.defer(ephemeral=True)
        await guard(bot, interaction, admin=True)
        bot.emergency_stop(interaction.guild.id, None if whole_server else interaction.channel.id)
        await respond(
            interaction, "หยุดแล้วครับ งานค้างถูกยกเลิก เปิดกลับผ่าน /panel; คำขอที่ส่งถึงผู้ให้บริการไปแล้วอาจมีค่าใช้จ่าย"
        )

    jokes = app_commands.Group(
        name="joke", description="มุกประจำห้องแบบเจ้าตัวยินยอม / Consented room jokes", guild_only=True
    )

    @jokes.command(name="propose", description="เสนอมุก: เจ้าตัวยินยอมและแอดมินอนุมัติ อายุ30วัน")
    async def propose_joke(
        interaction: discord.Interaction,
        text: app_commands.Range[str, 1, 240],
        subject: discord.Member | None = None,
    ):
        from .jokes import JokeProposal, validate_text
        from .panel import guard

        await interaction.response.defer()
        await guard(bot, interaction)
        person = subject or interaction.user
        validate_text(text, person.id)
        if (
            bot.store.user(interaction.guild.id, person.id)["optout"]
            or bot.store.user(interaction.guild.id, interaction.user.id)["optout"]
        ):
            raise ValueError("คนที่เกี่ยวข้องปิดความจำไว้ครับ")
        if len(bot.control_views) >= 100:
            raise ValueError("มีข้อเสนอเปิดมากเกินไป รอสักครู่")
        view = JokeProposal(bot, interaction, text, person.id)
        bot.control_views.add(view)
        await interaction.edit_original_response(
            content=f"เสนอมุก: {text}\nเจ้าตัวกดยินยอม และแอดมินกดอนุมัติภายใน 5 นาที; เสนอแซวตัวเองถือว่ายินยอมแล้ว",
            view=view,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @jokes.command(name="list", description="ดูมุกประจำห้องที่อนุมัติแล้ว")
    async def list_jokes(interaction: discord.Interaction, page: app_commands.Range[int, 1, 5] = 1):
        from .jokes import retained
        from .panel import guard

        await interaction.response.defer(ephemeral=True)
        await guard(bot, interaction)
        rows = retained(bot.store, interaction.guild.id, interaction.channel.id)
        await respond(
            interaction,
            "\n".join(f"{j['id']}: {j['text']}" for j in rows[(page - 1) * 4 : page * 4])
            + f"\nหน้า {page}; เลือก page เพื่อดูหน้าถัดไป"
            if rows
            else "ยังไม่มีมุกที่อนุมัติครับ",
        )

    @jokes.command(name="remove", description="ลบมุกของคุณ/มุกที่แซวคุณ; แอดมินลบได้ทุกมุก")
    async def remove_joke(interaction: discord.Interaction, joke_id: str):
        from .jokes import retained
        from .panel import guard

        await interaction.response.defer(ephemeral=True)
        member, _ = await guard(bot, interaction)
        rows = retained(bot.store, interaction.guild.id, interaction.channel.id)
        found = next((j for j in rows if j["id"] == joke_id), None)
        if not found or (
            interaction.user.id not in {found["author"], found["subject"]}
            and not member.guild_permissions.manage_guild
        ):
            raise ValueError("ไม่พบมุกหรือไม่มีสิทธิ์ลบครับ")
        from .privacy import forget_joke

        forget_joke(bot.store.privacy_folder, interaction.guild.id, interaction.channel.id, joke_id)
        bot.store.set_meta(
            f"jokes:{interaction.guild.id}:{interaction.channel.id}", [j for j in rows if j["id"] != joke_id]
        )
        bot.invalidate()
        await respond(interaction, "ลบมุกแล้วครับ")

    bot.tree.add_command(jokes)

    @settings.command(name="channel", description="กำหนดห้องอ่าน/ตอบ/บอทและกลุ่มแชร์บริบท")
    async def channel_settings(
        interaction: discord.Interaction,
        readable: bool,
        respond_here: bool,
        bot_room: bool,
        channel: discord.TextChannel | None = None,
        share_group: str = "",
    ):
        await ensure(interaction, True)
        channel = target(interaction, channel)
        if bot_room and not respond_here:
            raise ValueError("Bot room requires respond_here=true")
        if len(share_group) > 40:
            raise ValueError("Share group must be at most 40 characters")
        bot.store.set_channel(
            interaction.guild.id,
            channel.id,
            readable=int(readable),
            respond=int(respond_here),
            bot_room=int(bot_room),
            share_group=share_group.strip(),
        )
        bot.invalidate()
        await respond(
            interaction,
            f"บันทึกค่า {channel.mention} แล้วครับ\nแชร์ข้ามห้องในกลุ่มเดียวกันได้เมื่อผู้รับทุกคนอ่านห้องต้นทางได้",
        )

    @settings.command(name="interject", description="โอกาสแทรกคุย 0=ปิด, 0.1=10%; quiet -1=ปิด")
    async def interject_settings(
        interaction: discord.Interaction,
        probability: app_commands.Range[float, 0, 1],
        cooldown: app_commands.Range[int, 30, 86400] = 300,
        hourly: app_commands.Range[int, 1, 60] = 3,
        quiet_start: app_commands.Range[int, -1, 23] = -1,
        quiet_end: app_commands.Range[int, -1, 23] = -1,
        channel: discord.TextChannel | None = None,
    ):
        await ensure(interaction, True)
        channel = target(interaction, channel)
        if (quiet_start == -1) != (quiet_end == -1):
            raise ValueError("Set both quiet hours, or both to -1")
        bot.store.set_channel(
            interaction.guild.id,
            channel.id,
            interject=probability,
            cooldown=cooldown,
            hourly=hourly,
            quiet_start=quiet_start,
            quiet_end=quiet_end,
        )
        bot.invalidate()
        await respond(
            interaction,
            f"ตั้งโอกาสแทรกคุย {probability:.0%}; ช่วงเงียบใช้เวลา {bot.config.timezone} ครับ\n"
            "การตั้งค่านี้ใช้เมื่อห้องไม่ใช่ bot room และเปิดให้ตอบแล้ว",
        )

    @settings.command(name="tone", description="ระดับความกวนของห้อง 0–4 (4=เรื้อน)")
    async def channel_tone(
        interaction: discord.Interaction,
        level: app_commands.Range[int, 0, 4],
        channel: discord.TextChannel | None = None,
    ):
        await ensure(interaction, True)
        channel = target(interaction, channel)
        bot.store.set_channel(interaction.guild.id, channel.id, tone=level)
        bot.invalidate()
        await respond(interaction, f"ตั้งระดับห้องเป็น {level}; ใช้ระดับต่ำกว่าระหว่างค่าห้องกับค่าของคนคุยครับ")

    @settings.command(name="show", description="แสดงการตั้งค่าห้อง")
    async def show_settings(interaction: discord.Interaction, channel: discord.TextChannel | None = None):
        await ensure(interaction, True)
        channel = target(interaction, channel)
        await respond(
            interaction,
            "```json\n"
            + json.dumps(bot.settings(interaction.guild.id, channel.id), ensure_ascii=False, indent=2)
            + "\n```",
        )

    @settings.command(name="alerts", description="เปิดแจ้งปัญหา: ไม่เลือกห้อง = DM เจ้าของเซิร์ฟเวอร์")
    async def alerts(
        interaction: discord.Interaction,
        enabled: bool = True,
        channel: discord.TextChannel | None = None,
    ):
        await ensure(interaction, True)
        if channel:
            channel = target(interaction, channel)
            member = await interaction.guild.fetch_member(bot.user.id)
            permissions = channel.permissions_for(member)
            if not permissions.view_channel or not permissions.send_messages:
                raise ValueError("บอทต้องเห็นและส่งข้อความในห้องแจ้งเตือนได้ครับ")
        bot.store.set_meta(
            f"alerts:{interaction.guild.id}", {"enabled": enabled, "channel": channel.id if channel else 0}
        )
        await respond(interaction, "ตั้งค่าการแจ้งเตือนแล้วครับ; ปัญหาเดิมแจ้งซ้ำไม่ถี่กว่า 6 ชั่วโมง")

    @bot.tree.command(name="help", description="วิธีใช้และเรื่องการเก็บข้อมูล")
    @app_commands.guild_only()
    async def help_command(interaction: discord.Interaction):
        await ensure(interaction)
        from .capabilities import capability_text, schedule_is_ready
        message = SlashMessage(interaction, "")
        text = capability_text(
            "discord", web_enabled=bot.tool_allowed(message, "search", interaction.user),
            fetch_enabled=bot.tool_allowed(message, "fetch_content", interaction.user),
            storage_allowed=not bot.store.user(interaction.guild.id, interaction.user.id)["optout"],
            memory_enabled=bool(bot.settings(interaction.guild.id, interaction.channel.id)["readable"]),
            scheduler_ready=schedule_is_ready(bot),
        )
        await respond(interaction, text + "\n\n/ask ถามส่วนตัว · /panel ตั้งค่า · /privacy จัดการข้อมูล · /mcp ตั้งค่าเว็บ\n"
                      "ห้องที่เปิดอ่านอาจเก็บข้อความตามอายุข้อมูลที่ตั้งไว้; ข้อความและความจำที่เกี่ยวข้องอาจส่งให้ Groq เมื่อตอบ")

    @bot.tree.command(name="remember", description="บอกให้จำเรื่องของคุณ เฉพาะขอบเขตห้องนี้")
    @app_commands.guild_only()
    async def remember(interaction: discord.Interaction, text: app_commands.Range[str, 1, 500]):
        await ensure(interaction)
        if not bot.settings(interaction.guild.id, interaction.channel.id)["readable"]:
            raise ValueError("แอดมินต้องเปิดอ่านห้องนี้ก่อนครับ")
        mid = bot.store.remember(interaction.guild.id, interaction.channel.id, interaction.user.id, text)
        bot.invalidate()
        await respond(interaction, f"จำแล้วครับ รหัส {mid}; ดูด้วย /memory list และลบด้วย /forget")

    @memory.command(name="list", description="ดูความจำของคุณแบบส่วนตัว")
    async def list_memory(interaction: discord.Interaction, page: app_commands.Range[int, 1, 13] = 1):
        await ensure(interaction)
        rows = bot.store.own_memories(interaction.guild.id, interaction.user.id)
        text = "\n".join(
            f"#{r['id']} (ห้อง {r['channel']}): {r['content'][:180]}" for r in rows[(page - 1) * 8 : page * 8]
        )
        if text:
            text += f"\nหน้า {page}/{max(1, (len(rows) + 7) // 8)}"
        await respond(interaction, text or "ยังไม่มีความจำครับ")

    @memory.command(name="suggestions", description="เลือกให้บอทเสนอจำข้อมูลของคุณ พร้อมปุ่มยืนยัน")
    async def suggestions(interaction: discord.Interaction, enabled: bool):
        await ensure(interaction)
        bot.store.set_meta(f"suggestions:{interaction.guild.id}:{interaction.user.id}", enabled)
        bot.invalidate()
        await respond(interaction, "เปิดข้อเสนอจำแล้วครับ ต้องกดจำไว้ก่อนถึงบันทึก" if enabled else "ปิดข้อเสนอจำแล้วครับ")

    @memory.command(name="edit", description="แก้ความจำของคุณตามรหัส")
    async def edit_memory(
        interaction: discord.Interaction, memory_id: int, text: app_commands.Range[str, 1, 500]
    ):
        await ensure(interaction)
        found = bot.store.edit_memory(interaction.guild.id, interaction.user.id, memory_id, text)
        bot.invalidate()
        await respond(interaction, "แก้แล้วครับ" if found else "ไม่พบความจำของคุณรหัสนี้ครับ")

    @memory.command(name="show", description="ดูความจำของคุณหนึ่งรายการแบบเต็ม")
    async def show_memory(interaction: discord.Interaction, memory_id: int):
        await ensure(interaction)
        row = next(
            (
                r
                for r in bot.store.own_memories(interaction.guild.id, interaction.user.id)
                if r["id"] == memory_id
            ),
            None,
        )
        await respond(interaction, row["content"] if row else "ไม่พบความจำของคุณรหัสนี้ครับ")

    @bot.tree.command(name="forget", description="ลบความจำของคุณตามรหัส")
    @app_commands.guild_only()
    async def forget(interaction: discord.Interaction, memory_id: int):
        await ensure(interaction)
        found = bot.store.forget(interaction.guild.id, interaction.user.id, memory_id)
        bot.invalidate()
        await respond(interaction, "ลบแล้วครับ" if found else "ไม่พบความจำของคุณรหัสนี้ครับ")

    @memory.command(name="clear", description="ลบความจำและประวัติที่เก็บของคุณในเซิร์ฟเวอร์นี้")
    async def clear_memory(interaction: discord.Interaction, confirm: bool = False):
        await ensure(interaction)
        if not confirm:
            await respond(
                interaction,
                "คำสั่งนี้ลบทั้งความจำและแชทที่บอทเก็บของคุณ ตั้ง confirm=true เพื่อยืนยัน\n"
                "ถ้าต้องการปิดการเก็บต่อ ให้ใช้ /privacy store_data=false",
            )
            return
        bot.store.erase_user(interaction.guild.id, interaction.user.id)
        bot.invalidate()
        await respond(
            interaction,
            "ลบความจำ ประวัติที่บอทเก็บและบริบทชั่วคราวแล้ว มีบันทึกการลบกันคืนข้อมูลจาก backup; ไม่ลบข้อความใน Discord",
        )

    @bot.tree.command(name="privacy", description="เลือกให้เก็บข้อมูลหรือปิดและลบข้อมูลเดิม")
    @app_commands.guild_only()
    async def privacy(interaction: discord.Interaction, store_data: bool):
        await ensure(interaction)
        bot.store.set_user(interaction.guild.id, interaction.user.id, optout=int(not store_data))
        if not store_data:
            bot.store.erase_user(interaction.guild.id, interaction.user.id)
        bot.invalidate()
        await respond(
            interaction,
            "เปิดการเก็บข้อมูลแล้วครับ"
            if store_data
            else "ปิดการเก็บข้อมูลและลบข้อมูลปัจจุบันแล้วครับ รวมถึงข้ามข้อมูลคุณเมื่อนำเข้าประวัติ\n"
            "ยังคุยได้ โดยข้อความที่ใช้เรียกบอทจะถูกส่งให้ AI เพื่อการตอบครั้งนั้น "
            "backup เดิมหมดอายุตามรอบผู้ดูแล และใช้บันทึกการลบกันคืนข้อมูล",
        )

    @bot.tree.command(name="progress", description="เปิดหรือปิดข้อความแจ้งความคืบหน้าของคุณ")
    @app_commands.guild_only()
    async def progress_setting(interaction: discord.Interaction, enabled: bool):
        await ensure(interaction)
        bot.store.set_user(interaction.guild.id, interaction.user.id, progress_enabled=int(enabled))
        await respond(interaction, "เปิดแจ้งความคืบหน้าแล้วครับ" if enabled else "ปิดแจ้งความคืบหน้าแล้วครับ")

    @bot.tree.command(name="tone", description="ระดับความกวนที่คุณยอมรับ 0–4 (4=เรื้อน) (0=หยุดแซว)")
    @app_commands.guild_only()
    async def personal_tone(interaction: discord.Interaction, level: app_commands.Range[int, 0, 4]):
        await ensure(interaction)
        bot.store.set_user(interaction.guild.id, interaction.user.id, tone=level)
        bot.invalidate()
        await respond(interaction, f"ตั้งระดับการแซวของคุณเป็น {level} แล้วครับ")

    @room.command(name="add", description="บันทึกกติกาหรือประเด็นของห้อง (ไม่ใช่ข้อมูลส่วนตัวคนอื่น)")
    async def add_room_memory(interaction: discord.Interaction, text: app_commands.Range[str, 1, 500]):
        await ensure(interaction, True)
        if not bot.settings(interaction.guild.id, interaction.channel.id)["readable"]:
            raise ValueError("เปิดอ่านห้องนี้ก่อนครับ")
        mid = bot.store.remember(interaction.guild.id, interaction.channel.id, 0, text, "room")
        bot.invalidate()
        await respond(interaction, f"เพิ่มบันทึกห้อง #{mid} แล้วครับ")

    @room.command(name="list", description="แสดงบันทึกห้องนี้")
    async def list_room_memory(interaction: discord.Interaction, page: app_commands.Range[int, 1, 13] = 1):
        await ensure(interaction, True)
        rows = bot.store.memories(interaction.guild.id, [interaction.channel.id], 0)
        await respond(
            interaction,
            "\n".join(f"#{r['id']}: {r['content'][:180]}" for r in rows[(page - 1) * 8 : page * 8])
            or "ยังไม่มีบันทึกในหน้านี้ครับ",
        )

    @room.command(name="forget", description="ลบบันทึกห้องนี้ตามรหัส")
    async def forget_room_memory(interaction: discord.Interaction, memory_id: int):
        await ensure(interaction, True)
        with bot.store.db:
            result = bot.store.db.execute(
                "DELETE FROM memories WHERE id=? AND guild=? AND channel=? AND kind='room'",
                (memory_id, interaction.guild.id, interaction.channel.id),
            )
        bot.invalidate()
        await respond(interaction, "ลบแล้วครับ" if result.rowcount else "ไม่พบบันทึกในห้องนี้ครับ")

    @room.command(name="show", description="ดูบันทึกห้องหนึ่งรายการแบบเต็ม")
    async def show_room_memory(interaction: discord.Interaction, memory_id: int):
        await ensure(interaction, True)
        rows = bot.store.memories(interaction.guild.id, [interaction.channel.id], 0)
        row = next((r for r in rows if r["id"] == memory_id), None)
        await respond(interaction, row["content"] if row else "ไม่พบบันทึกในห้องนี้ครับ")

    @history.command(name="import", description="นำเข้าประวัติ (ข้ามคนที่ opt-out และข้อความบอท)")
    async def import_command(
        interaction: discord.Interaction,
        channel: discord.TextChannel,
        limit: app_commands.Range[int, 1, 5000] = 500,
        days: app_commands.Range[int, 1, 3650] = 7,
        before_message: str = "",
    ):
        await ensure(interaction, True)
        channel = target(interaction, channel)
        if before_message and (
            not before_message.isascii()
            or not before_message.isdecimal()
            or not 0 < int(before_message) < 2**64
        ):
            raise ValueError("before_message must be a valid Discord message ID")
        from .policy import read_access

        if (
            not bot.settings(interaction.guild.id, channel.id)["readable"]
            or not read_access(channel, interaction.user)
            or not read_access(channel, interaction.guild.me)
        ):
            raise ValueError("ต้องเปิดอ่านและมี View Channel + Read Message History ก่อนครับ")
        await interaction.response.defer(ephemeral=True, thinking=True)
        await bot.import_history(
            interaction, channel, limit, days, int(before_message) if before_message else None
        )

    @history.command(name="clear", description="ลบประวัติและความจำของห้องนี้ในฐานข้อมูลบอท")
    async def clear_history(interaction: discord.Interaction, confirm: bool = False):
        await ensure(interaction, True)
        if not confirm:
            await respond(interaction, "ตั้ง confirm=true เพื่อล้างข้อมูลห้องนี้ในบอท; ไม่ลบข้อความใน Discord")
            return
        bot.store.erase_channel(interaction.guild.id, interaction.channel.id)
        bot.invalidate()
        await respond(interaction, "ล้างข้อมูลห้องนี้แล้วครับ หากนำเข้าประวัติอีกจะเก็บใหม่ตามสิทธิ์ปัจจุบัน")

    @bot.tree.command(name="catchup", description="สรุปหัวข้อ ข้อสรุป และเรื่องค้างจากตัวอย่างประวัติตามช่วงเวลา")
    @app_commands.guild_only()
    async def catchup(interaction: discord.Interaction, hours: app_commands.Range[int, 1, 168] = 24):
        await ensure(interaction)
        await interaction.response.defer(ephemeral=True, thinking=True)
        question = f"สรุปหัวข้อ ข้อสรุปที่ตกลงจริง และเรื่องที่ยังค้างในช่วง {hours} ชั่วโมงที่ผ่านมา ระบุว่าเป็นตัวอย่างประวัติที่อ่านได้"
        message = SlashMessage(interaction, question)
        result = await bot.enqueue(message, "direct", question, time.time() - hours * 3600)
        await interaction.edit_original_response(content=result)

    @bot.tree.command(name="ask", description="ถามและค้นประวัติข้ามห้องที่คุณอ่านได้ ตอบแบบส่วนตัว")
    @app_commands.guild_only()
    async def ask(interaction: discord.Interaction, question: app_commands.Range[str, 1, 1000]):
        await ensure(interaction)
        await interaction.response.defer(ephemeral=True, thinking=True)
        result = await bot.enqueue(SlashMessage(interaction, question), "direct", question)
        await interaction.edit_original_response(
            content=result.replace("คำตอบจะส่งในห้องนี้", "คำตอบจะแสดงให้คุณคนเดียว")
        )

    @bot.tree.command(name="time", description="วันและเวลาปัจจุบันของบอท")
    @app_commands.guild_only()
    async def show_time(interaction: discord.Interaction):
        await ensure(interaction)
        from .schedule import clock_text
        await respond(interaction, clock_text(bot.config.timezone))

    @bot.tree.command(name="schedule", description="ตั้งเวลาเตือนหรือรายงานข่าว ส่งในห้องนี้")
    @app_commands.guild_only()
    async def schedule(interaction: discord.Interaction, request: app_commands.Range[str, 1, 400]):
        await ensure(interaction)
        channel = interaction.channel
        bot_member = interaction.guild.me
        if (not channel.permissions_for(interaction.user).view_channel or bot_member is None
                or not channel.permissions_for(bot_member).send_messages
                or not bot.settings(interaction.guild.id, channel.id)["respond"]
                or bot.settings(interaction.guild.id, channel.id)["paused"]):
            raise ValueError("ห้องนี้ไม่เปิดให้บอทตอบ หรือคุณไม่มีสิทธิ์ดูห้องครับ")
        if request.strip().startswith("รายงานข่าว") and not bot.tool_allowed(SlashMessage(interaction, request), "search", interaction.user):
            raise ValueError("ห้องนี้ยังไม่เปิดค้นเว็บหรือคุณไม่มีสิทธิ์ จึงตั้งรายงานข่าวไม่ได้ครับ")
        from .schedule import request_reply
        result = request_reply(bot.store, "discord", interaction.guild.id, channel.id, interaction.user.id, request, bot.config.timezone)
        if result is None:
            raise ValueError("ใช้ เตือนพรุ่งนี้ 07:00 ข้อความ หรือ รายงานข่าว AI ทุกวัน 07:00 ครับ")
        await respond(interaction, result + "\nข้อความจะส่งในห้องนี้")

    @bot.tree.command(name="model", description="เลือกโมเดล AI สำหรับตัวคุณ")
    @app_commands.guild_only()
    @app_commands.choices(choice=[app_commands.Choice(name=name, value=name) for name in ('auto','deepseek','qwen','groq')])
    async def model(interaction: discord.Interaction, choice: app_commands.Choice[str] | None = None):
        await ensure(interaction)
        from .provider import user_model
        key = f'tokenme:model:discord:{interaction.guild.id}:{interaction.user.id}'
        if choice is None:
            await respond(interaction, f"โมเดลปัจจุบัน: {user_model(bot.store,key)}")
        elif choice.value not in {'auto','deepseek','qwen','groq'}:
            raise ValueError('โมเดลไม่ถูกต้องครับ')
        elif bot.store.user(interaction.guild.id, interaction.user.id)['optout']:
            raise ValueError('ปิดการเก็บข้อมูลอยู่ จึงไม่บันทึกโมเดลครับ')
        else:
            bot.store.set_meta(key, {'value':choice.value, 'created':time.time()})
            await respond(interaction, f'ตั้งโมเดลเป็น {choice.value} แล้วครับ')

    @bot.tree.command(name="usage", description="ดูการใช้ AI ของบอท")
    @app_commands.guild_only()
    async def usage(interaction: discord.Interaction, detail: bool = False):
        await ensure(interaction, admin=True)
        from .usage_stats import display
        await respond(interaction, display(bot.store, detail=detail, zone=bot.config.timezone))

    @bot.tree.command(name="report", description="สร้างรายงาน Markdown หรือ TXT พร้อมแหล่งข้อมูล")
    @app_commands.guild_only()
    async def report(interaction: discord.Interaction, format: str, topic: app_commands.Range[str, 1, 250]):
        await ensure(interaction)
        from .report_workflow import parse_report
        question = f'/report {format} {topic}'
        parse_report(question)
        await interaction.response.defer(ephemeral=True, thinking=True)
        result = await bot.enqueue(SlashMessage(interaction, question), 'direct', question)
        await interaction.edit_original_response(content=result.replace('คำตอบจะส่งในห้องนี้', 'คำตอบจะแสดงให้คุณคนเดียว'))

    @bot.tree.command(name="skills", description="ดูทักษะที่บอทเรียกใช้ได้")
    @app_commands.guild_only()
    async def skills(interaction: discord.Interaction):
        await ensure(interaction)
        from .skill_registry import skill_list
        await respond(interaction, skill_list())

    @bot.tree.command(name="skill", description="เรียกใช้ทักษะที่บอทรองรับ")
    @app_commands.guild_only()
    async def skill(interaction: discord.Interaction, name: str, topic: app_commands.Range[str, 1, 300]):
        await ensure(interaction)
        from .skill_registry import invoke_skill
        question = invoke_skill(name, topic)
        await interaction.response.defer(ephemeral=True, thinking=True)
        result = await bot.enqueue(SlashMessage(interaction, question), 'direct', question)
        await interaction.edit_original_response(content=result.replace('คำตอบจะส่งในห้องนี้', 'คำตอบจะแสดงให้คุณคนเดียว'))

    @bot.tree.command(name="tasks", description="ดูงานและรายการตั้งเวลาของคุณในห้องนี้")
    @app_commands.guild_only()
    async def tasks(interaction: discord.Interaction):
        await ensure(interaction)
        from .task_overview import task_overview
        await respond(interaction, task_overview(bot.store, 'discord', interaction.guild.id, interaction.channel.id, interaction.user.id, bot.config.timezone))

    @bot.tree.command(name="schedules", description="ดูรายการตั้งเวลาของคุณ")
    @app_commands.guild_only()
    async def schedules(interaction: discord.Interaction):
        await ensure(interaction)
        from .schedule import listing
        await respond(interaction, listing(bot.store, "discord", interaction.guild.id, interaction.user.id, bot.config.timezone))

    @bot.tree.command(name="cancel_schedule", description="ยกเลิกรายการตั้งเวลาของคุณ")
    @app_commands.guild_only()
    async def cancel_scheduled(interaction: discord.Interaction, id: app_commands.Range[str, 8, 32]):
        await ensure(interaction)
        from .schedule import cancel
        await respond(interaction, "ยกเลิกรายการแล้วครับ" if cancel(bot.store, "discord", interaction.guild.id, interaction.user.id, id.lower()) else "ไม่พบรายการของคุณครับ")

    @bot.tree.command(name="pause", description="พักหรือเปิดบอทในห้องนี้ หรือทั้งเซิร์ฟเวอร์")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    async def pause(interaction: discord.Interaction, paused: bool = True, whole_server: bool = False):
        await ensure(interaction, True)
        rows = (
            bot.store.channels(interaction.guild.id)
            if whole_server
            else [{"channel": interaction.channel.id}]
        )
        for row in rows:
            bot.store.set_channel(interaction.guild.id, row["channel"], paused=int(paused))
        # Also remember guild-wide state so newly configured channels cannot bypass a server pause.
        if whole_server:
            bot.store.set_meta(f"pause:{interaction.guild.id}", paused)
        if paused:
            bot.emergency_stop(interaction.guild.id, None if whole_server else interaction.channel.id)
        bot.invalidate()
        await respond(interaction, "พักแล้วครับ" if paused else "เปิดกลับแล้วครับ")

    @bot.tree.command(name="status", description="สถานะบอทและโควตารวมของ instance; ไม่แสดงคีย์")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    async def status(interaction: discord.Interaction):
        from .panel import guard

        await interaction.response.defer(ephemeral=True)
        await guard(bot, interaction, admin=True)
        from .board_status import board_status, board_summary

        board = await board_status()
        report = {
            "discord_ready": bot.is_ready(),
            "queue": bot.queue.qsize(),
            "workers_alive": sum(not task.done() for task in bot.workers),
            "model": bot.config.model,
            "provider_last_status": bot.provider.last_status,
            "provider_quota": provider_quota(bot.store),
            "history_days": bot.config.history_days,
            "memory_days": bot.config.memory_days,
            "conversation_seconds": bot.config.conversation_seconds,
            "operations": bot.operations.snapshot(),
            "mcp_status": bot.web.last_status,
            "alerts": bot.store.meta(f"alerts:{interaction.guild.id}", {"enabled": True, "channel": 0}),
        }
        await respond(
            interaction,
            board_summary(board)
            + "\n\nสถานะบอท\n```json\n"
            + json.dumps(report, ensure_ascii=False, indent=2)
            + "\n```",
        )

    for group in (settings, memory, history, room, mcp):
        bot.tree.add_command(group)
