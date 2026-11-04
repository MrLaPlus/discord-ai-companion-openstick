"""Ephemeral Discord controls; every mutation rechecks current member/channel permissions."""

from __future__ import annotations

import asyncio
import json
import time

import discord

from .control import TOOLS, panel_summary, set_budget
from .web_tools import checked_url


async def guard(bot, interaction, *, admin=False, channel=None, all_channels=False):
    if (
        not interaction.guild
        or not bot.in_scope(interaction.guild.id)
        or not isinstance(interaction.channel, discord.TextChannel)
    ):
        raise ValueError("ใช้ได้ในห้องข้อความของเซิร์ฟเวอร์ที่กำหนดเท่านั้น")
    guild = interaction.guild
    member, fresh, channels = await asyncio.gather(
        guild.fetch_member(interaction.user.id), bot.fetch_guild(guild.id), guild.fetch_channels()
    )
    if {r.id: r.permissions.value for r in fresh.roles} != {
        r.id: r.permissions.value for r in guild.roles
    } or fresh.owner_id != guild.owner_id:
        raise ValueError("สิทธิ์กำลังเปลี่ยน รอสักครู่แล้วลองใหม่")
    target = next((c for c in channels if c.id == (channel or interaction.channel_id)), None)
    if not isinstance(target, discord.TextChannel) or not target.permissions_for(member).view_channel:
        raise ValueError("อ่านห้องนี้ไม่ได้แล้ว")
    if admin and not member.guild_permissions.manage_guild:
        raise ValueError("ต้องมีสิทธิ์ Manage Server")
    return (member, target, channels) if all_channels else (member, target)


class EditModal(discord.ui.Modal):
    def __init__(self, panel, title, fields, apply):
        super().__init__(title=title, timeout=300)
        self.panel, self.apply = panel, apply
        self.fields = {}
        for key, label, default in fields:
            field = discord.ui.TextInput(label=label, default=str(default), max_length=500, required=False)
            self.fields[key] = field
            self.add_item(field)

    async def on_submit(self, interaction):
        await interaction.response.defer(ephemeral=True)
        try:
            await self.panel.check(interaction, admin=self.panel.page != "personal")
            self.apply({key: str(field).strip() for key, field in self.fields.items()})
            self.panel.bot.invalidate()
            await interaction.edit_original_response(content="บันทึกแล้วครับ เปิด /panel เพื่อดูค่าล่าสุด", view=None)
        except (ValueError, discord.HTTPException):
            await interaction.edit_original_response(
                content="บันทึกไม่ได้: ตรวจสิทธิ์ รูปแบบ และช่วงค่าที่ระบุ แล้วเปิด /panel ใหม่ครับ"
            )


class Panel(discord.ui.View):
    def __init__(self, bot, interaction, page="personal", channel=None, tool="search"):
        super().__init__(timeout=300)
        self.bot, self.owner = bot, interaction.user.id
        self.guild, self.channel = interaction.guild_id, channel or interaction.channel_id
        self.page, self.tool = page, tool
        self.deadline = time.monotonic() + 300
        menu = discord.ui.Select(
            placeholder="เลือกหน้าตั้งค่า",
            options=[
                discord.SelectOption(label=label, value=value, default=value == page)
                for label, value in [
                    ("ของฉัน", "personal"),
                    ("ห้องและบุคลิก", "room"),
                    ("เครื่องมือและสิทธิ์", "tools"),
                    ("โควตาจากผู้ให้บริการ", "usage"),
                    ("สถานะและกู้คืน", "health"),
                    ("หยุดฉุกเฉิน", "stop"),
                    ("ข้อเสนอแนะคำตอบ", "feedback"),
                ]
            ],
            row=0,
        )

        async def choose(i):
            await i.response.defer(ephemeral=True)
            try:
                await self.check(i)
                self.stop()
                bot.control_views.discard(self)
                await show_panel(bot, i, page=menu.values[0], channel=self.channel)
            except (ValueError, discord.HTTPException):
                await i.edit_original_response(content="เปิดหน้านี้ไม่ได้ ตรวจสิทธิ์แล้วลองใหม่ครับ", view=None)

        menu.callback = choose
        self.add_item(menu)
        if page not in {"personal", "usage", "health", "stop", "feedback"}:
            channels = discord.ui.ChannelSelect(
                channel_types=[discord.ChannelType.text], placeholder="เลือกห้อง", row=1
            )

            async def change(i):
                await i.response.defer(ephemeral=True)
                try:
                    await self.check(i, admin=True)
                    cid = channels.values[0].id
                    await guard(bot, i, admin=True, channel=cid)
                    self.stop()
                    bot.control_views.discard(self)
                    await show_panel(bot, i, page=page, channel=cid)
                except (ValueError, discord.HTTPException):
                    await i.edit_original_response(content="เลือกห้องไม่ได้ ตรวจสิทธิ์ครับ", view=None)

            channels.callback = change
            self.add_item(channels)
        if page == "personal":
            self.button("บุคลิกและความยาว", self.personal)
            self.button("กลับมาแซวได้", self.resume)
            self.button("อย่าจำฉัน", self.optout)
            self.button("ลบข้อมูลฉัน", self.erase)
        elif page == "room":
            self.button("ตั้งค่าห้อง", self.room)
            self.button("จังหวะแทรกคุย", self.interject)
        elif page == "tools":
            select = discord.ui.Select(
                options=[discord.SelectOption(label=t, value=t, default=t == tool) for t in TOOLS], row=2
            )

            async def choose_tool(i):
                await i.response.defer(ephemeral=True)
                try:
                    await self.check(i, admin=True)
                    await show_panel(bot, i, page="tools", channel=self.channel, tool=select.values[0])
                except (ValueError, discord.HTTPException):
                    await i.edit_original_response(content="ตรวจสิทธิ์ไม่ผ่านครับ", view=None)

            select.callback = choose_tool
            self.add_item(select)
            self.button("เปิด/ปิดเครื่องมือ", self.toggle_tool)
            self.button("สิทธิ์บทบาท", self.roles)
            self.button("โหมดค้นเว็บ", self.webmode)
        elif page == "news":
            self.button("ตั้งหัวข้อ/ฟีด/คำที่ไม่เอา", self.news)
        elif page == "usage":
            self.button("ตั้งวงเงินและราคา", self.budget)
        elif page == "health":
            self.button("ตรวจการเชื่อมต่อ", self.health)
            self.button("ทดสอบกู้คืนตอนนี้", self.drill)
        elif page == "stop":
            self.button("หยุดห้องนี้ทันที", lambda i: self.stop_now(i, False), danger=True)
            self.button("หยุดทั้งเซิร์ฟเวอร์", lambda i: self.stop_now(i, True), danger=True)
            self.button("เปิดห้องนี้กลับ", lambda i: self.unpause(i, False))
            self.button("เปิดเซิร์ฟเวอร์กลับ", lambda i: self.unpause(i, True))

    async def on_timeout(self):
        self.bot.control_views.discard(self)

    def button(self, label, action, danger=False):
        button = discord.ui.Button(
            label=label, style=discord.ButtonStyle.danger if danger else discord.ButtonStyle.secondary, row=3
        )

        async def callback(i):
            try:
                await self.check(i, admin=self.page != "personal")
                await action(i)
            except (ValueError, discord.HTTPException):
                if i.response.is_done():
                    await i.followup.send("ทำไม่ได้: ตรวจสิทธิ์หรือเปิด /panel ใหม่ครับ", ephemeral=True)
                else:
                    await i.response.send_message("ทำไม่ได้: ตรวจสิทธิ์หรือเปิด /panel ใหม่ครับ", ephemeral=True)

        button.callback = callback
        self.add_item(button)

    async def check(self, interaction, admin=False):
        if (
            interaction.user.id != self.owner
            or interaction.guild_id != self.guild
            or self.is_finished()
            or time.monotonic() >= self.deadline
        ):
            raise ValueError("ปุ่มไม่ใช่ของคุณหรือหมดอายุแล้ว")
        return await guard(self.bot, interaction, admin=admin, channel=self.channel)

    async def modal(self, i, title, fields, apply):
        await i.response.send_modal(EditModal(self, title, fields, apply))

    async def personal(self, i):
        store = self.bot.store

        def apply(v):
            if v["length"] not in {"auto", "short", "detailed"} or int(v["tone"]) not in range(5):
                raise ValueError("ค่าไม่ถูกต้อง")
            store.set_user(self.guild, self.owner, tone=int(v["tone"]))
            store.set_meta(f"length:{self.guild}:{self.owner}", v["length"])

        await self.modal(
            i,
            "บุคลิกของฉัน",
            [
                ("tone", "ระดับแซว 0–4", store.user(self.guild, self.owner)["tone"]),
                (
                    "length",
                    "ความยาว auto / short / detailed",
                    store.meta(f"length:{self.guild}:{self.owner}", "auto"),
                ),
            ],
            apply,
        )

    async def resume(self, i):
        self.bot.store.set_meta(f"gentle:{self.guild}:{self.owner}", 0)
        self.bot.invalidate()
        await i.response.send_message("กลับมาใช้โทนส่วนตัวเดิมแล้วครับ", ephemeral=True)

    async def optout(self, i):
        self.bot.store.set_user(self.guild, self.owner, optout=1)
        self.bot.store.erase_user(self.guild, self.owner)
        self.bot.invalidate()
        await i.response.send_message(
            "ปิดเก็บข้อมูลใหม่ ลบข้อมูลเดิมและบริบทชั่วคราวแล้ว ยังเรียกคุยได้ /privacy store_data:true เพื่อเปิดกลับ ข้อความใน Discord ยังอยู่; backup เก่าหมดอายุตามรอบและใช้บันทึกการลบกันคืนข้อมูล",
            ephemeral=True,
        )

    async def erase(self, i):
        def apply(v):
            if v["confirm"] != "DELETE":
                raise ValueError("ต้องพิมพ์ DELETE")
            self.bot.store.erase_user(self.guild, self.owner)

        await self.modal(
            i, "ลบข้อมูลที่บอทเก็บ (ไม่ลบใน Discord)", [("confirm", "ลบความจำ/ประวัติ/บริบท พิมพ์ DELETE", "")], apply
        )

    async def room(self, i):
        s = self.bot.settings(self.guild, self.channel)
        s["paused"] = self.bot.store.channel(self.guild, self.channel)["paused"]

        def apply(v):
            if any(v[k] not in {"0", "1"} for k in ("respond", "bot_room", "paused")) or int(
                v["tone"]
            ) not in range(5):
                raise ValueError("ค่าไม่ถูกต้อง")
            if v["bot_room"] == "1" and v["respond"] == "0":
                raise ValueError("ห้องบอทต้องเปิดตอบ")
            self.bot.store.set_channel(self.guild, self.channel, **{k: int(x) for k, x in v.items()})

        await self.modal(
            i,
            "ตั้งค่าห้อง (ไม่เปลี่ยนการเก็บประวัติ)",
            [
                (k, label, s[k])
                for k, label in [
                    ("respond", "ตอบ 1 / ปิด 0"),
                    ("bot_room", "ห้องบอทไม่ต้องแท็ก 1 / ปกติ 0"),
                    ("tone", "เพดานโทน 0–4"),
                    ("paused", "พักห้อง 1 / เปิด 0"),
                ]
            ],
            apply,
        )

    async def interject(self, i):
        s = self.bot.settings(self.guild, self.channel)

        def apply(v):
            p, c, h, a, b = float(v["p"]), int(v["cool"]), int(v["hour"]), int(v["start"]), int(v["end"])
            if (
                not 0 <= p <= 1
                or not 300 <= c <= 86400
                or not 1 <= h <= 60
                or not -1 <= a <= 23
                or not -1 <= b <= 23
                or ((a == -1) != (b == -1))
            ):
                raise ValueError("ค่าไม่ถูกต้อง")
            self.bot.store.set_channel(
                self.guild, self.channel, interject=p, cooldown=c, hourly=h, quiet_start=a, quiet_end=b
            )

        await self.modal(
            i,
            "แทรกคุย: 0 ปิด / 0.05 น้อย / 0.1 ปกติ",
            [
                ("p", "โอกาส 0–1", s["interject"]),
                ("cool", "พักอย่างน้อย 300 วินาที", max(300, s["cooldown"])),
                ("hour", "ครั้งสูงสุดต่อชั่วโมง", s["hourly"]),
                ("start", "เริ่มเงียบ ชั่วโมง -1 ปิด", s["quiet_start"]),
                ("end", "จบเงียบ ชั่วโมง -1 ปิด", s["quiet_end"]),
            ],
            apply,
        )

    async def toggle_tool(self, i):
        key = f"tools:{self.guild}:{self.channel}:{self.tool}"
        rule = self.bot.store.meta(key, {})
        rule["enabled"] = not rule.get("enabled", True)
        self.bot.store.set_meta(key, rule)
        self.bot.invalidate()
        await i.response.send_message(f"{self.tool}: {'เปิด' if rule['enabled'] else 'ปิด'}", ephemeral=True)

    async def roles(self, i):
        key = f"tools:{self.guild}:{self.channel}:{self.tool}"
        rule = self.bot.store.meta(key, {})
        roles = {r.id for r in i.guild.roles}

        def apply(v):
            parsed = {k: [int(x.strip()) for x in text.split(",") if x.strip()] for k, text in v.items()}
            if any(len(ids) > 20 or not set(ids) <= roles for ids in parsed.values()):
                raise ValueError("ต้องเป็น role ID ของเซิร์ฟเวอร์นี้ ไม่เกิน 20 รายการ")
            self.bot.store.set_meta(key, {**self.bot.store.meta(key, {}), **parsed})

        await self.modal(
            i,
            "บทบาท: ปิดมีผลเหนือเปิด",
            [
                ("allow", "role IDs อนุญาต คั่น comma; ว่าง=ทุกคน", ",".join(map(str, rule.get("allow", [])))),
                ("deny", "role IDs ปฏิเสธ คั่น comma", ",".join(map(str, rule.get("deny", [])))),
            ],
            apply,
        )

    async def webmode(self, i):
        def apply(v):
            if v["mode"] not in {"off", "button", "auto", "hybrid"}:
                raise ValueError("ค่าไม่ถูกต้อง")
            self.bot.store.set_meta(f"mcp:{self.guild}:{self.channel}", v["mode"])

        await self.modal(
            i,
            "โหมดค้นเว็บ",
            [("mode", "off / button / auto / hybrid", self.bot.web_mode(self.guild, self.channel))],
            apply,
        )

    async def news(self, i):
        key = f"news:{self.guild}:{self.channel}"
        current = self.bot.store.meta(key, {})

        def apply(v):
            topics = [x.strip() for x in v["topics"].split(",") if x.strip()]
            excluded = [x.strip() for x in v["exclude"].split(",") if x.strip()]
            feeds = [checked_url(x.strip()) for x in v["feeds"].splitlines() if x.strip()]
            if (
                len(topics) > 10
                or len(excluded) > 10
                or len(feeds) > 1
                or any(len(x) > 60 for x in topics + excluded)
            ):
                raise ValueError("ไม่เกิน 10 หัวข้อ/คำ และ 1 ฟีด")
            from .web_tools import needs_query_confirmation

            if any(needs_query_confirmation(t) for t in topics):
                raise ValueError("ตั้งหัวข้อสาธารณะเท่านั้น")
            self.bot.store.set_meta(key, {"topics": topics, "exclude": excluded, "feeds": feeds})

        await self.modal(
            i,
            "ข่าวเมื่อมีคนถาม (ไม่ส่งข่าวเอง)",
            [
                ("topics", "หัวข้อ คั่น comma", ",".join(current.get("topics", []))),
                ("exclude", "คำที่ไม่เอา คั่น comma", ",".join(current.get("exclude", []))),
                ("feeds", "RSS/Atom HTTPS 1 URL แยกบรรทัด", "\n".join(current.get("feeds", []))),
            ],
            apply,
        )

    async def budget(self, i):
        from .control import budget_settings

        b = budget_settings(self.bot.store)

        def apply(v):
            set_budget(self.bot.store, float(v["daily"]), float(v["rate"]))

        await self.modal(
            i,
            "งบทั้ง instance / ราคาเป็นประมาณการ",
            [
                ("daily", "เพดาน USD ย้อนหลัง 24h; 0=ไม่ตั้ง", b["daily_usd"]),
                ("rate", "USD/ล้าน token ใช้อัตราสูงสุด input/output", b["usd_per_million"]),
            ],
            apply,
        )

    async def health(self, i):
        await i.response.defer(ephemeral=True)
        report = await self.bot.web.check_health()
        await i.edit_original_response(
            content="ตรวจการเชื่อม MCP (ไม่เรียก AI/อ่านเว็บ): " + json.dumps(report, ensure_ascii=False)
        )

    async def drill(self, i):
        await i.response.defer(ephemeral=True)
        if getattr(self.bot.operations, "drilling", False):
            await i.edit_original_response(content="กำลังทดสอบกู้คืนอยู่ครับ")
            return
        self.bot.operations.drilling = True
        try:
            from .backup import recovery_drill

            snapshot = self.bot.store.meta("last_auto_backup", {})
            if not snapshot.get("file"):
                raise ValueError("ยังไม่มี backup")
            result = await asyncio.to_thread(
                recovery_drill,
                self.bot.config.database.parent / "backups" / snapshot["file"],
                self.bot.config.database.parent,
            )
            self.bot.store.set_meta("last_recovery", {**result, "checked": time.time()})
            await i.edit_original_response(content="ทดสอบกู้คืนในพื้นที่แยกผ่านแล้ว ฐานข้อมูลจริงไม่ถูกแทนที่ครับ")
        except Exception:
            self.bot.store.set_meta("last_recovery", {"ok": False, "checked": time.time()})
            raise ValueError("ทดสอบกู้คืนไม่สำเร็จ ตรวจ /status ครับ") from None
        finally:
            self.bot.operations.drilling = False

    async def stop_now(self, i, whole):
        self.bot.emergency_stop(self.guild, None if whole else self.channel)
        await i.response.send_message(
            "หยุดแล้ว ยกเลิกงานค้างและหยุดส่งคำตอบ งานที่ถึงผู้ให้บริการแล้วอาจมีค่าใช้จ่ายครับ", ephemeral=True
        )

    async def unpause(self, i, whole):
        if whole:
            self.bot.store.set_meta(f"pause:{self.guild}", False)
        else:
            self.bot.store.set_channel(self.guild, self.channel, paused=0)
        self.bot.invalidate()
        await i.response.send_message(
            "เปิดกลับแล้วครับ งานเก่าไม่ถูกเล่นซ้ำ; ถ้าพักทั้งห้องและเซิร์ฟเวอร์ต้องเปิดทั้งสองค่า", ephemeral=True
        )


async def show_panel(bot, interaction, *, page="personal", channel=None, tool="search"):
    admin = page != "personal"
    await guard(bot, interaction, admin=admin, channel=channel)
    if len(bot.control_views) >= 100:
        raise ValueError("หน้าควบคุมเปิดมากเกินไป รอหมดอายุ")
    view = Panel(bot, interaction, page, channel, tool)
    bot.control_views.add(view)
    gid, cid = interaction.guild_id, channel or interaction.channel_id
    if page == "personal":
        details = {
            "tone": bot.store.user(gid, interaction.user.id)["tone"],
            "store_data": not bot.store.user(gid, interaction.user.id)["optout"],
            "length": bot.store.meta(f"length:{gid}:{interaction.user.id}", "auto"),
        }
    elif page == "room":
        details = bot.settings(gid, cid)
    elif page == "tools":
        details = {
            "mode": bot.web_mode(gid, cid),
            "tool": tool,
            "policy": bot.store.meta(f"tools:{gid}:{cid}:{tool}", {"enabled": True, "allow": [], "deny": []}),
        }
    elif page == "news":
        details = bot.store.meta(f"news:{gid}:{cid}", {})
    elif page == "usage":
        from .provider_limits import summary

        details = summary(bot.store)
    elif page == "feedback":
        from .feedback import report

        details = {"text": await report(bot, interaction)}
    elif page == "health":
        details = {
            "provider": {
                "status": getattr(bot.provider, "last_status", "not checked"),
                "checked": bot.store.meta("provider_checked"),
            },
            "tools": {
                name: bot.store.meta(f"tool_health:{name}", {"status": "not checked"}) for name in TOOLS
            },
            "mcp_connection": bot.store.meta("mcp_service_health", {"status": "not checked"}),
            "search_provider": bot.store.meta("search_provider_status", {}),
            "operations": bot.operations.snapshot(),
        }
    else:
        details = {
            "guild_paused": bot.store.meta(f"pause:{gid}", False),
            "room_paused": bot.store.channel(gid, cid)["paused"],
        }
    text = "ตั้งค่าบอท — " + page + f"\nห้อง <#{cid}>\n" + panel_summary(page, details)
    text = text.encode("utf-16-le")[:3800].decode("utf-16-le", errors="ignore")
    if interaction.response.is_done():
        await interaction.edit_original_response(
            content=text, view=view, allowed_mentions=discord.AllowedMentions.none()
        )
    else:
        await interaction.response.send_message(
            text, ephemeral=True, view=view, allowed_mentions=discord.AllowedMentions.none()
        )
