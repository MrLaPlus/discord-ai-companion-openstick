"""Bounded Groq speech-to-text; audio stays in memory and is never logged."""
from __future__ import annotations

import asyncio
import json
import re
import time

import aiohttp

from .provider import ProviderError

MAX_AUDIO = 2 * 1024 * 1024
MAX_SECONDS = 120


def voice_needs_confirmation(text):
    from .schedule import is_scheduling_request
    from .schedule_edit import is_schedule_edit
    return (is_scheduling_request(text) or is_schedule_edit(text) or
            bool(re.search(r'^\s*/|(?:เตือน|ปลุก|ตั้งเวลา|ยกเลิก|ลบ|จำไว้|ช่วยจำ|อย่าจำ|ตั้งค่า|เปลี่ยนโหมด|หยุดแซว)', text, re.I)))


class Speech:
    def __init__(self, config, session, store):
        self.config, self.session, self.store = config, session, store
        self.lock = asyncio.Lock()
        self.uses = {}

    async def transcribe(self, audio, duration, user):
        if not isinstance(audio, bytes) or not audio.startswith(b'OggS') or len(audio) > MAX_AUDIO or not 0 < duration <= MAX_SECONDS:
            raise ProviderError('เสียงไม่รองรับ หรือเกิน 2 นาที / 2 MiB ครับ')
        # Dedicated, content-free request cap; token quota does not measure audio.
        now = time.monotonic()
        uses = [t for t in self.uses.get(user, ()) if now - 3600 < t <= now]
        if len(uses) >= 20:
            raise ProviderError('ใช้งานถอดเสียงครบ 20 ครั้งในชั่วโมงนี้แล้วครับ')
        self.uses[user] = uses + [now]
        form = aiohttp.FormData()
        form.add_field('model', 'whisper-large-v3-turbo')
        form.add_field('response_format', 'json')
        form.add_field('file', audio, filename='voice.ogg', content_type='audio/ogg')
        attempted = False
        success = False
        try:
            async with self.lock:
                attempted = True
                async with self.session.post('https://api.groq.com/openai/v1/audio/transcriptions', data=form,
                    headers={'Authorization': 'Bearer ' + self.config.api_key},
                    timeout=aiohttp.ClientTimeout(total=35), allow_redirects=False) as response:
                    if response.status == 429:
                        raise ProviderError('บริการถอดเสียงติดโควตา รอสักครู่แล้วลองใหม่ครับ')
                    if response.status != 200:
                        raise ProviderError('บริการถอดเสียงใช้งานไม่ได้ ลองใหม่ภายหลังครับ')
                    raw = bytearray()
                    async for chunk in response.content.iter_chunked(8192):
                        raw.extend(chunk)
                        if len(raw) > 65536:
                            raise ProviderError('ผลถอดเสียงยาวเกินกำหนดครับ')
                    data = json.loads(raw)
                    text = data.get('text') if isinstance(data, dict) else None
                    if not isinstance(text, str) or not text.strip() or len(text) > 4000:
                        raise ProviderError('ไม่ได้ยินข้อความชัดเจน ลองส่งเสียงใหม่ครับ')
                    success = True
                    return text.strip()
        except asyncio.CancelledError:
            raise
        except ProviderError:
            raise
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError, TypeError):
            raise ProviderError('เชื่อมต่อบริการถอดเสียงไม่สำเร็จครับ') from None
        finally:
            if attempted:
                from .usage_stats import record
                record(self.store, 'whisper', success=success, audio_seconds=duration,
                       zone=getattr(self.config, 'timezone', 'Asia/Bangkok'))
