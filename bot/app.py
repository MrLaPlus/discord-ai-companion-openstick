from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import time
import weakref
from dataclasses import dataclass, field, replace
from datetime import datetime

import aiohttp
import discord
from discord import app_commands

from .chat_style import SERIOUS, STOP, Activity
from .config import Config
from .context import build_context, format_answer, unavailable_live_score_reply
from .conversation import Conversation, scope
from .conversation_tools import catchup_heading, catchup_start, repair_question
from .football import football_query, football_request, host_rank
from .history import HistoryLoader, requested_channels
from .operations import Operations
from .policy import eligible_channels, read_access, trigger
from .provider import Provider, ProviderError
from .quota import Quota, QuotaExceeded
from .rendering import sources_only, split_reply, used_sources, wants_sources
from .search_views import SearchView, handle_expired_search
from .storage import Store
from .suggestions import MemorySuggestion, preference
from . import timing
from .web_tools import (
    ToolError,
    WebTools,
    checked_url,
    explicit_query,
    lookup_candidate,
    needs_query_confirmation,
)

log = logging.getLogger(__name__)


@dataclass
class Job:
    message: discord.Message
    reason: str
    question: str
    revision: int
    catchup_since: float | None = None
    confirmed_query: str | None = None
    lookup_mode: str | None = None
    used_tools: set[str] = field(default_factory=set)
    received_at: float = field(default_factory=time.monotonic)
    enqueued_at: float = field(default_factory=time.monotonic)
    gateway_delay: float | None = None


class StaleRequest(Exception):
    pass


class Companion(discord.Client):
    def __init__(self, config: Config):
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(intents=intents, allowed_mentions=discord.AllowedMentions.none(), max_messages=100)
        self.config = config
        self.store = Store(config.database)
        self.quota = Quota(self.store, config)
        self.history = HistoryLoader(self.store, config)
        self.conversation = Conversation(config)
        self.room_activity = Activity()
        self.web = WebTools(config, self.store)
        self.search_views: set[SearchView] = set()
        self.query_modals = weakref.WeakValueDictionary()
        self.control_views: set[discord.ui.View] = set()
        self.operations = Operations(self)
        self.operations_task: asyncio.Task | None = None
        self.telegram_task: asyncio.Task | None = None
        self.telegram = None
        self.scheduler_task: asyncio.Task | None = None
        self.scheduler = None
        self.dashboard_runner = None
        self.suggestion_views: set[MemorySuggestion] = set()
        self.suggestion_times: dict[tuple[int, int], float] = {}
        self.tree = app_commands.CommandTree(self)
        self.queue: asyncio.Queue[Job] = asyncio.Queue(maxsize=config.queue_size)
        self.workers: list[asyncio.Task] = []
        self.background: asyncio.Task | None = None
        self.session: aiohttp.ClientSession | None = None
        self.provider: Provider | None = None
        self.revision = 0
        self.user_times: dict[tuple[int, int], float] = {}
        self.channel_locks: dict[int, asyncio.Lock] = {}
        self.imports: set[int] = set()
        self.notice_times: dict[int, float] = {}
        self.shutting_down = False
        self.pending_messages: set[int] = set()
        self.active_jobs: dict[asyncio.Task, Job] = {}

    async def setup_hook(self):
        from .commands import register

        self.session = aiohttp.ClientSession()
        self.provider = Provider(self.config, self.quota, self.session)
        register(self)
        await self.sync_scoped_commands()
        self.store.maintain(self.config.history_days, self.config.memory_days, self.config.max_messages)
        self.workers = [
            asyncio.create_task(self.worker(), name=f"worker-{i}") for i in range(self.config.workers)
        ]
        self.background = asyncio.create_task(self.heartbeat(), name="heartbeat")
        self.operations_task = asyncio.create_task(self.operations.run(), name="operations")
        from .telegram import Telegram, settings as telegram_settings

        telegram_config = telegram_settings()
        if telegram_config:
            self.telegram = Telegram(self, *telegram_config)
            self.telegram_task = asyncio.create_task(self.telegram.run(), name="telegram")
        from .schedule import Scheduler

        self.scheduler = Scheduler(self)
        self.scheduler_task = asyncio.create_task(self.scheduler.run(), name="scheduler")
        from .dashboard import start_dashboard

        self.dashboard_runner = await start_dashboard(self)

    async def sync_scoped_commands(self):
        if self.config.guild_id:
            for guild_id in (self.config.guild_id, *self.config.additional_guild_ids):
                guild = discord.Object(guild_id)
                self.tree.copy_global_to(guild=guild)
                await self.tree.sync(guild=guild)
        else:
            await self.tree.sync()

    async def on_ready(self):
        log.info(
            "Discord connected; %d guild(s). Channels require explicit /settings channel setup.",
            len(self.guilds),
        )

    async def on_interaction(self, interaction):
        await handle_expired_search(self, interaction)

    def in_scope(self, guild_id: int) -> bool:
        return not self.config.guild_id or guild_id in (
            self.config.guild_id,
            *self.config.additional_guild_ids,
        )

    def invalidate(self):
        # Cancel delivery if storage/privacy/permissions changed while a request was in flight.
        self.revision += 1
        self.conversation.clear()
        self.room_activity.clear()
        self.history.clear()
        self.web.clear()
        for view in self.control_views:
            if hasattr(view, "text"):
                view.text = ""
            view.stop()
        self.control_views.clear()
        for view in self.search_views:
            view.query = view.question = ""
            view.stop()
        self.search_views.clear()
        for modal in list(self.query_modals.values()):
            modal.stop()
        self.query_modals.clear()
        for view in self.suggestion_views:
            view.fact = ""
            view.stop()
        self.suggestion_views.clear()

    def emergency_stop(self, guild: int, channel: int | None = None):
        previous_revision = self.revision
        if channel is None:
            self.store.set_meta(f"pause:{guild}", True)
        else:
            self.store.set_channel(guild, channel, paused=1)
        self.invalidate()
        for task, job in list(self.active_jobs.items()):
            if job.message.guild.id == guild and (channel is None or job.message.channel.id == channel):
                task.cancel()
            elif job.revision == previous_revision:
                job.revision = self.revision
        retained = []
        while not self.queue.empty():
            job = self.queue.get_nowait()
            self.queue.task_done()
            if job.message.guild.id == guild and (channel is None or job.message.channel.id == channel):
                self.store.finish(job.message.id, "emergency stopped")
                self.pending_messages.discard(job.message.id)
            else:
                if job.revision == previous_revision:
                    job.revision = self.revision
                retained.append(job)
        for job in retained:
            self.queue.put_nowait(job)

    def tool_allowed(self, message, name, member=None):
        from .control import tool_allowed

        return self.web_allowed(message) and tool_allowed(
            self.store, message.guild.id, message.channel.id, member or message.author, name
        )

    def settings(self, guild_id: int, channel_id: int) -> dict:
        result = self.store.channel(guild_id, channel_id)
        if self.config.auto_respond_channels and not self.store.meta(
            f"response_disabled:{guild_id}:{channel_id}", False
        ):
            result["respond"] = 1
            if not self.store.meta(f"channel_tone_set:{guild_id}:{channel_id}", False):
                result["tone"] = 4  # Personal opt-in remains the lower cap; explicit room ceilings win.
        if self.store.meta(f"pause:{guild_id}", False):
            result["paused"] = 1
        return result

    def web_mode(self, guild, channel):
        return self.store.meta(
            f"mcp:{guild}:{channel}", "hybrid" if self.config.auto_respond_channels else "off"
        )

    def web_allowed(self, message) -> bool:
        settings = self.settings(message.guild.id, message.channel.id)
        return bool(
            self.config.mcp_enabled
            and settings["respond"]
            and not settings["paused"]
            and self.web_mode(message.guild.id, message.channel.id) != "off"
        )

    def search_view(self, message, question, *, required=False, choose_sources=False):
        settings = self.settings(message.guild.id, message.channel.id)
        if not settings["respond"] or settings["paused"] or len(self.search_views) >= 100:
            return None
        origin = getattr(message, "origin", message)
        member = getattr(origin.guild, "me", None)
        if (
            isinstance(origin, discord.Message)
            and member is not None
            and not origin.channel.permissions_for(member).read_message_history
        ):
            return None  # A button must be able to revalidate its older source; /ask remains available.
        try:
            query = "" if needs_query_confirmation(question) else explicit_query(question)
        except ToolError:
            query = ""
        view = SearchView(self, origin, question, query, choose_sources=choose_sources)
        view.context_key = scope(message)
        if not self.web_allowed(message):
            view.remove_item(view.search)
            view.remove_item(view.focus)
            view.remove_item(view.source_choice)
        self.search_views.add(view)
        return view

    def remember_view_state(self, message, delivered, *, catchup_since=None):
        from copy import deepcopy

        for view in self.search_views:
            if view.context_key == scope(message) and getattr(view.sent, "id", None) in delivered:
                view.catchup_since = catchup_since
                view.lookup_state = deepcopy(self.web.lookup(scope(message)))
                view.history_state = deepcopy(self.history.sessions.get(scope(message)))

    async def heartbeat(self):
        last_maintenance = time.monotonic()
        try:
            while True:
                healthy = self.is_ready() and all(not worker.done() for worker in self.workers)
                self.store.set_meta("heartbeat", time.time() if healthy else 0)
                self.conversation.maintain()
                self.history.maintain()
                self.web.maintain()
                if time.monotonic() - last_maintenance > 3600:
                    self.store.maintain(
                        self.config.history_days, self.config.memory_days, self.config.max_messages
                    )
                    self.invalidate()
                    last_maintenance = time.monotonic()
                await asyncio.sleep(10)
        except asyncio.CancelledError:
            pass

    async def on_message(self, message: discord.Message):
        received_at = time.monotonic()
        gateway_delay = max(0.0, time.time() - message.created_at.timestamp())
        if (
            self.shutting_down
            or not message.guild
            or not self.in_scope(message.guild.id)
            or not isinstance(message.channel, discord.TextChannel)
            or message.author.bot
            or message.webhook_id
            or not message.content.strip()
        ):
            return
        settings = self.settings(message.guild.id, message.channel.id)
        if settings["readable"] and message.guild.me and read_access(message.channel, message.guild.me):
            self.store.put_message(
                message.id,
                message.guild.id,
                message.channel.id,
                message.author.id,
                message.content,
                message.created_at.timestamp(),
            )
        mention = any(user.id == self.user.id for user in message.mentions)
        reply = False
        if message.reference:
            referenced = message.reference.resolved
            if isinstance(referenced, discord.Message):
                reply = referenced.author.id == self.user.id
            elif message.reference.message_id:
                try:
                    referenced = await message.channel.fetch_message(message.reference.message_id)
                    reply = referenced.author.id == self.user.id
                except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                    pass
        now = time.time()
        if not self.store.user(message.guild.id, message.author.id)["optout"]:
            self.room_activity.add(
                message.guild.id, message.channel.id, message.author.id, message.content, message.id
            )
        reason = trigger(
            settings,
            mention=mention,
            reply=reply,
            now=now,
            timezone=self.config.timezone,
            interjections=self.store.interjection_times(message.guild.id, message.channel.id, now),
        )
        if reason == "interject" and not self.room_activity.ready(message.guild.id, message.channel.id):
            return
        if reason:
            content = (
                message.content.replace(f"<@{self.user.id}>", "").replace(f"<@!{self.user.id}>", "").strip()
            )
            await self.enqueue(message, reason, content or "ทักทายหน่อย", received_at=received_at, gateway_delay=gateway_delay)

    async def enqueue(
        self,
        message: discord.Message,
        reason: str,
        question: str,
        catchup_since: float | None = None,
        confirmed_query: str | None = None,
        lookup_mode: str | None = None,
        *, received_at: float | None = None, gateway_delay: float | None = None,
    ) -> str:
        settings = self.settings(message.guild.id, message.channel.id)
        if not settings["respond"] or settings["paused"]:
            return "ห้องนี้ยังไม่ได้เปิดให้ตอบ หรือบอทพักอยู่ครับ"
        if STOP.fullmatch(question.strip()):
            self.store.set_meta(f"gentle:{message.guild.id}:{message.author.id}", time.time() + 1800)
            self.invalidate()
            question = "รับทราบคำขอให้หยุดแซว ตอบยืนยันสั้น ๆ อย่างสุภาพ ไม่เล่นมุก"
        now = time.monotonic()
        key = (message.guild.id, message.author.id)
        if now - self.user_times.get(key, -10000) < self.config.cooldown:
            return "รอสักครู่ก่อนเรียกอีกครั้งครับ"
        if self.queue.full():
            if reason != "interject":
                await self.notice(message, "ตอนนี้คิวเต็ม ลองเรียกใหม่อีกสักครู่ครับ")
            return "คิวเต็มครับ"
        if not self.store.claim(message.id):
            return "ข้อความนี้ถูกประมวลผลแล้วครับ"
        if isinstance(message, discord.Message):
            self.pending_messages.add(message.id)
        self.user_times[key] = now
        if len(self.user_times) > 5000:
            self.user_times = {k: v for k, v in self.user_times.items() if now - v < 300}
        self.queue.put_nowait(
            Job(message, reason, question, self.revision, catchup_since, confirmed_query, lookup_mode,
                received_at=received_at if received_at is not None else time.monotonic(),
                enqueued_at=time.monotonic(), gateway_delay=gateway_delay)
        )
        if reason == "interject":
            self.store.mark_interjection(message.guild.id, message.channel.id, time.time())
        return "เข้าคิวแล้วครับ คำตอบจะส่งในห้องนี้"

    async def notice(self, message: discord.Message, text: str):
        if getattr(message, "private", False) is True or getattr(message, "status_private", False) is True:
            await self.private_status(message, text)
            return
        now = time.monotonic()
        if now - self.notice_times.get(message.channel.id, -1000) < 30:
            return
        self.notice_times[message.channel.id] = now
        try:
            await message.reply(text, mention_author=False, allowed_mentions=discord.AllowedMentions.none())
        except discord.HTTPException:
            pass

    async def private_status(self, message, text: str):
        if getattr(message, "private", False) is True or getattr(message, "status_private", False) is True:
            try:
                await message.interaction.edit_original_response(content=text)
            except discord.HTTPException:
                pass

    async def cancel_job(self, job: Job, status: str):
        self.store.finish(job.message.id, status)
        self.store.set_meta(
            "last_cancel",
            {
                "status": status,
                "state_changed": job.revision != self.revision,
                "access_rejection": self.store.meta("last_access_rejection"),
            },
        )
        await self.private_status(job.message, "คำถามนี้ถูกหยุดเพราะข้อมูลหรือสิทธิ์เปลี่ยนระหว่างทำงานครับ ลองถามใหม่ได้เลย")

    @timing.timed("discord_permissions")
    async def access_snapshot(self, message: discord.Message):
        guild = message.guild
        channels, requester, bot_member, fresh_guild = await asyncio.gather(
            guild.fetch_channels(),
            guild.fetch_member(message.author.id),
            guild.fetch_member(self.user.id),
            self.fetch_guild(guild.id),
        )
        if fresh_guild.owner_id != guild.owner_id or {
            r.id: r.permissions.value for r in fresh_guild.roles
        } != {r.id: r.permissions.value for r in guild.roles}:
            self.store.set_meta("last_access_rejection", "role_cache")
            return None
        text_channels = [c for c in channels if isinstance(c, discord.TextChannel)]
        destination = next((c for c in text_channels if c.id == message.channel.id), None)
        if destination is None:
            self.store.set_meta("last_access_rejection", "channel_missing")
            return None
        target = self.settings(guild.id, destination.id)
        permissions = destination.permissions_for(bot_member)
        if not target["respond"] or target["paused"] or not permissions.send_messages:
            self.store.set_meta("last_access_rejection", "response_permission")
            return None
        # Conversation requires visibility; persisted history has its own stricter ACL below.
        if not destination.permissions_for(requester).view_channel or not permissions.view_channel:
            self.store.set_meta("last_access_rejection", "read_permission")
            return None
        allowed = eligible_channels(
            destination,
            text_channels,
            lambda cid: self.settings(guild.id, cid),
            requester,
            bot_member,
            private=getattr(message, "private", False) is True,
        )
        tone = min(target["tone"], self.store.user(guild.id, requester.id)["tone"])
        if self.store.meta(f"gentle:{guild.id}:{requester.id}", 0) > time.time() or SERIOUS.search(
            message.content
        ):
            tone = 0
        return destination, requester, allowed, tone

    async def quota_retry(self, job, operation, *, context=None, tone=None):
        # Retry only the AI operation that failed before reserving, never repeat MCP/history work.
        opted_out = self.store.user(job.message.guild.id, job.message.author.id)["optout"]
        try:
            return await operation()
        except QuotaExceeded as error:
            if error.daily or error.retry_after > 25 or job.reason == "interject":
                raise
            await timing.sleep(error.retry_after + 0.2)
            fresh = await self.access_snapshot(job.message)
            if (
                fresh is None
                or job.revision != self.revision
                or (tone is not None and fresh[3] != tone)
                or self.store.user(job.message.guild.id, job.message.author.id)["optout"] != opted_out
            ):
                raise StaleRequest from None
            if context is not None:
                if not set(context.channel_ids) <= set(fresh[2]) or not await self.verify_sources(
                    job.message.guild, context.sources, context.fingerprints
                ):
                    raise StaleRequest from None
            if any(not self.tool_allowed(job.message, t, fresh[1]) for t in job.used_tools):
                raise StaleRequest from None
            if job.revision != self.revision:
                raise StaleRequest from None
            return await operation()

    async def process(self, job: Job):
        from .provider import model_choice, user_model
        key = f'tokenme:model:discord:{job.message.guild.id}:{job.message.author.id}'
        token = model_choice.set(user_model(self.store, key))
        try:
            return await self._process(job)
        finally:
            model_choice.reset(token)

    async def _process(self, job: Job):
        message = job.message
        if job.revision != self.revision:
            await self.cancel_job(job, "cancelled: state changed")
            return
        if time.time() - message.created_at.timestamp() > 300:
            await self.cancel_job(job, "expired")
            return
        snapshot = await self.access_snapshot(message)
        if snapshot is None or job.revision != self.revision:
            await self.cancel_job(job, "cancelled: access changed")
            return
        destination, requester, allowed, tone = snapshot
        requested = requested_channels(job.question, getattr(message.guild, "text_channels", []))
        if requested and not requested <= set(allowed):
            await self.deliver(
                job,
                message,
                "ห้องที่ถามยังแชร์ข้อมูลมาที่นี่ไม่ได้ครับ ถ้าคุณอ่านต้นทางได้ ใช้ "
                "`/ask question:คำถามของคุณ` เพื่อค้นและดูคำตอบส่วนตัว "
                "และให้แอดมินเปิดอ่าน/ตั้ง share_group ให้ห้องต้นทางก่อนครับ",
                {},
                allowed,
                tone,
            )
            return
        # Re-fetch the triggering message so an offline edit/deletion cannot leak cached content.
        current = message
        if isinstance(message, discord.Message):
            member = getattr(message.guild, "me", None)
            if member is None or destination.permissions_for(member).read_message_history:
                current = await timing.awaited("discord_message_fetch", destination.fetch_message(message.id))
                if current.content != message.content:
                    await self.cancel_job(job, "cancelled: message changed")
                    return
            elif time.time() - message.created_at.timestamp() > 30:
                # Without history permission use only fresh Gateway input, tracked by pending revisions.
                await self.cancel_job(job, "cancelled: cannot revalidate old input")
                return
        origin = getattr(message, "origin", None)
        if isinstance(origin, discord.Message):
            source = await timing.awaited("discord_message_fetch", destination.fetch_message(origin.id))
            if source.content != origin.content or job.revision != self.revision:
                await self.cancel_job(job, "cancelled: button source changed")
                return
        from .board_status import is_board_question, board_assessment
        if is_board_question(job.question):
            if not getattr(getattr(requester, 'guild_permissions', None), 'manage_guild', False):
                await self.deliver(job, current, "สถานะบอร์ดดูได้เฉพาะแอดมินครับ", {}, allowed, tone)
                return
            assessment = await board_assessment()
            fresh = await self.access_snapshot(message)
            if fresh is None or job.revision != self.revision or not getattr(getattr(fresh[1], 'guild_permissions', None), 'manage_guild', False):
                await self.cancel_job(job, "cancelled: access changed")
                return
            try:
                await fresh[1].send(assessment, allowed_mentions=discord.AllowedMentions.none())
                notice = "ส่งสถานะบอร์ดทางข้อความส่วนตัวแล้วครับ"
            except discord.HTTPException:
                notice = "ส่งข้อความส่วนตัวไม่ได้ครับ ใช้ /status เพื่อดูแบบส่วนตัวได้"
            await self.deliver(job, current, notice, {}, allowed, tone)
            return
        from .capabilities import capability_question, capability_text, schedule_is_ready
        if capability_question(job.question):
            text = capability_text(
                "discord", web_enabled=self.tool_allowed(message, "search", requester),
                fetch_enabled=self.tool_allowed(message, "fetch_content", requester),
                storage_allowed=not self.store.user(message.guild.id, requester.id)["optout"],
                memory_enabled=bool(self.settings(message.guild.id, destination.id)["readable"]),
                scheduler_ready=schedule_is_ready(self),
            )
            await self.deliver(job, current, text, {}, allowed, tone)
            return
        if job.question.strip() in {'/tasks', 'วันนี้มีงานอะไรบ้าง', 'มีงานอะไรบ้าง', 'ดูงานทั้งหมด'}:
            from .task_overview import task_overview
            text = task_overview(self.store, 'discord', message.guild.id, destination.id, requester.id, self.config.timezone)
            await self.deliver(job, current, text, {}, allowed, tone)
            return
        if job.question.startswith('/github_release '):
            from .github_public import latest_release
            if job.reason == 'interject':
                await self.cancel_job(job, 'GitHub lookup requires an explicit user request')
                return
            if not self.tool_allowed(message, 'search', requester) or self.web_mode(message.guild.id, message.channel.id) == 'button':
                await self.deliver(job, current, 'ห้องนี้ยังไม่อนุญาตให้ค้นข้อมูล GitHub อัตโนมัติครับ', {}, allowed, tone)
                return
            fresh = await self.access_snapshot(message)
            if fresh is None or job.revision != self.revision or not self.tool_allowed(message, 'search', fresh[1]):
                await self.cancel_job(job, 'cancelled before GitHub search')
                return
            try:
                self.web.reserve(message.guild.id, requester.id, 'search')
                job.used_tools.add('search')
                reply = await latest_release(self.session, job.question[len('/github_release '):])
            except (ValueError, ToolError) as error:
                reply = str(error)
            await self.deliver(job, current, reply, {}, allowed, tone)
            return
        from .report_workflow import parse_report, build_report
        try:
            report = parse_report(job.question)
        except ValueError as error:
            await self.deliver(job, current, str(error), {}, allowed, tone)
            return
        if report is not None:
            if job.reason == 'interject':
                await self.cancel_job(job, 'report requires an explicit user request')
                return
            if not self.tool_allowed(message, 'search', requester) or self.web_mode(message.guild.id, message.channel.id) == 'button':
                await self.deliver(job, current, 'ห้องนี้ยังไม่อนุญาตให้ค้นเพื่อทำรายงานอัตโนมัติครับ', {}, allowed, tone)
                return
            if self.store.user(message.guild.id, requester.id)['progress_enabled']:
                if getattr(message, 'private', False) or getattr(message, 'status_private', False):
                    await self.private_status(message, 'กำลังค้นและทำรายงานครับ')
                else:
                    try:
                        await current.channel.send('กำลังค้นและทำรายงานครับ', allowed_mentions=discord.AllowedMentions.none())
                    except discord.HTTPException:
                        pass
            async def report_tool(name, value):
                fresh = await self.access_snapshot(message)
                if fresh is None or job.revision != self.revision or not self.tool_allowed(message, name, fresh[1]):
                    raise ToolError('สิทธิ์ใช้เครื่องมือเปลี่ยนแล้วครับ')
                job.used_tools.add(name)
                return await self.web.call(name, {'query' if name == 'search' else 'url': value}, guild=message.guild.id, user=requester.id)
            async def report_model(messages):
                fresh = await self.access_snapshot(message)
                if fresh is None or job.revision != self.revision or any(not self.tool_allowed(message, name, fresh[1]) for name in job.used_tools):
                    raise StaleRequest
                return await self.quota_retry(job, lambda: self.provider.chat(messages), tone=tone)
            try:
                content = await build_report(report[1], tool=report_tool, model=report_model)
                data = content.encode('utf-8')
                if len(data) > 131072:
                    raise ValueError('report too large')
                fresh = await self.access_snapshot(message)
                if fresh is None or job.revision != self.revision or not set(allowed) <= set(fresh[2]) or tone != fresh[3] or any(not self.tool_allowed(message, name, fresh[1]) for name in job.used_tools):
                    await self.cancel_job(job, 'cancelled before report delivery')
                    return
                import io
                await current.reply('รายงานพร้อมแล้วครับ', file=discord.File(io.BytesIO(data), filename='report.' + report[0]), mention_author=False, allowed_mentions=discord.AllowedMentions.none())
                self.store.finish(message.id, 'sent')
                await self.private_status(message, 'ส่งรายงานแล้วครับ')
            except ProviderError as error:
                await self.deliver(job, current, 'สร้างรายงานไม่สำเร็จ: ' + str(error), {}, allowed, tone)
            except (ValueError, ToolError, QuotaExceeded):
                await self.deliver(job, current, 'ค้นหรือสร้างรายงานไม่สำเร็จครับ ลองใหม่ภายหลัง', {}, allowed, tone)
            return
        from .fact_check import fact_check, fact_check_input
        claim = fact_check_input(job.question)
        if claim is not None:
            if job.reason == 'interject' or job.catchup_since is not None:
                await self.cancel_job(job, 'fact check requires an explicit user request')
                return
            if self.web_mode(message.guild.id, message.channel.id) == 'button':
                await self.deliver(job, current, 'ห้องนี้ตั้งให้ยืนยันคำค้นก่อน จึงยังใช้การตรวจข่าวอัตโนมัติไม่ได้ครับ ให้แอดมินพิจารณาโหมดค้นเว็บของห้อง', {}, allowed, tone)
                return
            if not self.tool_allowed(message, 'search', requester):
                await self.deliver(job, current, 'ห้องนี้ยังไม่เปิดค้นเว็บหรือคุณไม่มีสิทธิ์ตรวจข่าวครับ', {}, allowed, tone)
                return
            if claim and self.store.user(message.guild.id, requester.id)['progress_enabled']:
                try:
                    if getattr(message, 'private', False) or getattr(message, 'status_private', False):
                        await self.private_status(message, 'กำลังตรวจข้อกล่าวอ้างและค้นแหล่งข้อมูลครับ')
                    else:
                        await current.channel.send('กำลังตรวจข้อกล่าวอ้างและค้นแหล่งข้อมูลครับ', allowed_mentions=discord.AllowedMentions.none())
                except discord.HTTPException:
                    pass
            async def fact_tool(name, value):
                fresh = await self.access_snapshot(message)
                if fresh is None or job.revision != self.revision or not self.tool_allowed(message, name, fresh[1]):
                    raise ToolError('สิทธิ์ใช้เครื่องมือเปลี่ยนแล้วครับ')
                job.used_tools.add(name)
                return await self.web.call(name, {'query' if name == 'search' else 'url': value}, guild=message.guild.id, user=requester.id)
            async def fact_model(messages):
                fresh = await self.access_snapshot(message)
                if fresh is None or job.revision != self.revision or any(not self.tool_allowed(message, name, fresh[1]) for name in job.used_tools):
                    raise StaleRequest
                return await self.quota_retry(job, lambda: self.provider.chat(messages), tone=tone)
            try:
                reply = await fact_check(claim, tool=fact_tool, model=fact_model)
            except ProviderError as error:
                await self.deliver(job, current, 'ตรวจข่าวไม่สำเร็จ: ' + str(error), {}, allowed, tone)
            except (ToolError, QuotaExceeded):
                reply = 'ตรวจข่าวไม่สำเร็จหรือโควตาไม่พอ จึงยังสรุปว่าจริงหรือเท็จไม่ได้ครับ'
            await self.deliver(job, current, reply, {}, allowed, tone)
            return
        from .image_search import image_query
        try:
            image_term = image_query(job.question)
        except ToolError as error:
            await self.deliver(job, current, str(error), {}, allowed, tone)
            return
        if image_term is not None:
            if not self.tool_allowed(message, 'search', requester):
                await self.deliver(job, current, "ห้องนี้ยังไม่เปิดค้นเว็บหรือคุณไม่มีสิทธิ์ค้นรูปครับ", {}, allowed, tone)
                return
            job.used_tools.add('search')
            if self.store.user(message.guild.id, requester.id)['progress_enabled']:
                if getattr(message, 'private', False) or getattr(message, 'status_private', False):
                    await self.private_status(message, 'กำลังค้นรูปครับ')
                else:
                    try:
                        await current.channel.send('กำลังค้นรูปครับ', allowed_mentions=discord.AllowedMentions.none())
                    except discord.HTTPException:
                        pass
            fresh = await self.access_snapshot(message)
            if fresh is None or job.revision != self.revision or not self.tool_allowed(message, 'search', fresh[1]):
                await self.cancel_job(job, 'cancelled before image search')
                return
            try:
                pages = await self.web.call('search_images', {'query': image_term}, guild=message.guild.id, user=requester.id)
            except ToolError:
                await self.deliver(job, current, 'ค้นรูปไม่สำเร็จครับ ลองใหม่ภายหลัง', {}, allowed, tone)
                return
            text = (pages[0].text + "\nแหล่งรูป: <" + pages[0].url + ">") if pages else 'ยังไม่พบรูปที่ใช้ได้ครับ ลองระบุคำค้นใหม่'
            await self.deliver(job, current, text, {}, allowed, tone)
            return
        from .schedule_edit import is_schedule_edit, edit_reply
        if is_schedule_edit(job.question):
            if getattr(message, "private", False):
                await self.deliver(job, current, "แก้รายการในห้องที่ตั้งไว้ได้ครับ", {}, allowed, tone)
                return
            reply_text = edit_reply(self.store, "discord", message.guild.id, destination.id, requester.id, job.question, self.config.timezone)
            await self.deliver(job, current, reply_text, {}, allowed, tone)
            return
        from .schedule import is_clock_question, is_scheduling_request, request_reply
        schedule_command = is_scheduling_request(job.question)
        if schedule_command and getattr(message, "private", False):
            await self.deliver(job, current, "ตั้งเวลาในห้องด้วย /schedule ได้ครับ ข้อความแจ้งเตือนจะส่งในห้องนั้น", {}, allowed, tone)
            return
        if schedule_command and ("รายงานข่าว" in job.question or "ส่งข่าว" in job.question) and not self.tool_allowed(message, "search", requester):
            await self.deliver(job, current, "ห้องนี้ยังไม่เปิดค้นเว็บหรือคุณไม่มีสิทธิ์ จึงตั้งรายงานข่าวไม่ได้ครับ", {}, allowed, tone)
            return
        if schedule_command or is_clock_question(job.question) or job.question.strip() in {"/schedules", "รายการเตือน", "รายการตั้งเวลา"} or re.match(r"^(?:/cancel|ยกเลิกเตือน)\s+", job.question.strip()):
            reply_text = request_reply(self.store, "discord", message.guild.id, destination.id, requester.id, job.question, self.config.timezone)
            if reply_text is not None:
                await self.deliver(job, current, reply_text, {}, allowed, tone)
                return
        turns = (
            self.conversation.get(scope(message), allowed)
            if not self.store.user(message.guild.id, requester.id)["optout"]
            else []
        )
        reference = getattr(getattr(message, "reference", None), "message_id", None)
        repaired = repair_question(job.question, turns, reference if isinstance(reference, int) else None)
        if repaired == "":
            await self.deliver(
                job,
                current,
                "ยังไม่พบคำตอบก่อนหน้าของคุณที่ใช้แก้ต่อได้ครับ ส่งคำถามเต็มพร้อมคำแก้มาอีกครั้งได้เลย",
                {},
                allowed,
                tone,
            )
            return
        if repaired is not None and re.search(r"ราคา|ผลบอล|สกอ|ข่าว|แพตช์|อัปเดต|อัพเดต|https://", repaired):
            view = self.search_view(message, repaired, required=True) if self.web_allowed(message) else None
            if view:
                view.query = ""
            text = (
                "รับคำแก้แล้วครับ เรื่องนี้ต้องตรวจข้อมูลเว็บใหม่ ตรวจคำค้นที่ปุ่มค้นเว็บก่อนส่งได้เลย"
                if view
                else "รับคำแก้แล้วครับ แต่การค้นเว็บห้องนี้ปิดอยู่ ส่งข้อมูลที่จะให้สรุปมาได้ หรือให้แอดมินเปิด /mcp channel ครับ"
            )
            await self.deliver(job, current, text, {}, allowed, tone, view=view)
            return
        if job.catchup_since is None:
            try:
                job.catchup_since = catchup_start(repaired or job.question, self.config.timezone)
            except ValueError as error:
                await self.deliver(job, current, str(error), {}, allowed, tone)
                return
        if re.fullmatch(r"(?:อย่าจำฉัน|ไม่ต้องจำฉัน|หยุดจำฉัน)[.!\s]*", job.question):
            self.store.set_user(message.guild.id, message.author.id, optout=1)
            self.store.erase_user(message.guild.id, message.author.id)
            self.invalidate()
            job.revision = self.revision
            await self.deliver(
                job,
                current,
                "ปิดเก็บข้อมูลใหม่และลบความจำ/ประวัติที่บอทเก็บกับบริบทชั่วคราวแล้วครับ ข้อความต้นฉบับใน Discord ยังอยู่; บันทึกการลบจะกันคืนข้อมูลจาก backup เปิดกลับด้วย /privacy store_data:true",
                {},
                allowed,
                tone,
            )
            return
        if re.fullmatch(r"ลบข้อมูลฉัน[.!\s]*", job.question):
            await self.deliver(
                job,
                current,
                "เปิด /panel → ของฉัน → ลบข้อมูลฉัน แล้วพิมพ์ DELETE เพื่อยืนยันครับ ลบข้อมูลที่บอทเก็บ ไม่ลบข้อความต้นฉบับใน Discord",
                {},
                allowed,
                tone,
            )
            return
        if sources_only(job.question):
            reply_id = getattr(getattr(message, "reference", None), "message_id", None)
            if not isinstance(reply_id, int):
                reply_id = None
            if reply_id is None:
                row = self.store.db.execute(
                    "SELECT id FROM answers WHERE guild=? AND channel=? AND requester=? ORDER BY created DESC LIMIT 1",
                    (message.guild.id, destination.id, requester.id),
                ).fetchone()
                reply_id = row[0] if row else -1
            sources = self.store.answer_sources(message.guild.id, destination.id, requester.id, reply_id)
            sources = {key: url for key, url in sources.items() if int(url.rsplit("/", 2)[-2]) in allowed}
            web_sources = self.web.saved_sources(scope(message), reply_id)
            if (sources or web_sources) and await self.verify_sources(message.guild, sources):
                text = "บริบทที่ใช้ประกอบคำตอบ:\n" + "\n".join(f"<{url}>" for url in sources.values())
                text += "\n" + "\n".join(f"<{url}>" for url in web_sources.values())
            else:
                sources = {}
                text = "ไม่พบแชทต้นทางที่เก็บไว้สำหรับคำตอบนี้ อาจมาจากความจำที่สั่งจำหรือความรู้ทั่วไปครับ"
            await self.deliver(job, current, text, sources, allowed, tone)
            return
        if not re.search(r"https?://", job.question, re.I) and re.search(
            r"(?:มี|ใช้|ต่อ|เครื่องมือ).*(?:mcp|tavily|duckduckgo|dugduggo)|"
            r"(?:mcp|tavily|duckduckgo|dugduggo).*(?:มี|ไหม|อยู่|ได้)|"
            r"(?:อ่าน|เปิด|สรุป).*(?:ลิงก์|ลิงค์|url).*(?:ได้|ไหม|มั้ย)",
            job.question,
            re.I,
        ):
            text = capability_text(
                "discord", web_enabled=self.tool_allowed(message, "search", requester),
                fetch_enabled=self.tool_allowed(message, "fetch_content", requester),
                storage_allowed=not self.store.user(message.guild.id, requester.id)["optout"],
                memory_enabled=bool(self.settings(message.guild.id, destination.id)["readable"]),
                scheduler_ready=schedule_is_ready(self),
            )
            await self.deliver(job, current, text, {}, allowed, tone)
            return
        if (
            repaired is None
            and job.catchup_since is None
            and job.lookup_mode is None
            and not football_request(job.question)
            and re.match(r"^(?:หา|ค้น|สรุป)", job.question)
            and not re.search(
                r"เว็บ|https://|ข่าว|ล่าสุด|วันนี้|ประวัติ|ในแชท|ในห้อง|ที่คุย|หาเพิ่ม|หาเก่ากว่า|เฉพาะเดือน", job.question
            )
        ):
            view = self.search_view(message, job.question, required=True, choose_sources=True)
            if view:
                await self.deliver(
                    job,
                    current,
                    "อยากให้หาจากเว็บ ประวัติดิส หรือทั้งคู่ครับ? เลือกที่ปุ่มได้เลย",
                    {},
                    allowed,
                    tone,
                    view=view,
                )
                return
        if job.lookup_mode == "both":
            await self.history.hydrate(
                guild=message.guild.id,
                destination=message.channel.id,
                channels=[
                    c
                    for cid in allowed
                    if isinstance((c := message.guild.get_channel(cid)), discord.TextChannel)
                ],
                question=job.question,
                active=lambda: job.revision == self.revision and not self.shutting_down,
            )
        if (
            repaired is None
            and job.lookup_mode != "history"
            and await self.process_web(job, current, allowed, tone)
        ):
            return
        live_notice = unavailable_live_score_reply(job.question) if repaired is None else None
        if live_notice:
            await self.deliver(job, current, live_notice, {}, allowed, tone)
            return
        available = [message.guild.get_channel(cid) for cid in allowed]
        available = [c for c in available if isinstance(c, discord.TextChannel)]
        joined = getattr(getattr(message.guild, "me", None), "joined_at", None)
        plan, report = await self.history.hydrate(
            guild=message.guild.id,
            destination=destination.id,
            channels=available,
            question=repaired or job.question,
            active=lambda: job.revision == self.revision and not self.shutting_down,
            joined=joined,
            catchup_since=job.catchup_since,
            session_key=scope(message)
            if not self.store.user(message.guild.id, requester.id)["optout"]
            else None,
        )
        self.store.set_meta("last_auto_history", report)
        refreshed = await self.access_snapshot(message)
        if refreshed is None or job.revision != self.revision:
            await self.cancel_job(job, "cancelled: history access changed")
            return
        destination, requester, allowed, tone = refreshed
        if report.get("no_session") or (plan.message_ids is not None and not plan.message_ids):
            text = (
                "ยังไม่มีการค้นก่อนหน้า หรือหมดอายุแล้วครับ บอกหัวข้อ ห้อง และช่วงเวลาที่อยากค้นก่อน"
                if report.get("no_session")
                else "ค้นถึงท้ายประวัติที่อ่านได้แล้วครับ ไม่พบข้อความคนเพิ่มเติมในช่วงนี้"
                if report.get("exhausted")
                else "ชุดนี้ยังไม่พบข้อความคนที่เก็บได้ครับ พิมพ์ ‘หาเพิ่ม’ เพื่อค้นชุดถัดไป"
            )
            if job.catchup_since is not None:
                text = (
                    catchup_heading(job.catchup_since, self.config.timezone)
                    + "\n\nไม่พบข้อความคนเพิ่มเติมในชุดประวัติที่อ่านได้สำหรับช่วงนี้ครับ"
                )
            if await self.deliver(job, current, text, {}, allowed, tone) and job.revision == self.revision:
                self.history.commit(plan)
            return
        dialogue = []
        if (
            job.catchup_since is None
            and not report.get("more")
            and not self.store.user(message.guild.id, requester.id)["optout"]
        ):
            dialogue = self.conversation.get(scope(message), allowed)
            for turn in dialogue:
                if not await self.verify_sources(message.guild, turn.sources, turn.fingerprints):
                    await self.cancel_job(job, "cancelled: stale conversation")
                    return
        question = repaired or job.question
        if repaired is None and report.get("more") and plan.session:
            question = plan.session.question + "\nคำขอล่าสุด: " + job.question + " (ใช้เฉพาะประวัติชุดใหม่นี้)"
        context = build_context(
            self.store,
            self.config,
            guild=message.guild.id,
            channel=destination.id,
            user=requester.id,
            allowed=allowed,
            question=question,
            tone=tone,
            catchup_since=job.catchup_since,
            history_plan=plan,
            channel_names={c.id: c.name for c in available if c.id in allowed},
            dialogue=dialogue,
            repair=repaired is not None,
            web_enabled=self.tool_allowed(message, "search", requester) or self.tool_allowed(message, "fetch_content", requester),
            web_search_enabled=self.tool_allowed(message, "search", requester),
            web_fetch_enabled=self.tool_allowed(message, "fetch_content", requester),
            scheduler_ready=schedule_is_ready(self),
            scene=self.room_activity.scene(message.guild.id, destination.id)
            if job.reason == "interject"
            else None,
        )
        if job.revision != self.revision:
            await self.cancel_job(job, "cancelled")
            return
        if not await self.verify_sources(message.guild, context.sources, context.fingerprints):
            await self.cancel_job(job, "cancelled: stale history")
            return
        if job.revision != self.revision:
            return
        async with destination.typing():
            answer = await self.quota_retry(
                job, lambda: self.provider.chat(context.messages), context=context, tone=tone
            )
        if not await self.verify_sources(message.guild, context.sources, context.fingerprints):
            await self.cancel_job(job, "cancelled: stale history")
            return
        final = await self.access_snapshot(message)
        if (
            final is None
            or job.revision != self.revision
            or not set(context.channel_ids) <= set(final[2])
            or tone != final[3]
        ):
            await self.cancel_job(job, "cancelled: context changed")
            return
        if job.revision != self.revision:
            await self.cancel_job(job, "cancelled")
            return
        provenance = used_sources(answer.text, context.sources) or context.sources
        text = format_answer(answer.text, context.sources, wants_sources(job.question))
        if getattr(answer, 'notice', ''):
            text += '\n\n' + answer.notice
        if job.catchup_since is not None:
            text = catchup_heading(job.catchup_since, self.config.timezone) + "\n\n" + text
        if answer.truncated:
            text += "\n\nคำตอบรอบนี้อาจยังไม่ครบ พิมพ์ ‘ต่อ’ ให้ต่อได้ครับ"
        delivered = await self.deliver(
            job,
            current,
            text,
            provenance,
            context.channel_ids,
            tone,
            view=self.search_view(message, job.question)
            if job.reason != "interject" and lookup_candidate(job.question)
            else self.search_view(message, job.question)
            if job.reason != "interject"
            else None,
        )
        if delivered and job.revision == self.revision:
            self.history.commit(plan)
            self.remember_view_state(message, delivered, catchup_since=job.catchup_since)
            if not self.store.user(message.guild.id, requester.id)["optout"]:
                self.conversation.add(
                    scope(message),
                    question,
                    format_answer(answer.text, context.sources),
                    context.sources,
                    context.fingerprints,
                    context.channel_ids,
                    {message.id, *delivered},
                )
                if job.reason != "interject" and self.settings(message.guild.id, destination.id)["readable"]:
                    await self.suggest_memory(job)

    async def process_web(self, job, current, allowed, tone):
        """Public-only planning and tools; Discord context never enters an outgoing query."""
        message = job.message
        if not self.web_allowed(message) or job.reason == "interject" or job.catchup_since is not None:
            return False
        question = job.confirmed_query or job.question
        followup = self.web.lookup(scope(message))
        more = bool(re.fullmatch(r"(?:หาเพิ่ม|ค้นเพิ่ม|เพิ่มเติม|more)[.!\s]*", question, re.I)) and bool(followup)
        if more:
            if needs_query_confirmation(followup["query"]):
                raise ToolError("ตรวจคำค้นใหม่ก่อนค้นต่อครับ")
            question = followup["query"] + f" more sources page {followup['page'] + 1}"
        history_session = self.history.sessions.get(scope(message))
        if (
            more
            and history_session
            and history_session.touched > followup["created"]
            and job.lookup_mode != "web"
        ):
            return False
        urls = list(dict.fromkeys(re.findall(r"https?://[^\s<>]+", question, re.I)))
        if len(urls) > 1:
            raise ToolError("รุ่นนี้อ่านเว็บได้ครั้งละหนึ่งลิงก์ครับ")
        if not job.confirmed_query and not more and not lookup_candidate(question):
            return False
        mode = self.web_mode(message.guild.id, message.channel.id)
        unsafe = needs_query_confirmation(question)
        history_only = bool(re.search(r"ประวัติ|ในแชท|ในห้อง|ที่คุย|เมื่อกี้|<#", question))
        explicit = bool(re.search(r"ค้นเว็บ|ค้นหา|หาในเว็บ|search|https://", question, re.I))
        if history_only and not explicit and not job.confirmed_query and not more:
            return False
        if not job.confirmed_query and (unsafe or mode == "button"):
            await self.deliver(
                job,
                current,
                "ตรวจ/แก้คำค้นสาธารณะที่ปุ่มค้นเว็บเพิ่ม หรือใช้ /ask question:คำค้นสาธารณะ ได้ครับ ไม่ใส่ข้อมูลส่วนตัว",
                {},
                allowed,
                tone,
                view=self.search_view(message, question, required=True),
            )
            return True
        # Reserve enough headroom for synthesis before making an external request.
        if self.quota.remaining_tokens() < 4500:
            raise ToolError("บอทยังรับคำถามค้นเว็บเพิ่มไม่ได้ในรอบนี้ ลองใหม่ภายหลังครับ")
        if urls:
            action, value = "fetch_content", checked_url(urls[0].rstrip(".,)"))
        elif (
            explicit
            or job.confirmed_query
            or more
            or football_request(question)
            or re.search(r"ล่าสุด|สกอ|ผลบอล|ราคา|วันนี้|แพตช์|ข่าว", question)
        ):
            action, value = "search", explicit_query(question)
        else:
            plan = await self.quota_retry(job, lambda: self.provider.plan_public(question), tone=tone)
            if plan is None:
                return False
            action, value = plan
            if action == "fetch":
                raise ToolError("ส่งลิงก์ HTTPS ที่ต้องการอ่านมาโดยตรงครับ")
        if action == "search" and football_request(question):
            value = football_query(question, value)
        if not job.confirmed_query and needs_query_confirmation(value):
            await self.deliver(
                job,
                current,
                "ตรวจคำค้นสาธารณะก่อนส่งได้ที่ปุ่มครับ",
                {},
                allowed,
                tone,
                view=self.search_view(message, question, required=True),
            )
            return True
        # Revalidate permissions/revision after any planner await, before external egress.
        snapshot = await self.access_snapshot(message)
        if snapshot is None or job.revision != self.revision or not self.web_allowed(message):
            await self.cancel_job(job, "cancelled before web")
            return True

        async def call_tool(name, target):
            fresh = await self.access_snapshot(message)
            if (
                fresh is None
                or job.revision != self.revision
                or not self.tool_allowed(message, name, fresh[1])
            ):
                raise ToolError("เครื่องมือนี้ปิดอยู่ หรือสิทธิ์เปลี่ยนแล้วครับ")
            job.used_tools.add(name)
            return await self.web.call(
                name,
                {"query" if name in {"search", "rss_news"} else "url": target},
                guild=message.guild.id,
                user=message.author.id,
            )

        if not self.tool_allowed(message, action, snapshot[1]):
            raise ToolError("เครื่องมือนี้ปิดอยู่ หรือสิทธิ์เปลี่ยนแล้วครับ")
        # Progress carries no query or source data and does not mark the job as delivered.
        progress_text = "กำลังค้นข้อมูลและอ่านแหล่งที่มาครับ" if action == "search" else "กำลังอ่านลิงก์ครับ"
        if self.store.user(message.guild.id, message.author.id)["progress_enabled"]:
            try:
                if getattr(message, "private", False) is True or getattr(message, "status_private", False) is True:
                    await self.private_status(message, progress_text)
                else:
                    await current.channel.send(progress_text, allowed_mentions=discord.AllowedMentions.none())
            except discord.HTTPException:
                pass
        pages = await call_tool(action, value)
        if action == "fetch_content" and pages and not any(p.text.strip() for p in pages):
            raise ToolError("อ่านลิงก์แล้วไม่พบข้อความที่นำมาสรุปได้ครับ เว็บอาจต้องใช้ JavaScript หรือเข้าสู่ระบบ")
        if more:
            pages = [p for p in pages if p.url not in followup["seen"]]
        if not pages:
            await self.deliver(
                job,
                current,
                "ค้นแล้วไม่พบแหล่งใหม่ที่ใช้ตอบได้ครับ ลองระบุหัวข้อหรือวันที่เพิ่มได้",
                {},
                allowed,
                tone,
                view=self.search_view(message, question),
            )
            return True
        if action == "search":
            if football_request(question):
                pages.sort(key=lambda page: host_rank(page.url, question))
            from .research import read_search_results
            pages = await read_search_results(pages, lambda url: call_tool("fetch_content", url))
        if job.revision != self.revision:
            await self.cancel_job(job, "cancelled during web")
            return True
        context = build_context(
            self.store,
            replace(self.config, tpm=self.quota.remaining_tokens()),
            guild=message.guild.id,
            channel=message.channel.id,
            user=message.author.id,
            allowed=allowed if job.lookup_mode == "both" else [],
            question=(job.question + "\nPublic lookup: " + question)
            if job.lookup_mode == "both"
            else question,
            tone=tone,
            web_pages=pages,
            web_enabled=True,
            web_kind="page" if action == "fetch_content" else "mixed",
        )
        if not context.web_sources:
            raise ToolError("พื้นที่บริบทเหลือไม่พอให้อ่านผลเว็บครับ ลองใหม่ภายหลังได้")
        if not await self.verify_sources(message.guild, context.sources, context.fingerprints):
            await self.cancel_job(job, "cancelled combined sources")
            return True
        fresh = await self.access_snapshot(message)
        if (
            fresh is None
            or job.revision != self.revision
            or not self.web_allowed(message)
            or any(not self.tool_allowed(message, t, fresh[1]) for t in job.used_tools)
        ):
            await self.cancel_job(job, "cancelled before synthesis")
            return True
        try:
            async with current.channel.typing():
                answer = await self.quota_retry(
                    job, lambda: self.provider.chat(context.messages), context=context, tone=tone
                )
        except ProviderError as error:
            await self.deliver(job, current, 'ได้ข้อมูลจากเว็บแล้ว แต่สรุปไม่สำเร็จ: ' + str(error), {}, allowed, tone)
            return True
        except QuotaExceeded:
            await self.deliver(job, current, "ได้ข้อมูลจากเว็บแล้ว แต่สรุปไม่สำเร็จครับ ลองใหม่ภายหลัง", {}, allowed, tone)
            return True
        if not await self.verify_sources(message.guild, context.sources, context.fingerprints):
            await self.cancel_job(job, "cancelled combined sources")
            return True
        text = format_answer(
            answer.text,
            context.sources,
            action in {"search", "fetch_content"} or wants_sources(job.question),
            web_sources=context.web_sources,
        )
        if getattr(answer, 'notice', ''):
            text += '\n\n' + answer.notice
        if (
            action == "fetch_content"
            and context.web_sources
            and not any(url in text for url in context.web_sources.values())
        ):
            text += "\n\nแหล่งข้อมูล: <" + next(iter(context.web_sources.values())) + ">"
        if action == "search":
            from .research import evidence_kind
            if any(evidence_kind(page) == "search snippet" for page in pages):
                text += "\n\nบางแหล่งอ่านหน้าเว็บไม่ได้ จึงใช้ข้อความสั้นจากผลค้นหาแทนครับ"
        if answer.truncated:
            text += "\n\nคำตอบรอบนี้อาจยังไม่ครบ พิมพ์ ‘ต่อ’ ให้ต่อได้ครับ"
        delivered = await self.deliver(
            job, current, text, context.sources, allowed, tone, view=self.search_view(message, job.question)
        )
        if (
            delivered
            and job.revision == self.revision
            and not self.store.user(message.guild.id, message.author.id)["optout"]
        ):
            self.web.save_sources(scope(message), {message.id, *delivered}, context.web_sources)
            if action == "search":
                self.web.save_lookup(
                    scope(message), followup["query"] if more else question, pages, more=more
                )
            else:
                self.web.lookups.pop(scope(message), None)
            self.remember_view_state(message, delivered)
            self.conversation.add(
                scope(message),
                question,
                format_answer(answer.text, context.sources, web_sources=context.web_sources),
                context.sources,
                context.fingerprints,
                allowed,
                {message.id, *delivered},
            )
        return True

    @timing.timed("delivery_including_checks")
    async def deliver(
        self, job, current, text: str, sources: dict[str, str], allowed: list[int], tone: int, *, view=None
    ):
        sent_ids = set()
        parts = split_reply(text)
        for index, part in enumerate(parts):
            snapshot = await self.access_snapshot(job.message)
            if (
                snapshot is None
                or job.revision != self.revision
                or not set(allowed) <= set(snapshot[2])
                or tone != snapshot[3]
                or any(not self.tool_allowed(job.message, t, snapshot[1]) for t in job.used_tools)
            ):
                await self.cancel_job(job, "cancelled during delivery")
                if view:
                    view.stop()
                    self.search_views.discard(view)
                return
            options = {"view": view} if view and index == len(parts) - 1 else {}
            member = getattr(job.message.guild, "me", None)
            can_reference = member is None or current.channel.permissions_for(member).read_message_history
            if (index == 0 and can_reference) or not isinstance(current, discord.Message):
                sent = await timing.awaited("discord_send", current.reply(
                    part, mention_author=False, allowed_mentions=discord.AllowedMentions.none(), **options
                ))
            else:
                sent = await timing.awaited("discord_send", job.message.channel.send(
                    part, allowed_mentions=discord.AllowedMentions.none(), **options
                ))
            if index == 0:
                timing.add("bot_to_first_reply", time.monotonic() - job.received_at)
            if options:
                view.sent = sent
            if sent is not None and isinstance(getattr(sent, "id", None), int):
                self.store.save_answer(
                    sent.id, job.message.guild.id, job.message.channel.id, job.message.author.id, sources
                )
                sent_ids.add(sent.id)
        self.store.finish(job.message.id, "sent")
        await self.private_status(job.message, "ตอบแล้วครับ")
        return sent_ids or {job.message.id}

    async def suggest_memory(self, job: Job):
        message = job.message
        fact = preference(job.question)
        key = (message.guild.id, message.author.id)
        now = time.monotonic()
        if (
            not fact
            or not self.config.memory_suggestions
            or len(self.suggestion_views) >= 100
            or now - self.suggestion_times.get(key, -1e9) < 3600
            or not self.store.meta(f"suggestions:{key[0]}:{key[1]}", True)
            or any(m["content"] == fact for m in self.store.own_memories(*key))
        ):
            return
        if job.revision != self.revision or await self.access_snapshot(message) is None:
            return
        if job.revision != self.revision or self.store.user(*key)["optout"]:
            return
        view = MemorySuggestion(self, message, fact)
        self.suggestion_views.add(view)
        try:
            view.sent = await message.reply(
                f"อยากให้จำว่า “{fact}” ไหมครับ? เลือกได้เฉพาะคุณ (หมดอายุ 5 นาที)",
                view=view,
                mention_author=False,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except discord.HTTPException:
            view.stop()
            self.suggestion_views.discard(view)
        else:
            self.suggestion_times[key] = now
            if len(self.suggestion_times) > 5000:
                self.suggestion_times = {k: v for k, v in self.suggestion_times.items() if now - v < 3600}

    @timing.timed("discord_source_checks")
    async def verify_sources(
        self, guild, sources: dict[str, str], fingerprints: dict[int, str] | None = None
    ) -> bool:
        # Check before model submission AND delivery, covering edits/deletions missed while offline.
        for url in sources.values():
            source_channel_id, source_message_id = map(int, url.rsplit("/", 2)[-2:])
            source_channel = guild.get_channel(source_channel_id)
            if not isinstance(source_channel, discord.TextChannel):
                return False
            try:
                source = await source_channel.fetch_message(source_message_id)
            except discord.NotFound:
                self.store.delete_message(source_message_id)
                self.invalidate()
                return False
            stored = self.store.db.execute(
                "SELECT content FROM messages WHERE id=?", (source_message_id,)
            ).fetchone()
            if not stored or source.content[:4000] != stored[0]:
                self.store.delete_message(source_message_id)
                self.invalidate()
                return False
            if (
                fingerprints
                and fingerprints.get(source_message_id) != hashlib.sha256(stored[0].encode()).hexdigest()
            ):
                return False
        return True

    async def on_error(self, event_method, *args, **kwargs):
        log.error("Discord event failed: %s (payload omitted)", event_method)

    async def worker(self):
        while True:
            job = await self.queue.get()
            trace = {"prequeue": max(0.0, job.enqueued_at - job.received_at),
                     "queue": max(0.0, time.monotonic() - job.enqueued_at)}
            if job.gateway_delay is not None:
                trace["gateway_to_bot"] = job.gateway_delay
            timing_token = timing.current.set(trace)
            lock_started = time.monotonic()
            try:
                lock = self.channel_locks.setdefault(job.message.channel.id, asyncio.Lock())
                async with lock:
                    timing.add("channel_lock", time.monotonic() - lock_started)
                    async with asyncio.timeout(90):
                        task = asyncio.create_task(self.process(job))
                        self.active_jobs[task] = job
                        try:
                            await task
                        except asyncio.CancelledError:
                            if asyncio.current_task().cancelling():
                                raise
                            self.store.finish(job.message.id, "emergency stopped")
                            await self.private_status(job.message, "หยุดงานนี้แล้วครับ")
                        finally:
                            self.active_jobs.pop(task, None)
            except ToolError as error:
                self.store.finish(job.message.id, "web unavailable")
                if self.web.failures >= 3:
                    self.operations.fault(job.message.guild.id, "mcp")
                if job.reason != "interject" and job.revision == self.revision:
                    await self.notice(job.message, str(error))
            except StaleRequest:
                await self.cancel_job(job, "cancelled during quota wait")
            except QuotaExceeded:
                self.store.finish(job.message.id, "local capacity")
                if job.reason != "interject" and job.revision == self.revision:
                    await self.notice(job.message, "บอทยังประมวลผลคำถามนี้ไม่ได้ในรอบนี้ ลองใหม่ภายหลังครับ")
            except ProviderError as error:
                self.store.finish(job.message.id, "provider error")
                self.operations.fault(job.message.guild.id, "provider")
                if job.reason != "interject" and job.revision == self.revision:
                    await self.notice(job.message, str(error))
            except discord.HTTPException:
                self.store.finish(job.message.id, "discord access/error")
                await self.private_status(job.message, "Discord ปฏิเสธคำขอนี้ ตรวจสิทธิ์หรือรอสักครู่แล้วลองใหม่ครับ")
                log.warning("Discord request failed; delivery skipped")
            except asyncio.CancelledError:
                self.store.finish(job.message.id, "shutdown")
                raise
            except Exception as error:
                self.store.finish(job.message.id, "internal error")
                self.operations.fault(job.message.guild.id, "job_error")
                log.error("Job failed: %s", type(error).__name__)
                if job.reason != "interject" and job.revision == self.revision:
                    await self.notice(job.message, "ประมวลผลไม่สำเร็จ ให้แอดมินตรวจ /status ครับ")
            finally:
                timing.add("bot_total", time.monotonic() - job.received_at)
                timing.current.reset(timing_token)
                log.info("job_durations_seconds %s", json.dumps(
                    {key: round(value, 4) for key, value in trace.items()}, sort_keys=True
                ))
                self.pending_messages.discard(job.message.id)
                self.queue.task_done()

    def affects_context(self, message_id: int) -> bool:
        return (
            message_id in self.pending_messages
            or self.conversation.contains(message_id)
            or self.web.contains(message_id)
            or bool(self.store.db.execute("SELECT 1 FROM messages WHERE id=?", (message_id,)).fetchone())
        )

    async def on_raw_message_delete(self, payload):
        affected = self.affects_context(payload.message_id)
        self.store.delete_message(payload.message_id)
        if affected:
            self.invalidate()

    async def on_raw_bulk_message_delete(self, payload):
        affected = any(self.affects_context(mid) for mid in payload.message_ids)
        for message_id in payload.message_ids:
            self.store.delete_message(message_id)
        if affected:
            self.invalidate()

    async def on_raw_message_edit(self, payload):
        # Invalidate instead of trusting a partial Gateway update; ingestion/import can refill later.
        if "content" in payload.data:
            affected = self.affects_context(payload.message_id)
            self.store.delete_message(payload.message_id)
            if affected:
                self.invalidate()

    async def on_guild_channel_update(self, before, after):
        settings = self.settings(after.guild.id, after.id)
        if (settings["readable"] or settings["respond"]) and (
            before.overwrites != after.overwrites or before.is_nsfw() != after.is_nsfw()
        ):
            self.invalidate()

    async def on_guild_channel_delete(self, channel):
        self.store.erase_channel(channel.guild.id, channel.id)
        self.invalidate()

    async def on_guild_role_update(self, before, after):
        if before.permissions.value != after.permissions.value:
            self.invalidate()

    async def on_guild_role_delete(self, role):
        self.invalidate()

    async def on_guild_role_create(self, role):
        self.invalidate()

    async def on_guild_update(self, before, after):
        if before.owner_id != after.owner_id:
            self.invalidate()

    async def import_history(
        self,
        interaction,
        channel: discord.TextChannel,
        limit: int,
        days: int,
        before_message: int | None = None,
    ):
        if channel.id in self.imports:
            await interaction.followup.send("ห้องนี้กำลังนำเข้าอยู่ครับ", ephemeral=True)
            return
        self.imports.add(channel.id)
        count, scanned = 0, 0
        revision = self.revision
        deadline = time.monotonic() + 600
        interrupted = False
        oldest_id = None
        try:
            since = datetime.fromtimestamp(time.time() - days * 86400, tz=discord.utils.utcnow().tzinfo)
            before = discord.Object(before_message) if before_message else None
            async for message in channel.history(limit=limit, after=since, before=before, oldest_first=False):
                settings = self.settings(channel.guild.id, channel.id)
                if (
                    not settings["readable"]
                    or self.shutting_down
                    or revision != self.revision
                    or time.monotonic() >= deadline
                ):
                    interrupted = True
                    break
                scanned += 1
                oldest_id = message.id
                if not message.author.bot and not message.webhook_id and message.content:
                    if not self.store.user(channel.guild.id, message.author.id)["optout"]:
                        self.store.put_message(
                            message.id,
                            channel.guild.id,
                            channel.id,
                            message.author.id,
                            message.content,
                            message.created_at.timestamp(),
                        )
                        count += 1
                if scanned % 100 == 0:
                    await interaction.edit_original_response(
                        content=f"กำลังนำเข้า: ตรวจ {scanned}, เก็บ {count} ข้อความ"
                    )
                    await asyncio.sleep(0)
            self.store.maintain(self.config.history_days, self.config.memory_days, self.config.max_messages)
            self.invalidate()
            await interaction.edit_original_response(
                content=f"{'หยุดนำเข้าเพราะสถานะเปลี่ยนหรือครบเวลา' if interrupted else 'นำเข้าเสร็จ'}: "
                f"ตรวจ {scanned}, เก็บ {count} ข้อความครับ"
                + (f"\nนำเข้าชุดเก่ากว่านี้ด้วย before_message={oldest_id}" if oldest_id else "")
            )
        finally:
            self.imports.discard(channel.id)

    async def close(self):
        if self.shutting_down:
            return
        self.shutting_down = True
        try:
            await asyncio.wait_for(self.queue.join(), timeout=10)
        except asyncio.TimeoutError:
            pass
        tasks = [*self.workers, *([self.background] if self.background else [])]
        if self.operations_task:
            tasks.append(self.operations_task)
        if self.telegram_task:
            tasks.append(self.telegram_task)
        if self.scheduler_task:
            tasks.append(self.scheduler_task)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self.invalidate()
        if self.dashboard_runner:
            await self.dashboard_runner.cleanup()
            self.dashboard_runner = None
        self.store.set_meta("heartbeat", 0)
        await super().close()
        if self.session:
            await self.session.close()
        self.store.close()
