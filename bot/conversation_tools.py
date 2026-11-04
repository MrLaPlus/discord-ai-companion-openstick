"""Scoped conversational repairs and explicit local-time catch-up windows."""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

CORRECTION = re.compile(
    r"^(?:หมายถึง|ที่หมายถึงคือ|ที่บอกคือ|เข้าใจผิด|ฉันหมายถึง|ผมหมายถึง|กูหมายถึง|i meant\b|ไม่ใช่.{1,80}(?:หมายถึง|ที่บอก))",
    re.I,
)
CATCHUP = re.compile(
    r"พลาดอะไร|สรุปช่วงที่ไม่อยู่|สรุป(?:แชท|บทสนทนา|ที่คุย)|ตอน(?:กู|ฉัน|ผม)?ไม่อยู่.*(?:เกิด|คุย)|while.*away", re.I
)


def repair_question(question, turns, reply_id=None):
    if not CORRECTION.search(question):
        return None
    turn = (
        next((t for t in reversed(turns) if reply_id in t.message_ids), None)
        if isinstance(reply_id, int)
        else (turns[-1] if turns else None)
    )
    if turn is None:
        return ""
    previous = turn.question.encode()[:600].decode(errors="ignore")
    return previous + "\nคำแก้ความเข้าใจล่าสุดจากผู้ถาม: " + question


def catchup_start(question, timezone, now=None):
    if not CATCHUP.search(question):
        return None
    if "คำแก้ความเข้าใจล่าสุดจากผู้ถาม:" in question:
        correction = question.rsplit("คำแก้ความเข้าใจล่าสุดจากผู้ถาม:", 1)[-1]
        if re.search(r"เมื่อคืน|เมื่อวาน|เช้านี้|วันนี้|ชั่วโมง|\d{4}-\d{2}-\d{2}|\d{1,2}:\d{2}", correction):
            question = correction
    now = (now or datetime.now(ZoneInfo(timezone))).astimezone(ZoneInfo(timezone))
    day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    hours = re.search(r"(?<!\d)(\d{1,4})\s*ชั่วโมง", question)
    if hours:
        count = int(hours[1])
        if not 1 <= count <= 168:
            raise ValueError("สรุปย้อนหลังได้ 1–168 ชั่วโมงครับ")
        return (now - timedelta(hours=count)).timestamp()
    date = re.search(r"(\d{4}-\d{2}-\d{2})", question)
    start = None
    if date:
        try:
            start = datetime.fromisoformat(date[1]).replace(tzinfo=now.tzinfo)
        except ValueError:
            raise ValueError("วันที่ไม่ถูกต้อง ใช้ YYYY-MM-DD ครับ") from None
    clock = re.search(r"(?<!\d)(\d{1,2}):(\d{2})(?!\d)", question)
    if clock:
        hour, minute = map(int, clock.groups())
        if hour > 23 or minute > 59:
            raise ValueError("เวลาไม่ถูกต้อง ใช้ HH:MM ครับ")
        base = start or (day - timedelta(days=1) if "เมื่อวาน" in question or "เมื่อคืน" in question else day)
        start = base.replace(hour=hour, minute=minute)
        if start > now and not date and "วันนี้" not in question:
            start -= timedelta(days=1)
    if start is not None:
        if start > now or now - start > timedelta(days=3650):
            raise ValueError("ช่วงเวลาอยู่นอกขอบเขตครับ")
        return start.timestamp()
    if "เมื่อคืน" in question or "เมื่อวานเย็น" in question:
        return (day - timedelta(days=1) + timedelta(hours=18)).timestamp()
    if "เมื่อวาน" in question:
        return (day - timedelta(days=1)).timestamp()
    if "เช้านี้" in question:
        return (day + timedelta(hours=6)).timestamp() if now.hour >= 6 else day.timestamp()
    if "วันนี้" in question:
        return day.timestamp()
    if re.search(r"เดือนที่แล้ว|เดือนก่อน|ปีที่แล้ว", question):
        from .history import plan_history

        plan = plan_history(question, timezone, now=now)
        if plan.after:
            return plan.after.timestamp()
    return (now - timedelta(hours=24)).timestamp()


def catchup_heading(since, timezone, now=None):
    zone = ZoneInfo(timezone)
    start = datetime.fromtimestamp(since, zone).strftime("%d/%m/%Y %H:%M")
    end = (now or datetime.now(zone)).astimezone(zone).strftime("%d/%m/%Y %H:%M")
    return f"สรุปช่วง {start}–{end} ({timezone}; ตัวอย่างประวัติที่อ่านได้)"
