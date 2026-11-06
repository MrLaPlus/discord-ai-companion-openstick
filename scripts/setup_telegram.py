#!/usr/bin/env python3
"""Configure one private Telegram user locally without printing the bot token."""
from __future__ import annotations

import getpass
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import tempfile
from urllib.request import Request, urlopen

ENV = Path('/etc/discord-companion-usb.env')


def call(token, method, payload):
    try:
        req = Request(f'https://api.telegram.org/bot{token}/{method}',
                      data=json.dumps(payload).encode(), headers={'Content-Type': 'application/json'})
        with urlopen(req, timeout=35) as response:
            raw = response.read(262145)
        if len(raw) > 262144:
            raise ValueError
        body = json.loads(raw)
        if not isinstance(body, dict) or body.get('ok') is not True:
            raise ValueError
        return body.get('result')
    except Exception:
        raise SystemExit('ติดต่อ Telegram ไม่สำเร็จ ตรวจ token อินเทอร์เน็ต และ webhook แล้วลองใหม่') from None


def main():
    if os.geteuid() != 0:
        raise SystemExit('กรุณารันด้วย sudo')
    token = getpass.getpass('Bot Token จาก BotFather (จะไม่แสดงบนจอ): ').strip()
    if not re.fullmatch(r'[0-9]{5,}:[A-Za-z0-9_-]{20,}', token):
        raise SystemExit('รูปแบบ token ไม่ถูกต้อง')
    info = call(token, 'getMe', {})
    if not isinstance(info, dict) or info.get('is_bot') is not True:
        raise SystemExit('ไม่พบบัญชีบอทจาก token นี้')
    hook = call(token, 'getWebhookInfo', {})
    if not isinstance(hook, dict) or hook.get('url'):
        raise SystemExit('บอทนี้มี webhook อยู่ กรุณาใช้บอทใหม่ที่ยังไม่ได้เชื่อมกับระบบอื่น')
    print('ส่ง /start ให้บอทของคุณใน Telegram ตอนนี้ แล้วรอได้สูงสุด 25 วินาที')
    updates = call(token, 'getUpdates', {'timeout': 25, 'limit': 100, 'allowed_updates': ['message']})
    users = {}
    for item in updates if isinstance(updates, list) else []:
        msg = item.get('message', {}) if isinstance(item, dict) else {}
        chat, sender = msg.get('chat', {}), msg.get('from', {})
        if not isinstance(chat, dict) or not isinstance(sender, dict):
            continue
        uid = sender.get('id')
        if type(uid) is int and uid > 0 and chat.get('type') == 'private' and chat.get('id') == uid and msg.get('text') == '/start':
            name = ''.join(c for c in str(sender.get('first_name', '')) if c.isprintable())[:40]
            users[uid] = name
    if not users:
        raise SystemExit('ยังไม่พบ /start จากแชตส่วนตัว กรุณาส่ง /start และรันคำสั่งใหม่')
    for uid, name in users.items():
        print(f'บัญชีที่พบ: {name} (ID {uid})')
    selected = input('พิมพ์ ID ของบัญชีคุณเพื่ออนุญาต: ').strip()
    if not selected.isascii() or not selected.isdigit() or int(selected) not in users:
        raise SystemExit('ID ไม่ตรงกับบัญชีที่ส่ง /start; ไม่มีการแก้ไขไฟล์')
    try:
        metadata = ENV.lstat()
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != 0:
            raise ValueError
        old = ENV.read_text(encoding='utf-8')
        lines = [line for line in old.splitlines() if not re.match(r'^\s*(?:TELEGRAM_BOT_TOKEN|TELEGRAM_ALLOWED_USER_IDS)\s*=', line)]
        contents = '\n'.join(lines) + f'\nTELEGRAM_BOT_TOKEN={token}\nTELEGRAM_ALLOWED_USER_IDS={selected}\n'
        fd, path = tempfile.mkstemp(prefix='.telegram-config-', dir=ENV.parent)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                f.write(contents)
                f.flush()
                os.fsync(f.fileno())
            os.replace(path, ENV)
        finally:
            if os.path.exists(path):
                os.unlink(path)
    except Exception:
        raise SystemExit('เขียนไฟล์ตั้งค่าไม่สำเร็จ; โปรดตรวจสิทธิ์บนบอร์ด') from None
    result = subprocess.run(['systemctl', 'restart', 'discord-usb-bot.service'], check=False)
    if result.returncode:
        raise SystemExit('บันทึกค่าแล้ว แต่รีสตาร์ตบอทไม่สำเร็จ')
    print('บันทึกค่าและเริ่มบริการแล้ว ลองส่งข้อความถึงบอทใน Telegram ได้เลย')


if __name__ == '__main__':
    main()
