"""Small, platform-specific capability descriptions, never a tool execution."""
from __future__ import annotations

import re


def schedule_is_ready(bot) -> bool:
    task = getattr(bot, "scheduler_task", None)
    scheduler = getattr(bot, "scheduler", None)
    return bool(task is not None and not task.done() and scheduler is not None and scheduler.clock_ok)


def capability_question(text: str) -> bool:
    text = text.strip()
    if len(text) > 160 or re.search(r'https?://', text, re.I):
        return False
    if text.lower() in {'/features', '/help'}:
        return True
    subject = r'(?:(?:บอท|bot|เธอ|นาย|คุณ|มึง|แก|ai)\s*)?'
    generic = (
        subject + r'(?:ทำ(?:อะไร|ไร)ได้(?:บ้าง)?|มี(?:ฟีเจอร์|ฟังก์ชัน|ฟังก์ชั่น|ความสามารถ|เครื่องมือ)อะไร(?:บ้าง)?|'
        r'มีอะไร(?:บ้าง|ให้ใช้(?:บ้าง)?)|ช่วยอะไรได้(?:บ้าง)?|ใช้ทำอะไรได้(?:บ้าง)?)'
    )
    specific = (
        subject + r'(?:ส่งรูป|ดูรูป|อ่านรูป|วิเคราะห์รูป|ตั้งเตือน|ปลุก|ดูเวลา|อ่านลิงก์|อ่านลิงค์|'
        r'ค้นเว็บ|ค้นหาข้อมูล|จำข้อมูล|รายงานข่าว)(?:ได้ไหม|ได้มั้ย|ได้หรือเปล่า|ทำได้ไหม|ทำได้มั้ย)'
    )
    return bool(re.fullmatch(r'(?:ตอนนี้\s*)?(?:' + generic + '|' + specific + r')(?:ครับ|ค่ะ|คะ|บ้าง)?[\s?!？.]*', text, re.I))


def capability_prompt(platform: str, *, web_enabled: bool, storage_allowed: bool = True, vision_enabled: bool = True,
                      fetch_enabled: bool | None = None, memory_enabled: bool = True,
                      scheduler_ready: bool = True) -> str:
    common = ('Features are platform commands, not actions you have already performed. '
              'Never claim to have searched, set a reminder, stored memory or changed settings '
              'unless the application confirms it. ')
    if platform == 'telegram':
        common += ('Telegram: /time, /tone 0-5, /status, /progress on or off, /summary, /clear, and voice transcription using Whisper. '
                   + ('Supports one JPEG photo. ' if vision_enabled else 'Image support is unconfirmed. '))
    else:
        common += 'Discord: /time, /tone 0-4, /progress and /help. Images are unsupported. '
    common += ('Reminders are enabled. ' if storage_allowed and scheduler_ready else 'Reminders are unavailable. ')
    if not storage_allowed:
        common += 'Memory is disabled for this user. '
    if storage_allowed:
        common += 'Memory can be saved here. ' if memory_enabled else 'Saving memory is unavailable in this room. '
    if fetch_enabled is None:
        fetch_enabled = web_enabled
    common += 'Search is configured. ' if web_enabled else 'Search is unavailable here. '
    return common + ('Public HTTPS reading is configured.' if fetch_enabled else 'Public HTTPS reading is unavailable here.')


def capability_text(platform: str, *, web_enabled: bool, storage_allowed: bool = True, vision_enabled: bool = True,
                    fetch_enabled: bool | None = None, memory_enabled: bool = True,
                    scheduler_ready: bool = True) -> str:
    if platform not in {'telegram', 'discord'}:
        raise ValueError('Unknown platform')
    lines = ['ตอนนี้ผมช่วยได้ดังนี้ครับ']
    lines.append('• คุย ตอบคำถาม และดูวันเวลา: /time หรือถาม “ตอนนี้กี่โมง”')
    lines.append('• /progress เปิดหรือปิดแจ้งความคืบหน้า (ค่าเริ่มต้นเปิด)')
    if fetch_enabled is None:
        fetch_enabled = web_enabled
    lines.append('• ค้นเว็บ: Tavily เมื่อมีคีย์ และ DuckDuckGo สำรอง; การใช้งานขึ้นกับการเชื่อมต่อและโควตา' if web_enabled
                 else '• ค้นเว็บ: ยังไม่ได้เปิดใช้หรือคุณไม่มีสิทธิ์ในห้องนี้')
    lines.append('• อ่านหน้าเว็บ HTTPS สาธารณะครั้งละหนึ่งลิงก์; เว็บที่ต้องล็อกอินหรือ JavaScript อาจอ่านไม่ได้' if fetch_enabled
                 else '• อ่านหน้าเว็บ HTTPS: ยังไม่ได้เปิดใช้หรือคุณไม่มีสิทธิ์ในห้องนี้')
    if web_enabled:
        lines.append('• ตรวจข่าว: “เช็กข่าว” ตามด้วยข้อกล่าวอ้างสาธารณะหรือลิงก์; สรุปพร้อมหลักฐานและข้อจำกัด')
    if storage_allowed:
        if scheduler_ready:
            lines.append('• เตือนและรายงานข่าวตามเวลา: เช่น “เตือนทุกวัน 07:00 กินข้าว”; ดูรายการด้วย /schedules')
            lines.append('• เลื่อนเวลา: “เลื่อนเตือนกินยาเป็น 8 โมง”; ข้ามข่าว: “ข้ามรายงานข่าว AI วันเสาร์นี้”')
            lines.append('• รายงานข่าวตามเวลาต้องเปิดค้นเว็บ' if not web_enabled else '• รายงานข่าว: “รายงานข่าว AI ทุกวัน 07:00”')
        else:
            lines.append('• ตัวตั้งเวลาไม่พร้อมใช้งานหรือยังตรวจสอบเวลาไม่สำเร็จ')
        if memory_enabled:
            lines.append('• จำข้อมูลที่สั่งและลบได้: /remember /memory /forget')
        else:
            lines.append('• ดูหรือลบความจำได้ด้วย /memory และ /forget; การสั่งจำในห้องนี้ต้องให้แอดมินเปิดอ่านห้องก่อน')
    else:
        lines.append('• ความจำและการตั้งเวลาปิดอยู่ เพราะคุณปิดการเก็บข้อมูล')
    if platform == 'telegram':
        lines.append('• ดูรูป: ส่งรูป JPEG ไม่เกิน 1 MiB พร้อมคำถาม ภาพจะส่งให้ Groq' if vision_enabled else '• ดูรูป: โมเดลที่ตั้งไว้ยังไม่ยืนยันว่ารองรับภาพ')
        lines.append('• /tone 0–5 (5 = ซึน), /status ดูบอร์ด, /cancel รหัส ยกเลิกเตือน')
        lines.append('• /summary สรุปแชตที่เก็บไม่เกิน 7 วัน และ /clear ลบประวัติสนทนา' if storage_allowed else '• /summary ใช้ไม่ได้ขณะปิดการเก็บข้อมูล; /clear ลบประวัติสนทนา')
        lines.append('• ส่งข้อความเสียงได้ไม่เกิน 2 นาที / 2 MiB; /transcript on หรือ off เปิด–ปิดข้อความถอดเสียง')
        lines.append('คุยได้เฉพาะบัญชีที่อนุญาตในแชตส่วนตัว; ยังไม่รองรับกลุ่มหรือวิดีโอ')
    else:
        lines.append('• /tone 0–4' + (', /schedule ตั้งงานในห้องนี้, /cancel_schedule ยกเลิกงาน' if storage_allowed and scheduler_ready else ''))
        lines.append('• /catchup สรุปประวัติที่มีสิทธิ์ใช้; /status และการตั้งค่าบางอย่างสำหรับแอดมิน')
        lines.append('Discord รุ่นนี้ยังไม่รองรับอ่านรูป ไฟล์แนบ เสียง หรือวิดีโอ')
    lines.append('งานที่สั่งต้องรอข้อความยืนยันก่อน และการแจ้งเตือนขึ้นกับเครื่องและเครือข่าย')
    return '\n'.join(lines)
