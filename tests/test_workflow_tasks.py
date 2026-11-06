import tempfile
import unittest
from pathlib import Path

from bot.backup import backup, restore
from bot.storage import Store
from bot.task_overview import task_overview
from bot.workflow_tasks import checkpoint, create_task, delete_task, get_task, mark_interrupted


class WorkflowTasksTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'db'
        self.store = Store(self.path)
        self.addCleanup(self.store.close)

    def test_scope_restart_and_overview(self):
        task = create_task(self.store, 'discord', 1, 2, 3, 'report', 'AI news')
        checkpoint(self.store, task, 'อ่านแหล่งข้อมูล', evidence=[{'url': 'https://example.org'}])
        self.assertIsNone(get_task(self.store, 'discord', 1, 9, 3, task[:8]))
        self.assertIsNone(get_task(self.store, 'discord', 1, 2, 4, task[:8]))
        self.assertIn('AI news', task_overview(self.store, 'discord', 1, 2, 3, 'Asia/Bangkok'))
        self.assertNotIn('AI news', task_overview(self.store, 'discord', 1, 9, 3, 'Asia/Bangkok'))
        mark_interrupted(self.store)
        self.assertEqual(get_task(self.store, 'discord', 1, 2, 3, task[:8])['status'], 'interrupted')

    def test_deletion_survives_restore(self):
        task = create_task(self.store, 'telegram', -1, 3, 3, 'report', 'AI news')
        checkpoint(self.store, task, 'รายงานพร้อม', status='finished')
        snapshot = Path(self.tmp.name) / 'snapshot'
        backup(self.path, snapshot)
        self.assertFalse(delete_task(self.store, 'telegram', -1, 4, 4, task[:8]))
        self.assertTrue(delete_task(self.store, 'telegram', -1, 3, 3, task[:8]))
        dest = Path(self.tmp.name) / 'restored' / 'db'
        dest.parent.mkdir()
        restore(snapshot, dest, privacy_folder=Path(self.tmp.name))
        restored = Store(dest)
        self.addCleanup(restored.close)
        self.assertIsNone(get_task(restored, 'telegram', -1, 3, 3, task[:8]))

    def test_evidence_is_bounded(self):
        task = create_task(self.store, 'telegram', -1, 3, 3, 'report', 'AI news')
        with self.assertRaises(ValueError):
            checkpoint(self.store, task, 'สรุปข้อมูล', evidence=['x' * 21000])
