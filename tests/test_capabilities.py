import unittest
from types import SimpleNamespace

from bot.capabilities import capability_prompt, capability_question, capability_text, schedule_is_ready


class CapabilityTests(unittest.TestCase):
    def test_recognizes_natural_questions_without_intercepting_tasks(self):
        for value in ('ทำอะไรได้บ้าง', 'บอททำอะไรได้บ้าง', 'มีฟีเจอร์อะไรบ้าง', 'ตั้งเตือนได้ไหม', 'ดูรูปได้ไหม', '/features', '/help'):
            with self.subTest(value=value):
                self.assertTrue(capability_question(value))
        for value in ('เตือนทุกวัน 07:00 กินข้าว', 'สรุป https://example.com', 'แมวทำอะไรได้บ้าง', 'ค้นเว็บข่าววันนี้'):
            with self.subTest(value=value):
                self.assertFalse(capability_question(value))

    def test_respects_platform_web_and_private_storage_state(self):
        telegram = capability_text('telegram', web_enabled=True)
        self.assertIn('/tone 0–5', telegram)
        self.assertIn('JPEG', telegram)
        discord = capability_text('discord', web_enabled=False, storage_allowed=False)
        self.assertIn('ยังไม่ได้เปิดใช้', discord)
        self.assertIn('ความจำและการตั้งเวลาปิดอยู่', discord)
        self.assertIn('ยังไม่รองรับอ่านรูป', discord)
        self.assertNotIn('/tone 0–5', discord)
        prompt = capability_prompt('telegram', web_enabled=False, storage_allowed=False, vision_enabled=False)
        self.assertIn('disabled', prompt)
        self.assertIn('unconfirmed', prompt)
        self.assertIn('Never claim', prompt)

    def test_scheduler_and_individual_tools_are_described_separately(self):
        text = capability_text('discord', web_enabled=False, fetch_enabled=True,
                               memory_enabled=False, scheduler_ready=False)
        self.assertIn('ค้นเว็บ: ยังไม่ได้เปิดใช้หรือคุณไม่มีสิทธิ์', text)
        self.assertIn('อ่านหน้าเว็บ HTTPS สาธารณะ', text)
        self.assertIn('ตัวตั้งเวลาไม่พร้อม', text)
        self.assertIn('ต้องให้แอดมินเปิดอ่านห้องก่อน', text)
        self.assertNotIn('/schedule ตั้งงาน', text)

    def test_scheduler_status_requires_live_task_and_synchronized_clock(self):
        task = SimpleNamespace(done=lambda: False)
        scheduler = SimpleNamespace(clock_ok=False)
        bot = SimpleNamespace(scheduler_task=task, scheduler=scheduler)
        self.assertFalse(schedule_is_ready(bot))
        scheduler.clock_ok = True
        self.assertTrue(schedule_is_ready(bot))
        task.done = lambda: True
        self.assertFalse(schedule_is_ready(bot))
