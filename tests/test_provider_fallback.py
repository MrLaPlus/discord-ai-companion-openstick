import asyncio
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from bot.config import Config
from bot.provider import Provider, ProviderError
from bot.quota import Quota, QuotaExceeded
from bot.storage import Store
from tests.test_provider import Response, Session

class SequenceSession(Session):
    def __init__(self, *responses):
        super().__init__(); self.responses = list(responses)
    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        value = self.responses.pop(0)
        if isinstance(value, Exception): raise value
        return value

class FallbackTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.store = Store(Path(self.tmp.name)/'db'); self.addCleanup(self.store.close)
        self.config = replace(Config('discord','groq'), openrouter_key='router')
        self.quota = Quota(self.store,self.config)
    async def test_gemma_success(self):
        s=SequenceSession(Response(data={'choices':[{'message':{'content':'สวัสดี'}}], 'usage':{'prompt_tokens':5,'completion_tokens':4,'total_tokens':9}}))
        answer=await Provider(self.config,self.quota,s).chat([{'role':'user','content':'hello'}])
        self.assertEqual(answer.text,'สวัสดี');self.assertEqual(answer.notice,'')
        self.assertEqual(s.calls[0][1]['json']['model'],'google/gemma-4-31b-it:free')
        self.assertNotIn('reasoning_format',s.calls[0][1]['json'])
        self.assertEqual(self.store.db.execute("select prompt_tokens from usage_daily where provider='openrouter'").fetchone()[0],5)
    async def test_429_falls_back_and_pause_is_independent(self):
        s=SequenceSession(Response(status=429), Response(status=429), Response(), Response())
        provider=Provider(self.config,self.quota,s)
        answer=await provider.chat([{'role':'user','content':'hello'}])
        self.assertIn('429',answer.notice);self.assertEqual(len(s.calls),3)
        answer=await provider.chat([{'role':'user','content':'hello'}])
        self.assertEqual(len(s.calls),4);self.assertIn('Groq',answer.notice)
        self.assertIn('api.groq.com',s.calls[-1][0])
    async def test_both_fail_and_unsafe_error_not_exposed(self):
        s=SequenceSession(Response(status=503),Response(status=503),Response(status=401))
        with self.assertRaises(ProviderError) as err:
            await Provider(self.config,self.quota,s).chat([{'role':'user','content':'hello'}])
        self.assertIn('503',str(err.exception));self.assertIn('401',str(err.exception));self.assertNotIn('router',str(err.exception).lower().replace('openrouter',''))
    async def test_image_sent_to_both_on_timeout(self):
        s=SequenceSession(asyncio.TimeoutError(),Response(status=503),Response())
        answer=await Provider(self.config,self.quota,s).chat([{'role':'user','content':'look'}],image_bytes=b'\xff\xd8\xffa')
        self.assertIn('หมดเวลา',answer.notice)
        self.assertEqual(s.calls[0][1]['json']['messages'][0]['content'][1]['type'],'image_url')
        self.assertEqual(s.calls[1][1]['json']['messages'][0]['content'][1]['type'],'image_url')
        self.assertEqual(s.calls[2][1]['json']['messages'][0]['content'][1]['type'],'image_url')

    async def test_malformed_provider_response_falls_back(self):
        s=SequenceSession(Response(data={'choices':[{'message':{'content':None}}]}), Response())
        answer=await Provider(self.config,self.quota,s).chat([{'role':'user','content':'hello'}])
        self.assertIn('ตอบกลับไม่สมบูรณ์',answer.notice)
        self.assertEqual(len(s.calls),2)

    async def test_groq_local_quota_error_is_explained_in_thai(self):
        s = SequenceSession(Response(status=429),Response(status=429))
        self.quota.reserve = lambda *args, **kwargs: (_ for _ in ()).throw(QuotaExceeded(12))
        with self.assertRaises(ProviderError) as err:
            await Provider(self.config,self.quota,s).chat([{'role':'user','content':'hello'}])
        self.assertIn('429',str(err.exception))
        self.assertIn('โควตาภายใน',str(err.exception))
        self.assertIn('12 วินาที',str(err.exception))
        self.assertNotIn('Please wait',str(err.exception))

    async def test_secondary_model_success(self):
        s = SequenceSession(Response(status=429), Response())
        answer = await Provider(self.config,self.quota,s).chat([{'role':'user','content':'hello'}])
        self.assertEqual(s.calls[1][1]['json']['model'],'dots-studio/dots-3-note-preview:free')
        self.assertIn('Dots3-Note',answer.notice)
        self.assertIn('429',answer.notice)
