import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock
from types import SimpleNamespace

from bot.config import Config
from bot.provider import ProviderError
from bot.speech import Speech, voice_needs_confirmation
from bot.storage import Store
from tests.test_provider import Response, Session


class SpeechTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(Path(self.tmp.name) / 'db')
        self.addCleanup(self.store.close)

    async def test_transcribe_uses_exact_model_and_bounded_ogg(self):
        session = Session(Response(data={'text': 'สวัสดีครับ'}))
        speech = Speech(Config('token', 'key'), session, self.store)
        self.assertEqual(await speech.transcribe(b'OggSsample', 5, 10), 'สวัสดีครับ')
        url, kwargs = session.calls[0]
        self.assertEqual(url, 'https://api.groq.com/openai/v1/audio/transcriptions')
        self.assertFalse(kwargs['allow_redirects'])
        fields = kwargs['data']._fields
        self.assertTrue(any(field[2] == 'whisper-large-v3-turbo' for field in fields))
        with self.assertRaises(ProviderError):
            await speech.transcribe(b'not ogg', 5, 10)
        with self.assertRaises(ProviderError):
            await speech.transcribe(b'OggS', 121, 10)
        self.assertEqual(len(session.calls), 1)

    async def test_provider_error_and_per_user_audio_cap(self):
        session = Session(Response(status=429))
        speech = Speech(Config('token', 'key'), session, self.store)
        with self.assertRaises(ProviderError):
            await speech.transcribe(b'OggSsample', 5, 10)
        speech.uses[10] = [__import__('time').monotonic()] * 20
        with self.assertRaises(ProviderError):
            await speech.transcribe(b'OggSsample', 5, 10)
        self.assertEqual(len(session.calls), 1)

    def test_mutation_intents_require_confirmation(self):
        for text in ('เตือนทุกวัน 07:00 กินยา', 'เลื่อนเตือนกินยาเป็น 8 โมง', '/clear', 'อย่าจำฉัน'):
            self.assertTrue(voice_needs_confirmation(text), text)
        self.assertFalse(voice_needs_confirmation('สวัสดี วันนี้อากาศเป็นไง'))
