"""Small deterministic image-search intent and safe query extraction."""
import re

from .web_tools import needs_query_confirmation, ToolError


def image_query(text):
    text = text.strip()
    match = re.match(r'^(?:ค้นหา|ค้น|หา|ส่ง|ขอ)(?:รูปภาพ|รูป|ภาพ)\s*(.+)$', text, re.I)
    if not match:
        return None
    query = re.sub(r'(?:สิ)?(?:เอามา)?สัก(?:หนึ่ง|1)?รูป(?:หน่อย)?[\s.!?]*$', '', match[1]).strip()
    query = re.sub(r'(?:ให้หน่อย|หน่อย|สิ)[\s.!?]*$', '', query).strip()
    if not query or len(query) > 300 or needs_query_confirmation(query):
        raise ToolError('ระบุคำค้นรูปสาธารณะให้ชัด โดยไม่ใส่ข้อมูลส่วนตัวครับ')
    return query
