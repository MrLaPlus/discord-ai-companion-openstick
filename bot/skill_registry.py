"""Explicit built-in bot workflows; never execute code supplied by users."""
import re


def skill_list():
    return ('ทักษะที่ใช้ได้:\n'
            '• ข่าว — ค้นข่าวสาธารณะและสรุปพร้อมแหล่งข้อมูล\n'
            '• ตรวจข่าว — ตรวจข้อกล่าวอ้างสาธารณะหรือลิงก์\n'
            '• github — ดู release ล่าสุดของ public repository\n'
            'ใช้ /skill ข่าว หัวข้อ, /skill ตรวจข่าว ข้อกล่าวอ้าง หรือ /skill github owner/repo')


def invoke_skill(name, value):
    key = name.strip().lower()
    topic = value.strip()
    if key not in {'ข่าว', 'news', 'ตรวจข่าว', 'factcheck', 'github'}:
        raise ValueError('ไม่พบทักษะนี้ ใช้ /skills เพื่อดูรายการครับ')
    if not topic or len(topic) > 300 or re.search(r'[\r\n]', topic):
        raise ValueError('ระบุหัวข้อสั้น ๆ ไม่เกิน 300 ตัวอักษรครับ')
    if key == 'github':
        from .github_public import clean_repo
        return '/github_release ' + clean_repo(topic)
    return ('เช็กข่าว ' if key in {'ตรวจข่าว', 'factcheck'} else 'ค้นเว็บ ข่าว ') + topic


def parse_skill_command(text):
    if text.strip() == '/skills':
        return ('list', '')
    match = re.fullmatch(r'/skill\s+(ข่าว|news|ตรวจข่าว|factcheck|github)\s+(.+)', text.strip(), re.I)
    if match:
        return ('invoke', invoke_skill(match[1], match[2]))
    if text.strip().startswith('/skill '):
        raise ValueError('ใช้ /skills ดูรายการ หรือ /skill ข่าว ตามด้วยหัวข้อครับ')
    return None
