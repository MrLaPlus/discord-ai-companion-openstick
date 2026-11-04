import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from bot.config import Config
from bot.context import build_context
from bot.conversation import Conversation
from bot.storage import Store


class ConversationTests(unittest.TestCase):
    def setUp(self):
        self.config = Config("fake", "fake")
        self.cache = Conversation(self.config)
        self.key = (10, 20, 30, False)

    def add(self, answer="1. แมว 2. หมา", key=None, channels=None):
        self.cache.add(key or self.key, "เสนอสัตว์หน่อย", answer, {}, {}, channels or [20], {1, 100})

    def test_followup_contains_prior_answer_but_database_does_not(self):
        self.add()
        with tempfile.TemporaryDirectory() as folder:
            store = Store(Path(folder) / "db")
            try:
                context = build_context(
                    store,
                    self.config,
                    guild=10,
                    channel=20,
                    user=30,
                    allowed=[20],
                    question="อันที่สองล่ะ",
                    tone=1,
                    dialogue=self.cache.get(self.key, [20]),
                )
                self.assertIn("1. แมว 2. หมา", str(context.messages))
                self.assertNotIn("แมว", "\n".join(store.db.iterdump()))
            finally:
                store.close()

    def test_scope_private_and_permissions_are_enforced(self):
        self.add(channels=[20, 21])
        for key in [(10, 20, 31, False), (10, 21, 30, False), (10, 20, 30, True), (11, 20, 30, False)]:
            self.assertEqual(self.cache.get(key, [20, 21]), [])
        self.assertEqual(self.cache.get(self.key, [20]), [])

    def test_ttl_count_and_capacity_are_bounded(self):
        for i in range(5):
            self.add(answer=str(i))
        self.assertEqual([t.answer for t in self.cache.get(self.key, [20])], ["2", "3", "4"])
        for turn in self.cache.sessions[self.key]:
            turn.created -= self.config.conversation_seconds + 1
        self.cache.maintain()
        self.assertEqual(self.cache.sessions, {})
        for i in range(210):
            self.add(key=(10, 20, i, False))
        self.assertEqual(len(self.cache.sessions), 200)

    def test_disabled_and_clear_leave_no_retained_turns(self):
        self.cache.config = replace(self.config, conversation_seconds=0)
        self.add()
        self.assertEqual(self.cache.sessions, {})
        self.cache.config = self.config
        self.add()
        self.assertTrue(self.cache.contains(100))
        self.cache.clear()
        self.assertFalse(self.cache.contains(100))
