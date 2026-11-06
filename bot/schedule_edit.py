"""Owner-scoped schedule edits with restore-safe retirement of the old job."""
from __future__ import annotations

import json
import re
import time
import uuid
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .privacy import cancel_schedule


def is_schedule_edit(text):
    return bool(re.match(r'^\s*(?:เลื่อนเตือน|เลื่อนรายงานข่าว|หยุดรายงานข่าว|ข้ามรายงานข่าว)', text))


def _matching_jobs(store, platform, guild, owner, kind, topic):
    selected = re.search(r'\s*รหัส\s+([0-9a-fA-F]{8,32})$', topic)
    if selected:
        topic = topic[:selected.start()].strip()
    return [dict(row) for row in store.db.execute("SELECT * FROM scheduled_jobs WHERE platform=? AND guild=? AND owner=? AND status='active' AND kind=? ORDER BY created", (platform, guild, owner, kind)) if topic and topic.casefold() in row['body'].casefold() and (not selected or row['id'].startswith(selected[1].lower()))]


def _multiple_jobs(rows):
    items = ', '.join(f"{row['id'][:8]} ({row['body']})" for row in rows)
    return 'พบหลายรายการ กรุณาระบุรหัสต่อท้ายหัวข้อ เช่น เลื่อนเตือนกินยา รหัส abcdef12 เป็น 08:00: ' + items


def edit_reply(store, platform, guild, channel, owner, text, zone, now=None):
    text = text.strip()
    if not is_schedule_edit(text):
        return None
    if text.startswith(('หยุดรายงานข่าว', 'ข้ามรายงานข่าว')):
        skip = re.fullmatch(r'(หยุด|ข้าม)รายงานข่าว\s*(.+?)\s*(ทุกวันเสาร์|วันเสาร์นี้)', text)
        if not skip or (skip[1], skip[3]) not in {('หยุด', 'ทุกวันเสาร์'), ('ข้าม', 'วันเสาร์นี้')}:
            return 'ต้องการข้ามเฉพาะวันเสาร์ครั้งนี้ หรือหยุดทุกวันเสาร์ครับ? ใช้ “ข้ามรายงานข่าว AI วันเสาร์นี้” หรือ “หยุดรายงานข่าว AI ทุกวันเสาร์”'
        topic = skip[2].strip()
        rows = _matching_jobs(store, platform, guild, owner, 'news', topic)
        if not rows:
            return 'ไม่พบรายการตั้งเวลาของคุณที่ตรงกันครับ'
        if len(rows) > 1:
            return _multiple_jobs(rows)
        row = rows[0]
        if store.user(guild, owner)['optout']:
            return 'การเก็บข้อมูลของคุณปิดอยู่ จึงแก้รายการไม่ได้ครับ'
        if not row['daily']:
            return 'รายการนี้ไม่ใช่งานรายวันหรือรายสัปดาห์ กรุณาใช้ /cancel ครับ'
        if skip[3] == 'ทุกวันเสาร์' and row['weekday'] is not None:
            return 'รายการรายสัปดาห์ตั้งไว้วันเดียว หากต้องการหยุดรายการให้ใช้ /cancel ครับ'
        now = time.time() if now is None else now
        current = datetime.fromtimestamp(now, ZoneInfo(zone))
        saturday = current.date() + timedelta(days=(5 - current.weekday()) % 7)
        if skip[3] == 'วันเสาร์นี้' and row['weekday'] not in (None, 5):
            return 'รายการนี้ไม่ได้กำหนดส่งวันเสาร์ครับ'
        if skip[3] == 'วันเสาร์นี้' and datetime.fromtimestamp(row['next_at'], ZoneInfo(zone)).date() > saturday:
            return 'ไม่มีรอบวันเสาร์นี้ที่ยังมาไม่ถึงครับ'
        policy = {'weekday': 5} if skip[3] == 'ทุกวันเสาร์' else {'date': saturday.isoformat()}
        try:
            new_id = _replace_job(store, row, row['next_at'], now, policy)
        except ValueError as error:
            return str(error)
        return ('ตั้งให้ข้ามทุกวันเสาร์แล้ว' if 'weekday' in policy else f"ตั้งให้ข้ามวันเสาร์ {saturday:%d/%m/%Y} แล้ว") + f' · หมายเลขใหม่ {new_id[:8]}'
    match = re.fullmatch(r'เลื่อน(เตือน|รายงานข่าว)(.+?)เป็น\s*(?:(\d{1,2}):(\d{2})|(\d{1,2})\s*โมง(?:เช้า)?)\s*', text)
    if not match:
        return 'ระบุรายการและเวลาให้ชัด เช่น เลื่อนเตือนกินยาเป็น 08:00 ครับ'
    kind = 'reminder' if match[1] == 'เตือน' else 'news'
    topic = match[2].strip().removeprefix('ฉัน').strip()
    hour, minute = (int(match[3]), int(match[4])) if match[3] else (int(match[5]), 0)
    if not topic or hour > 23 or minute > 59 or (match[5] and hour > 11):
        return 'ระบุรายการและเวลาแบบ 00:00–23:59 ครับ'
    if store.user(guild, owner)['optout']:
        return 'การเก็บข้อมูลของคุณปิดอยู่ จึงแก้รายการไม่ได้ครับ'
    rows = _matching_jobs(store, platform, guild, owner, kind, topic)
    if not rows:
        return 'ไม่พบรายการตั้งเวลาของคุณที่ตรงกันครับ'
    if len(rows) > 1:
        return _multiple_jobs(rows)
    row = rows[0]
    now = time.time() if now is None else now
    current = datetime.fromtimestamp(now, ZoneInfo(zone))
    target = datetime.fromtimestamp(row['next_at'], ZoneInfo(zone)).replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= current:
        if not row['daily']:
            return 'เวลาใหม่ผ่านไปแล้ว กรุณาระบุเวลาที่ยังมาไม่ถึงครับ'
        step = 7 if row['weekday'] is not None else 1
        while target <= current:
            target += timedelta(days=step)
    policy = store.meta(f"schedule:skip:{row['id']}", {})
    try:
        new_id = _replace_job(store, row, target.timestamp(), now, policy)
    except ValueError as error:
        return str(error)
    return f"เลื่อนแล้วเป็น {target:%d/%m/%Y %H:%M} ({zone}) · หมายเลขใหม่ {new_id[:8]}"


def _replace_job(store, row, next_at, now, policy):
    new_id = uuid.uuid4().hex
    store.db.execute('BEGIN IMMEDIATE')
    retired = False
    try:
        live = store.db.execute("SELECT status,next_at FROM scheduled_jobs WHERE id=?", (row['id'],)).fetchone()
        if not live or live['status'] != 'active' or live['next_at'] != row['next_at']:
            raise ValueError('รายการเปลี่ยนระหว่างทำงาน กรุณาลองใหม่ครับ')
        cancel_schedule(store.privacy_folder, row['id'])
        retired = True
        store.db.execute('DELETE FROM scheduled_jobs WHERE id=?', (row['id'],))
        store.db.execute("DELETE FROM metadata WHERE key=?", (f"schedule:skip:{row['id']}",))
        store.db.execute("INSERT INTO scheduled_jobs (id,platform,guild,channel,owner,kind,body,daily,next_at,status,created,last_result,last_due,weekday) VALUES (?,?,?,?,?,?,?,?,?,'active',?,'',NULL,?)", (new_id, row['platform'], row['guild'], row['channel'], row['owner'], row['kind'], row['body'], row['daily'], next_at, now, row['weekday']))
        if policy:
            store.db.execute("INSERT INTO metadata VALUES (?,?)", (f"schedule:skip:{new_id}", json.dumps(policy)))
        store.db.commit()
    except Exception as error:
        store.db.rollback()
        if retired:
            # The durable cancellation cannot be undone. Remove the old live row
            # too, so a failed replacement cannot fire before the next restart.
            with store.db:
                store.db.execute("DELETE FROM scheduled_jobs WHERE id=?", (row['id'],))
            raise ValueError('แก้รายการไม่สำเร็จและยกเลิกรายการเดิมแล้ว กรุณาตั้งใหม่ครับ') from error
        raise
    return new_id
