from __future__ import annotations

from . import timing

import asyncio
import re
import time
from collections import OrderedDict
from copy import deepcopy
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import discord


@dataclass(frozen=True)
class HistoryPlan:
    after: datetime | None = None
    before: datetime | None = None
    oldest_first: bool = False
    deep: bool = False
    message_ids: tuple[int, ...] | None = None
    session: SearchSession | None = field(default=None, compare=False, repr=False)


@dataclass
class SearchSession:
    key: tuple
    question: str
    plan: HistoryPlan
    cursors: dict[int, int] = field(default_factory=dict)
    exhausted: set[int] = field(default_factory=set)
    touched: float = field(default_factory=time.monotonic)
    anchor: datetime | None = None


def wants_more(question: str) -> bool:
    return bool(re.search(r"หาเก่ากว่านี้|หาเพิ่ม|ค้นต่อ|ค้นเพิ่ม|เฉพาะเดือนนั้น|search more|search older", question, re.I))


def requested_channels(question: str, channels) -> set[int]:
    ids = {int(cid) for cid in re.findall(r"<#(\d+)>", question)}
    normalized = "".join(c for c in question.lower() if c.isalnum())
    for channel in channels:
        name = "".join(c for c in channel.name.lower() if c.isalnum())
        named = any(prefix + name in normalized for prefix in ("หอง", "ชอง", "channel"))
        hashtag = bool(re.search(r"(?:^|\s)#" + re.escape(channel.name) + r"(?:\s|$)", question, re.I))
        if len(name) >= 3 and (named or hashtag):
            ids.add(channel.id)
    return ids


def plan_history(
    question: str, timezone: str, *, joined: datetime | None = None, now: datetime | None = None
) -> HistoryPlan:
    zone = ZoneInfo(timezone)
    now = (now or datetime.now(zone)).astimezone(zone)
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    deep = bool(
        re.search(
            r"ประวัติ|ย้อนหลัง|เมื่อ|ก่อน|เคย|คุย|พูด|จำได้|สรุป|หา.*ข้อความ|ห้อง|<#|history|last|ago", question, re.I
        )
    )
    if re.search(r"ก่อน.*(?:บอท|bot).*(?:เข้า|มา|join)", question, re.I) and joined:
        return HistoryPlan(before=joined, deep=True)
    if re.search(r"แรกสุด|เริ่มห้อง|แรก.?ๆ|oldest|first message", question, re.I):
        return HistoryPlan(oldest_first=True, deep=True)
    dates = re.findall(r"(?<!\d)(\d{4})-(\d{1,2})-(\d{1,2})(?!\d)", question)
    if dates:
        try:
            year, month, day = map(int, dates[0])
            date = datetime(year - 543 if year > 2400 else year, month, day, tzinfo=zone)
            return HistoryPlan(after=date, before=date + timedelta(days=1), deep=True)
        except ValueError:
            pass
    year = re.search(r"(?:ปี|พ\.?ศ\.?|ค\.?ศ\.?|year)\s*(\d{4})", question, re.I)
    if year:
        value = int(year[1])
        value = value - 543 if value > 2400 else value
        if 2015 <= value < 9999:
            return HistoryPlan(
                datetime(value, 1, 1, tzinfo=zone), datetime(value + 1, 1, 1, tzinfo=zone), deep=True
            )
    if "ปีที่แล้ว" in question:
        return HistoryPlan(
            today.replace(year=today.year - 1, month=1, day=1), today.replace(month=1, day=1), deep=True
        )
    if "เดือนที่แล้ว" in question or "เดือนก่อน" in question:
        first = today.replace(day=1)
        return HistoryPlan((first - timedelta(days=1)).replace(day=1), first, deep=True)
    if "เมื่อคืน" in question:
        return HistoryPlan(today - timedelta(hours=6), today + timedelta(hours=6), deep=True)
    if "เมื่อวาน" in question:
        return HistoryPlan(today - timedelta(days=1), today, deep=True)
    if "วันนี้" in question:
        return HistoryPlan(today, deep=True)
    days = re.search(r"(?:ย้อนหลัง|ที่ผ่านมา)\s*(\d{1,4})\s*วัน", question)
    if days:
        return HistoryPlan(now - timedelta(days=min(3650, int(days[1]))), deep=True)
    return HistoryPlan(deep=deep)


class HistoryLoader:
    def __init__(self, store, config):
        self.store, self.config = store, config
        self.refreshed: dict[tuple, float] = {}
        self.sessions: OrderedDict[tuple, SearchSession] = OrderedDict()

    def clear(self):
        self.sessions.clear()
        self.refreshed.clear()

    def commit(self, plan: HistoryPlan):
        # Advance only after successful delivery: failed AI/quota attempts must not skip a page.
        if plan.session:
            self.sessions[plan.session.key] = plan.session
            self.sessions.move_to_end(plan.session.key)
            while len(self.sessions) > 200:
                self.sessions.popitem(last=False)

    def maintain(self):
        cutoff = time.monotonic() - 1800
        for key in list(self.sessions):
            if self.sessions[key].touched < cutoff:
                del self.sessions[key]

    @timing.timed("history")
    async def hydrate(
        self,
        *,
        guild: int,
        destination: int,
        channels,
        question: str,
        active,
        joined: datetime | None = None,
        catchup_since: float | None = None,
        session_key: tuple | None = None,
    ) -> tuple[HistoryPlan, dict]:
        plan = plan_history(question, self.config.timezone, joined=joined)
        if catchup_since is not None:
            plan = HistoryPlan(
                after=datetime.fromtimestamp(catchup_since, ZoneInfo(self.config.timezone)), deep=True
            )
        self.maintain()
        more = wants_more(question)
        previous = self.sessions.get(session_key)
        report = {"scanned": 0, "stored": 0, "channels": 0, "partial": False, "more": more}
        if more and not previous:
            report["no_session"] = True
            return plan, report
        state = None
        if more:
            state = deepcopy(previous)
            plan = state.plan
            month_only = "เฉพาะเดือนนั้น" in question
            question = state.question
            if month_only:
                anchor = plan.after or state.anchor
                if anchor is None:
                    report["no_session"] = True
                    return plan, report
                first = anchor.astimezone(ZoneInfo(self.config.timezone)).replace(
                    day=1, hour=0, minute=0, second=0, microsecond=0
                )
                next_month = (first + timedelta(days=32)).replace(day=1)
                plan = HistoryPlan(first, next_month, deep=True)
                state.plan = plan
                state.cursors.clear()
                state.exhausted.clear()
        elif session_key and plan.deep:
            state = SearchSession(session_key, question, plan)
        if not self.config.auto_history:
            return plan, report
        selected = requested_channels(question, channels)
        ordered = sorted(channels, key=lambda c: (c.id != destination, c.id))
        if selected:
            ordered = [c for c in ordered if c.id in selected]
        if state:
            permitted = {c.id for c in ordered}
            state.cursors = {cid: mid for cid, mid in state.cursors.items() if cid in permitted}
            state.exhausted &= permitted
            ordered = [c for c in ordered if c.id not in state.exhausted]
        total = self.config.history_total
        page_ids = []

        async def load():
            nonlocal total
            for channel in ordered[: self.config.history_channels]:
                if not active() or total <= 0:
                    report["partial"] = True
                    return
                key = (guild, channel.id, plan.after, plan.before, plan.oldest_first, plan.deep)
                if not state and time.monotonic() - self.refreshed.get(key, -1e9) < self.config.history_cache:
                    continue
                limit = min(total, self.config.history_batch if plan.deep else 30)
                kwargs = {"limit": limit, "oldest_first": plan.oldest_first}
                if plan.after:
                    kwargs["after"] = plan.after
                if plan.before:
                    kwargs["before"] = plan.before
                if state and channel.id in state.cursors:
                    kwargs["after" if plan.oldest_first else "before"] = discord.Object(
                        state.cursors[channel.id]
                    )
                scanned = 0
                try:
                    async for message in channel.history(**kwargs):
                        if not active():
                            report["partial"] = True
                            return
                        scanned += 1
                        if state:
                            state.cursors[channel.id] = message.id
                            state.anchor = message.created_at
                        total -= 1
                        report["scanned"] += 1
                        if not message.author.bot and not message.webhook_id and message.content:
                            if not self.store.user(guild, message.author.id)["optout"]:
                                self.store.put_message(
                                    message.id,
                                    guild,
                                    channel.id,
                                    message.author.id,
                                    message.content,
                                    message.created_at.timestamp(),
                                )
                                report["stored"] += 1
                                page_ids.append(message.id)
                    self.refreshed[key] = time.monotonic()
                    report["channels"] += 1
                    report["partial"] |= scanned >= limit
                    if state and scanned < limit:
                        state.exhausted.add(channel.id)
                except (discord.Forbidden, discord.NotFound):
                    report["partial"] = True
            if len(ordered) > self.config.history_channels:
                report["partial"] = True

        try:
            await asyncio.wait_for(load(), timeout=self.config.history_timeout)
        except (asyncio.TimeoutError, discord.HTTPException):
            report["partial"] = True
        if len(self.refreshed) > 1000:
            cutoff = time.monotonic() - self.config.history_cache
            self.refreshed = {key: value for key, value in self.refreshed.items() if value > cutoff}
        if state:
            state.touched = time.monotonic()
            report["exhausted"] = all(c.id in state.exhausted for c in ordered)
            plan = replace(plan, message_ids=tuple(page_ids), session=state)
        return plan, report
