import tempfile, unittest
from dataclasses import replace
from pathlib import Path
from bot.config import Config
from bot.provider import Provider, ProviderError
from bot.quota import Quota
from bot.storage import Store
from tests.test_provider_fallback import SequenceSession
from tests.test_provider import Response

class TokenMeTests(unittest.IsolatedAsyncioTestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
  self.store=Store(Path(self.tmp.name)/'db');self.addCleanup(self.store.close)
  self.config=replace(Config('discord','groq'),tokenme_key='test-key')
  self.quota=Quota(self.store,self.config)
 async def test_tokenme_primary_no_local_groq_quota(self):
  self.quota.reserve=lambda *a,**k: (_ for _ in ()).throw(AssertionError('incorrect quota'))
  s=SequenceSession(Response(data={'choices':[{'message':{'content':'สวัสดี'}}],'usage':{'prompt_tokens':4,'completion_tokens':3,'total_tokens':7}}))
  result=await Provider(self.config,self.quota,s).chat([{'role':'user','content':'hello'}])
  self.assertEqual(result.text,'สวัสดี');self.assertEqual(s.calls[0][0],'https://api.tokenme.limited/api/v1/chat/completions')
  self.assertEqual(s.calls[0][1]['json']['model'],'deepseek-ai/deepseek-v4-flash-0731')
  self.assertEqual(self.store.db.execute("select prompt_tokens from usage_daily where provider='tokenme'").fetchone()[0],4)
 async def test_failover_and_no_secrets(self):
  s=SequenceSession(Response(status=429),Response())
  result=await Provider(self.config,self.quota,s).chat([{'role':'user','content':'hi'}])
  self.assertIn('429',result.notice);self.assertIn('Groq',result.notice);self.assertNotIn('test-key',result.notice)
  self.assertEqual(len(s.calls),2);self.assertIn('api.groq.com',s.calls[1][0])
 async def test_image_routes_to_groq(self):
  s=SequenceSession(Response())
  result=await Provider(self.config,self.quota,s).chat([{'role':'user','content':'look'}],image_bytes=b'\xff\xd8\xffx')
  self.assertIn('api.groq.com',s.calls[0][0]);self.assertIn('วิเคราะห์ภาพ',result.notice)
 async def test_invalid_response_fallback(self):
  s=SequenceSession(Response(data={'choices':[{'message':{'content':None}}]}),Response())
  result=await Provider(self.config,self.quota,s).chat([{'role':'user','content':'hi'}])
  self.assertIn('ไม่สมบูรณ์',result.notice)
 async def test_select_qwen_and_groq(self):
  from bot.provider import model_choice
  s=SequenceSession(Response())
  token=model_choice.set('qwen')
  try: await Provider(self.config,self.quota,s).chat([{'role':'user','content':'hello'}])
  finally: model_choice.reset(token)
  self.assertEqual(s.calls[0][1]['json']['model'],'unsloth/qwen3.8-27b-nvfp4')
  s=SequenceSession(Response());token=model_choice.set('groq')
  try: await Provider(self.config,self.quota,s).chat([{'role':'user','content':'hello'}])
  finally: model_choice.reset(token)
  self.assertIn('api.groq.com',s.calls[0][0])
 async def test_no_tokenme_key_selection_falls_back_groq(self):
  from bot.provider import model_choice
  s=SequenceSession(Response());token=model_choice.set('qwen')
  try: await Provider(replace(self.config,tokenme_key=''),self.quota,s).chat([{'role':'user','content':'hello'}])
  finally:model_choice.reset(token)
  self.assertIn('api.groq.com',s.calls[0][0])
 async def test_telegram_model_selection_is_scoped(self):
  from unittest.mock import AsyncMock
  from types import SimpleNamespace
  from bot.telegram import Telegram
  from bot.provider import user_model, model_choice
  bot=SimpleNamespace(store=self.store,config=SimpleNamespace(mcp_enabled=True,timezone='Asia/Bangkok'))
  telegram=Telegram(bot,'test',frozenset({123,456}));telegram.send=AsyncMock()
  def msg(uid,text): return {'message':{'from':{'id':uid},'chat':{'id':uid,'type':'private'},'text':text}}
  await telegram.handle(msg(123,'/model qwen'))
  self.assertEqual(user_model(self.store,'tokenme:model:telegram:123'),'qwen')
  self.assertEqual(user_model(self.store,'tokenme:model:telegram:456'),'auto')
  self.assertEqual(model_choice.get(),'auto')
  self.store.erase_user(-1,123)
  self.assertEqual(user_model(self.store,'tokenme:model:telegram:123'),'auto')
 def test_usage_totals_include_tokenme_without_hiding_old_openrouter(self):
  from bot.usage_stats import record,display
  record(self.store,'tokenme',success=True,prompt=4,completion=3)
  record(self.store,'openrouter',success=True,prompt=2,completion=1)
  text=display(self.store)
  self.assertIn('TokenMe 7',text);self.assertIn('OR 3',text);self.assertIn('รวม 10',text)
