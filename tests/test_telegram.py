import asyncio
import os
import tempfile
from pathlib import Path
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.provider import Answer
from bot.storage import Store
from bot.telegram import Telegram, settings
from bot.web_tools import WebPage, ToolError


class TelegramTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / "db")
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(self.store.close)
        self.bot = SimpleNamespace(
            store=self.store,
            config=SimpleNamespace(mcp_enabled=True, timezone="Asia/Bangkok"),
            provider=SimpleNamespace(chat=AsyncMock(return_value=Answer("คำตอบ", 10))),
            web=SimpleNamespace(call=AsyncMock(return_value=[WebPage("title", "https://example.com", "public facts", "now")])),
        )
        self.telegram = Telegram(self.bot, "token", frozenset({123}))
        self.telegram.send = AsyncMock()

    def message(self, uid=123, chat_type="private", text="สวัสดี"):
        return {"message": {"from": {"id": uid}, "chat": {"id": uid, "type": chat_type}, "text": text}}

    async def test_usage_requires_explicit_administrator(self):
        with patch.dict(os.environ, {'TELEGRAM_USAGE_ADMIN_IDS': '456'}, clear=False):
            await self.telegram.handle(self.message(text='/usage'))
            self.assertIn('ผู้ดูแล', self.telegram.send.await_args.args[1])
        with patch.dict(os.environ, {'TELEGRAM_USAGE_ADMIN_IDS': '123'}, clear=False):
            await self.telegram.handle(self.message(text='/usage detail'))
            self.assertIn('ขาเข้า+ขาออก', self.telegram.send.await_args.args[1])

    async def test_voice_downloader_bounded_and_rejects_invalid_paths(self):
        from tests.test_provider import Chunks
        from bot.provider import ProviderError
        class Response:
            status = 200
            content = Chunks(b'OggSvoice')
            async def __aenter__(self): return self
            async def __aexit__(self, *args): return False
        session = SimpleNamespace(get=lambda *a, **k: Response())
        self.bot.session = session
        self.telegram.api = AsyncMock(return_value={'file_path': 'voice/file_1.oga'})
        audio, seconds = await self.telegram.download_voice({'file_id': 'abc', 'file_size': 9, 'duration': 3})
        self.assertEqual((audio, seconds), (b'OggSvoice', 3))
        self.telegram.api.return_value = {'file_path': '../private.oga'}
        with self.assertRaises(ProviderError):
            await self.telegram.download_voice({'file_id': 'abc', 'file_size': 9, 'duration': 3})
        with self.assertRaises(ProviderError):
            await self.telegram.download_voice({'file_id': 'abc', 'file_size': 3 * 1024 * 1024, 'duration': 3})
        with self.assertRaises(ProviderError):
            await self.telegram.download_voice({'file_id': 'abc', 'file_size': 9, 'duration': 121})

    async def test_voice_trims_old_history_to_fit_provider_ceiling(self):
        from bot.quota import QuotaExceeded
        self.bot.config.tpm = 7000
        self.bot.config.tpd = 180000
        self.bot.config.output_tokens = 768
        for i in range(6):
            self.store.add_telegram_turn(123, 'ก' * 300 + str(i), 'ตอบ' * 300)
        self.telegram.download_voice = AsyncMock(return_value=(b'OggSvoice', 3))
        self.telegram.speech = SimpleNamespace(transcribe=AsyncMock(return_value='สวัสดี'))
        async def chat(messages):
            estimate = sum(len(m['content'].encode()) + 32 for m in messages) + 768
            if estimate > 7000:
                raise QuotaExceeded(60)
            return Answer('ตอบได้', 10)
        self.bot.provider.chat.side_effect = chat
        voice = self.message(text=None)
        voice['message']['voice'] = {'file_id': 'abc', 'file_size': 8, 'duration': 3}
        await self.telegram.handle(voice)
        self.assertIn('ตอบได้', self.telegram.send.await_args.args[1])
        self.assertLess(len(self.bot.provider.chat.await_args.args[0]), 14)
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM telegram_chat_turns').fetchone()[0], 7)

    async def test_voice_quota_retries_only_answer_without_retranscription(self):
        from bot.quota import QuotaExceeded
        self.telegram.download_voice = AsyncMock(return_value=(b'OggSvoice', 3))
        self.telegram.speech = SimpleNamespace(transcribe=AsyncMock(return_value='สวัสดีครับ'))
        self.bot.provider.chat.side_effect = [QuotaExceeded(1), Answer('สวัสดี', 10)]
        voice = self.message(text=None)
        voice['message']['voice'] = {'file_id': 'abc', 'file_size': 8, 'duration': 3}
        with patch('bot.telegram.asyncio.sleep', new=AsyncMock()) as wait:
            await self.telegram.handle(voice)
        self.telegram.speech.transcribe.assert_awaited_once()
        self.assertEqual(self.bot.provider.chat.await_count, 2)
        wait.assert_awaited_once()
        self.assertIn('สวัสดี', self.telegram.send.await_args.args[1])
        self.assertTrue(any('กำลังรอโควตา' in c.args[1] for c in self.telegram.send.await_args_list))

    async def test_voice_quota_retry_does_not_repeat_web_search(self):
        from bot.quota import QuotaExceeded
        self.telegram.download_voice = AsyncMock(return_value=(b'OggSvoice', 3))
        self.telegram.speech = SimpleNamespace(transcribe=AsyncMock(return_value='ค้นเว็บ แมว'))
        self.bot.provider.chat.side_effect = [QuotaExceeded(1), Answer('สรุป', 10)]
        voice = self.message(text=None)
        voice['message']['voice'] = {'file_id': 'abc', 'file_size': 8, 'duration': 3}
        with patch('bot.telegram.asyncio.sleep', new=AsyncMock()):
            await self.telegram.handle(voice)
        self.assertEqual([c.args[0] for c in self.bot.web.call.await_args_list], ['search', 'fetch_content'])
        self.telegram.speech.transcribe.assert_awaited_once()
        self.assertEqual(self.bot.provider.chat.await_count, 2)

    async def test_voice_quota_long_wait_is_thai_and_does_not_retry(self):
        from bot.quota import QuotaExceeded
        self.telegram.download_voice = AsyncMock(return_value=(b'OggSvoice', 3))
        self.telegram.speech = SimpleNamespace(transcribe=AsyncMock(return_value='สวัสดีครับ'))
        self.bot.provider.chat.side_effect = QuotaExceeded(60)
        voice = self.message(text=None)
        voice['message']['voice'] = {'file_id': 'abc', 'file_size': 8, 'duration': 3}
        await self.telegram.handle(voice)
        self.assertIn('ถอดเสียงแล้ว', self.telegram.send.await_args.args[1])
        self.assertIn('วินาที', self.telegram.send.await_args.args[1])
        self.assertNotIn('Please wait', self.telegram.send.await_args.args[1])
        self.bot.provider.chat.assert_awaited_once()

    async def test_voice_failure_log_has_only_stage_and_exception_type(self):
        self.telegram.download_voice = AsyncMock(return_value=(b'OggSvoice', 3))
        self.telegram.speech = SimpleNamespace(transcribe=AsyncMock(side_effect=RuntimeError('SECRET_AUDIO_DATA')))
        voice = self.message(text=None)
        voice['message']['voice'] = {'file_id': 'secret-file-id', 'file_size': 8, 'duration': 3}
        with self.assertLogs('bot.telegram', level='WARNING') as logs:
            await self.telegram.handle(voice)
        self.assertIn('stage=transcribe type=RuntimeError', logs.output[0])
        self.assertNotIn('SECRET_AUDIO_DATA', str(logs.output))
        self.assertNotIn('secret-file-id', str(logs.output))
        self.assertIn('ประมวลผลเสียงไม่สำเร็จ', self.telegram.send.await_args.args[1])

    async def test_voice_full_adapter_pipeline_with_real_speech_class(self):
        from tests.test_provider import Response, Session
        session = Session(Response(data={'text': 'สวัสดีครับ'}))
        self.bot.session = session
        self.bot.config.api_key = 'fake-key'
        self.telegram.download_voice = AsyncMock(return_value=(b'OggSvoice', 3))
        voice = self.message(text=None)
        voice['message']['voice'] = {'file_id': 'abc', 'file_size': 8, 'duration': 3}
        await self.telegram.handle(voice)
        self.assertEqual(self.telegram.send.await_args.args[1], 'คำตอบ')
        self.assertEqual(len(session.calls), 1)
        self.bot.provider.chat.assert_awaited_once()

    async def test_voice_transcript_default_on_off_and_user_isolation(self):
        self.telegram.download_voice = AsyncMock(return_value=(b'OggSvoice', 3))
        self.telegram.speech = SimpleNamespace(transcribe=AsyncMock(return_value='สวัสดีครับ'))
        voice = self.message(text=None)
        voice['message']['voice'] = {'file_id': 'abc', 'file_size': 8, 'duration': 3}
        await self.telegram.handle(voice)
        self.assertTrue(any('ได้ยินว่า: สวัสดีครับ' in c.args[1] for c in self.telegram.send.await_args_list))
        self.bot.provider.chat.assert_awaited_once()
        await self.telegram.handle(self.message(text='/transcript off'))
        self.telegram.send.reset_mock()
        await self.telegram.handle(voice)
        self.assertFalse(any('ได้ยินว่า:' in c.args[1] for c in self.telegram.send.await_args_list))
        self.assertEqual(self.store.user(-1, 456)['transcript_enabled'], 1)
        await self.telegram.handle(self.message(text='/transcript on'))
        self.assertEqual(self.store.user(-1, 123)['transcript_enabled'], 1)

    async def test_voice_schedule_requires_confirmation_even_when_display_off(self):
        self.store.set_user(-1, 123, transcript_enabled=0)
        self.telegram.download_voice = AsyncMock(return_value=(b'OggSvoice', 3))
        self.telegram.speech = SimpleNamespace(transcribe=AsyncMock(return_value='เตือนทุกวัน 07:00 กินยา'))
        voice = self.message(text=None)
        voice['message']['voice'] = {'file_id': 'abc', 'file_size': 8, 'duration': 3}
        await self.telegram.handle(voice)
        self.assertIn('ได้ยินว่า:', self.telegram.send.await_args.args[1])
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM scheduled_jobs').fetchone()[0], 0)
        await self.telegram.handle(self.message(text='/confirmvoice'))
        self.assertIn('ตั้งเวลาแล้ว', self.telegram.send.await_args.args[1])
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM scheduled_jobs').fetchone()[0], 1)
        await self.telegram.handle(self.message(text='/confirmvoice'))
        self.assertIn('ไม่มีคำสั่งเสียง', self.telegram.send.await_args.args[1])

    async def test_unauthorized_voice_never_downloads(self):
        self.telegram.download_voice = AsyncMock()
        voice = self.message(uid=456, text=None)
        voice['message']['voice'] = {'file_id': 'abc', 'file_size': 8, 'duration': 3}
        await self.telegram.handle(voice)
        self.telegram.download_voice.assert_not_awaited()

    async def test_fact_check_routes_to_search_and_reports_sources(self):
        self.bot.web.call.return_value = [WebPage('source', 'https://example.com/story', 'facts', 'now')]
        self.bot.provider.chat.return_value = Answer('หลักฐาน [W1]', 10)
        await self.telegram.handle(self.message(text='เช็กข่าว โลกกลม'))
        self.assertEqual([call.args[0] for call in self.bot.web.call.await_args_list], ['search', 'fetch_content'])
        self.assertIn('https://example.com/story', self.telegram.send.await_args.args[1])
        self.assertIn('เพียงหนึ่งเว็บไซต์', self.telegram.send.await_args.args[1])

    async def test_fact_check_missing_claim_never_sends_web_query(self):
        await self.telegram.handle(self.message(text='เช็กข่าวนี้'))
        self.assertIn('ส่งข้อความข่าว', self.telegram.send.await_args.args[1])
        self.bot.web.call.assert_not_awaited()
        self.bot.provider.chat.assert_not_awaited()

    async def test_image_search_sends_one_image_and_source(self):
        self.bot.web.call.return_value = [WebPage('rimuru', 'https://example.com/source', 'https://example.com/pic.jpg', 'now')]
        self.telegram.api = AsyncMock()
        await self.telegram.handle(self.message(text='ค้นหารูป ริมุรุสิเอามาสักรูป'))
        self.assertEqual(self.bot.web.call.await_args.args[0], 'search_images')
        self.assertEqual(self.bot.web.call.await_args.args[1]['query'], 'ริมุรุ')
        self.assertEqual(self.telegram.api.await_args.args[0], 'sendPhoto')
        self.assertEqual(self.telegram.api.await_args.args[1]['photo'], 'https://example.com/pic.jpg')
        self.assertIn('https://example.com/source', self.telegram.api.await_args.args[1]['caption'])
        self.bot.provider.chat.assert_not_awaited()

    async def test_image_search_private_reference_is_not_sent(self):
        await self.telegram.handle(self.message(text='หารูปคนที่คุยในห้องเมื่อกี้'))
        self.bot.web.call.assert_not_awaited()
        self.assertIn('สาธารณะ', self.telegram.send.await_args.args[1])

    async def test_report_md_sends_document_with_validated_sources(self):
        self.bot.web.call.return_value = [WebPage('source', 'https://example.com/story', 'facts', 'now')]
        self.bot.provider.chat.return_value = Answer('สรุป [W1]', 10)
        self.telegram.send_document = AsyncMock()
        await self.telegram.handle(self.message(text='/report md ข่าว AI'))
        self.assertEqual(self.telegram.send_document.await_args.args[1], 'report.md')
        self.assertIn('https://example.com/story', self.telegram.send_document.await_args.args[2])
        self.assertEqual([c.args[0] for c in self.bot.web.call.await_args_list], ['search', 'fetch_content'])

    async def test_report_private_reference_never_calls_web(self):
        self.telegram.send_document = AsyncMock()
        await self.telegram.handle(self.message(text='/report txt เรื่องที่คุยในห้องเมื่อกี้'))
        self.bot.web.call.assert_not_awaited()
        self.telegram.send_document.assert_not_awaited()

    async def test_github_skill_uses_read_only_public_release(self):
        from tests.test_github_public import Session, Response
        self.bot.web.reserve = __import__('unittest').mock.MagicMock()
        self.bot.session = Session(Response(200, {'tag_name': 'v1', 'html_url': 'https://github.com/owner/repo/releases/tag/v1'}))
        await self.telegram.handle(self.message(text='/skill github owner/repo'))
        self.assertIn('https://github.com/owner/repo/releases/tag/v1', self.telegram.send.await_args.args[1])
        self.bot.web.reserve.assert_called_once_with(0, 123, 'search')
        self.bot.provider.chat.assert_not_awaited()

    async def test_starter_skills_list_and_fact_check_invocation(self):
        await self.telegram.handle(self.message(text='/skills'))
        self.assertIn('ตรวจข่าว', self.telegram.send.await_args.args[1])
        self.bot.web.call.return_value = [WebPage('source', 'https://example.com/story', 'facts', 'now')]
        self.bot.provider.chat.return_value = Answer('หลักฐาน [W1]', 10)
        await self.telegram.handle(self.message(text='/skill ตรวจข่าว โลกกลม'))
        self.assertIn('https://example.com/story', self.telegram.send.await_args.args[1])
        self.assertEqual(self.bot.web.call.await_args_list[0].args[0], 'search')

    async def test_tasks_command_shows_own_schedules_without_ai(self):
        from bot.schedule import create, parse_request
        req = parse_request('เตือนทุกวัน 23:55 กินยา', self.bot.config.timezone)
        create(self.store, 'telegram', -1, 123, 123, req)
        await self.telegram.handle(self.message(text='/tasks'))
        self.assertIn('กินยา', self.telegram.send.await_args.args[1])
        self.bot.provider.chat.assert_not_awaited()

    async def test_natural_schedule_edit_routes_through_telegram(self):
        from bot.schedule import create, parse_request
        req = parse_request('เตือนทุกวัน 23:55 กินยา', self.bot.config.timezone)
        create(self.store, 'telegram', -1, 123, 123, req)
        await self.telegram.handle(self.message(text='เลื่อนเตือนกินยาเป็น 23:56'))
        self.assertIn('เลื่อนแล้ว', self.telegram.send.await_args.args[1])
        self.bot.provider.chat.assert_not_awaited()

    async def test_seven_day_history_summary_and_clear_are_user_scoped(self):
        import time
        now = time.time()
        self.store.add_telegram_turn(123, 'expired private', 'old', now - 8 * 86400)
        self.store.add_telegram_turn(456, 'OTHER_PRIVATE', 'other', now)
        self.store.add_telegram_turn(123, 'recent personal', 'answer', now)
        await self.telegram.handle(self.message(text='/summary'))
        prompt = str(self.bot.provider.chat.await_args.args[0])
        self.assertIn('recent personal', prompt)
        self.assertNotIn('OTHER_PRIVATE', prompt)
        self.assertNotIn('expired private', prompt)
        await self.telegram.handle(self.message(text='/clear'))
        self.assertEqual(self.store.telegram_turns(123), [])
        self.assertEqual(len(self.store.telegram_turns(456)), 1)

    async def test_summary_is_bounded_and_today_excludes_yesterday(self):
        import time
        from datetime import datetime, timedelta
        from zoneinfo import ZoneInfo
        today = datetime.now(ZoneInfo('Asia/Bangkok')).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        self.store.add_telegram_turn(123, 'YESTERDAY_PRIVATE', 'old', today - 10)
        for n in range(20):
            self.store.add_telegram_turn(123, ('ล่าสุด' * 200) + str(n), 'ตอบ' * 300, time.time())
        await self.telegram.handle(self.message(text='สรุปแชทวันนี้'))
        messages = self.bot.provider.chat.await_args.args[0]
        self.assertNotIn('YESTERDAY_PRIVATE', str(messages))
        self.assertLessEqual(len(messages[1]['content'].encode()), 2400)
        self.assertIn('ประวัติวันนี้', self.telegram.send.await_args.args[1])

    async def test_clear_stays_cleared_after_backup_restore(self):
        from bot.backup import backup, restore
        self.store.add_telegram_turn(123, 'private before clear', 'answer')
        snap = Path(self.temp.name) / 'old.sqlite3'
        backup(self.store.path, snap)
        await self.telegram.handle(self.message(text='/clear'))
        dest = Path(self.temp.name) / 'restored' / 'db'
        dest.parent.mkdir()
        restore(snap, dest, privacy_folder=Path(self.temp.name))
        restored = Store(dest)
        try:
            self.assertEqual(restored.telegram_turns(123), [])
        finally:
            restored.close()

    async def test_optout_prevents_telegram_turn_storage(self):
        self.store.set_user(-1, 123, optout=1)
        await self.telegram.handle(self.message(text='hello'))
        self.assertEqual(self.store.telegram_turns(123), [])
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM telegram_chat_turns').fetchone()[0], 0)

    async def test_only_allowed_private_user_reaches_ai(self):
        for update in (self.message(456), self.message(123, "group"), self.message(123, "supergroup")):
            await self.telegram.handle(update)
        self.bot.provider.chat.assert_not_awaited()
        self.telegram.send.assert_not_awaited()
        await self.telegram.handle(self.message())
        self.bot.provider.chat.assert_awaited_once()
        self.telegram.send.assert_awaited_once()

    async def test_capabilities_are_accurate_and_no_tool_is_invoked(self):
        for question in ("ทำอะไรได้บ้าง", "/help", "ดูรูปได้ไหม"):
            await self.telegram.handle(self.message(text=question))
            reply = self.telegram.send.await_args.args[1]
            self.assertIn("/tone 0–5", reply)
            self.assertIn("JPEG", reply)
        self.bot.provider.chat.assert_not_awaited()
        self.bot.web.call.assert_not_awaited()
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM scheduled_jobs").fetchone()[0], 0)
        self.bot.config.mcp_enabled = False
        await self.telegram.handle(self.message(text="มีฟีเจอร์อะไรบ้าง"))
        self.assertIn("ยังไม่ได้เปิดใช้", self.telegram.send.await_args.args[1])

    async def test_ai_prompt_knows_capabilities_and_does_not_claim_actions(self):
        self.bot.config.mcp_enabled = False
        await self.telegram.handle(self.message(text="สวัสดี"))
        prompt = self.bot.provider.chat.await_args.args[0][0]["content"]
        self.assertIn("Telegram: /time", prompt)
        self.assertIn("Search is unavailable", prompt)
        self.assertIn("Never claim to have searched", prompt)

    async def test_clock_and_reminders_do_not_call_ai(self):
        await self.telegram.handle(self.message(text="ตอนนี้กี่โมง"))
        self.assertIn("Asia/Bangkok", self.telegram.send.await_args.args[1])
        await self.telegram.handle(self.message(text="เตือนทุกวัน 07:00 กินข้าว"))
        self.assertIn("ตั้งเวลาแล้ว", self.telegram.send.await_args.args[1])
        await self.telegram.handle(self.message(text="/schedules"))
        self.assertIn("กินข้าว", self.telegram.send.await_args.args[1])
        self.bot.provider.chat.assert_not_awaited()

    async def test_url_uses_bounded_web_reader_and_returns_source(self):
        await self.telegram.handle(self.message(text="สรุป https://example.com"))
        self.assertEqual(self.bot.web.call.await_args.args[0], "fetch_content")
        self.assertIn("https://example.com", self.telegram.send.await_args.args[1])
        prompt = str(self.bot.provider.chat.await_args.args[0])
        self.assertIn("public facts", prompt)

    async def test_search_and_clear_are_private_and_isolated(self):
        await self.telegram.handle(self.message(text="ค้นเว็บ แมว"))
        self.assertEqual(self.bot.web.call.await_args_list[0].args[0], "search")
        self.assertIn(123, self.telegram.turns)
        await self.telegram.handle(self.message(text="/clear"))
        self.assertNotIn(123, self.telegram.turns)

    async def test_progress_default_toggle_and_partial_failure(self):
        page = WebPage('source', 'https://example.com', 'snippet', 'now')
        self.bot.web.call.side_effect = [[page], ToolError('fetch failed')]
        await self.telegram.handle(self.message(text='ค้นเว็บ แมว'))
        self.assertIn('กำลังค้น', self.telegram.send.await_args_list[0].args[1])
        self.assertIn('บางแหล่ง', self.telegram.send.await_args_list[-1].args[1])
        await self.telegram.handle(self.message(text='/progress off'))
        self.telegram.send.reset_mock()
        self.bot.web.call.side_effect = None
        await self.telegram.handle(self.message(text='ค้นเว็บ แมว'))
        self.telegram.send.assert_awaited_once()
        self.assertEqual(self.store.user(-1, 123)['progress_enabled'], 0)
        self.assertEqual(self.store.user(-1, 456)['progress_enabled'], 1)
        await self.telegram.handle(self.message(text='/progress on'))
        self.assertEqual(self.store.user(-1, 123)['progress_enabled'], 1)

    async def test_progress_reports_synthesis_failure_after_search(self):
        from bot.provider import ProviderError
        self.bot.provider.chat.side_effect = ProviderError('provider unavailable')
        await self.telegram.handle(self.message(text='ค้นเว็บ แมว'))
        self.assertIn('ได้ข้อมูลจากเว็บแล้ว', self.telegram.send.await_args.args[1])

    async def test_rejects_internal_and_multiple_urls(self):
        await self.telegram.handle(self.message(text="อ่าน https://127.0.0.1"))
        await self.telegram.handle(self.message(text="https://a.example https://b.example"))
        self.bot.web.call.assert_not_awaited()
        self.bot.provider.chat.assert_not_awaited()


    async def test_public_status_without_exposing_chat(self):
        with patch("bot.telegram.board_status", new=AsyncMock(return_value={})):
            await self.telegram.handle(self.message(456, text="/status"))
        self.telegram.send.assert_awaited_once()
        self.bot.provider.chat.assert_not_awaited()
        await self.telegram.handle(self.message(456, text="hello"))
        self.bot.provider.chat.assert_not_awaited()

    async def test_memory_tone_and_forget_are_user_scoped(self):
        await self.telegram.handle(self.message(text="/remember ชอบแมว"))
        rows = self.store.own_memories(-1, 123)
        self.assertEqual(len(rows), 1)
        self.assertEqual(self.store.own_memories(-1, 456), [])
        await self.telegram.handle(self.message(text="/tone 4"))
        await self.telegram.handle(self.message(text="สวัสดี"))
        prompt = str(self.bot.provider.chat.await_args.args[0])
        self.assertIn("ชอบแมว", prompt)
        self.assertIn("Teasing level 4/4", prompt)
        await self.telegram.handle(self.message(text=f"/forget {rows[0]['id']}"))
        self.assertEqual(self.store.own_memories(-1, 123), [])

    async def test_tsundere_persists_is_private_and_respects_stop_and_serious(self):
        await self.telegram.handle(self.message(text="/tone 5"))
        self.assertEqual(self.store.meta("telegram:tone:123"), 5)
        self.assertEqual(self.store.meta("telegram:tone:456", 1), 1)
        restarted = Telegram(self.bot, "token", frozenset({123}))
        restarted.send = AsyncMock()
        await restarted.handle(self.message(text="สวัสดี"))
        prompt = self.bot.provider.chat.await_args.args[0][0]["content"]
        self.assertIn("tsundere", prompt)
        self.assertIn("do not force a catchphrase", prompt)
        await restarted.handle(self.message(text="วันนี้เครียดมาก"))
        prompt = self.bot.provider.chat.await_args.args[0][0]["content"]
        self.assertNotIn("Persona: playful tsundere", prompt)
        self.assertIn("Teasing level 0/4", prompt)
        self.assertEqual(self.store.meta("telegram:tone:123"), 5)
        await restarted.handle(self.message(text="หยุดแซว"))
        self.assertEqual(self.store.meta("telegram:tone:123"), 0)
        self.assertEqual(restarted.turns, {})
        await restarted.handle(self.message(text="/tone 6"))
        self.assertEqual(self.store.meta("telegram:tone:123"), 0)

    async def test_forgotten_memory_does_not_return_from_backup(self):
        from bot.backup import backup, restore
        backup_file = Path(self.temp.name) / "backup.sqlite3"
        mid = self.store.remember(-1, -1, 123, "private memory")
        backup(Path(self.temp.name) / "db", backup_file)
        self.assertTrue(self.store.forget(-1, 123, mid))
        recovery = Path(self.temp.name) / "recovery" / "db"
        recovery.parent.mkdir()
        restore(backup_file, recovery, privacy_folder=Path(self.temp.name))
        restored = Store(recovery)
        try:
            self.assertEqual(restored.own_memories(-1, 123), [])
        finally:
            restored.close()

    async def test_photo_private_and_image_forwarded(self):
        update = self.message(text="")
        update["message"].pop("text")
        update["message"]["photo"] = [{"file_id": "test", "file_size": 5}]
        update["message"]["caption"] = "ในภาพคืออะไร"
        self.telegram.download_photo = AsyncMock(return_value=b"\xff\xd8\xffxy")
        await self.telegram.handle(update)
        self.assertEqual(self.bot.provider.chat.await_args.kwargs["image_bytes"], b"\xff\xd8\xffxy")
        update["message"]["from"]["id"] = 456
        update["message"]["chat"]["id"] = 456
        await self.telegram.handle(update)
        self.telegram.download_photo.assert_awaited_once()

    def test_requires_token_and_allowlist(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(settings())
        with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "123456:" + "A" * 30, "TELEGRAM_ALLOWED_USER_IDS": "123"}, clear=True):
            self.assertEqual(settings()[1], frozenset({123}))
        with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "123456:" + "A" * 30}, clear=True):
            self.assertIsNone(settings())

    async def test_api_accepts_fragmented_json_and_masks_network_error(self):
        class Content:
            async def iter_chunked(self, size):
                yield b'{"ok":'
                yield b'true,"result":[]}'

        class Response:
            status = 200
            content = Content()

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

        self.bot.session = SimpleNamespace(post=lambda *a, **kw: Response())
        self.assertEqual(await self.telegram.api("getUpdates", {}), [])
        def fail(*args, **kwargs):
            raise ValueError("token should not appear")
        self.bot.session.post = fail
        with self.assertRaisesRegex(RuntimeError, "Telegram request failed") as caught:
            await self.telegram.api("getUpdates", {})
        self.assertNotIn("token should not appear", str(caught.exception))
