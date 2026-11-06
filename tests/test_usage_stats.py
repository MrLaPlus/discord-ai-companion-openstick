import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from bot.storage import Store
from bot.usage_stats import display, record

class UsageStatsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.store = Store(Path(self.tmp.name) / 'db'); self.addCleanup(self.store.close)
        self.now = datetime(2026,11,6,12,0,tzinfo=ZoneInfo('Asia/Bangkok')).timestamp()

    def test_aggregate_and_missing_usage(self):
        record(self.store,'openrouter',success=True,prompt=100,completion=20,timestamp=self.now)
        record(self.store,'groq',success=False,timestamp=self.now)
        record(self.store,'whisper',success=True,audio_seconds=12,timestamp=self.now)
        text=display(self.store,now=self.now)
        self.assertIn('OR 120 | Groq 0 | รวม 120 *',text)
        self.assertIn('ผู้ให้บริการไม่ได้แจ้ง',text)
        self.assertIn('Whisper: 1 ครั้ง · 12 วินาที',text)
        self.assertIn('/usage detail',text)
        self.assertIn('OR 100+20',display(self.store,detail=True,now=self.now))
        self.assertNotIn('05/11:', text)
        self.assertIn('OR: สำเร็จ 1 · ล้มเหลว 0', display(self.store,detail=True,now=self.now))

    def test_time_boundary_and_no_user_data(self):
        record(self.store,'groq',success=True,prompt=1,completion=2,timestamp=self.now-86400)
        text=display(self.store,now=self.now)
        self.assertIn('05/11: TokenMe 0 | OR 0 | Groq 3',text)
        cols=[r[1] for r in self.store.db.execute('pragma table_info(usage_daily)')]
        self.assertNotIn('text',cols);self.assertNotIn('user',cols)
