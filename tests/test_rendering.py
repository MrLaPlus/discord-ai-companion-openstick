import unittest

from bot.rendering import render_answer, sources_only, split_reply, wants_sources


class RenderingTests(unittest.TestCase):
    sources = {"S1": "https://discord.com/channels/1/2/3"}

    def test_hide_provenance_until_requested(self):
        self.assertEqual(render_answer("นัดกันวันเสาร์ [S1]", self.sources), "นัดกันวันเสาร์")
        explicit = render_answer("นัดกันวันเสาร์ [S1]", self.sources, True)
        self.assertIn(self.sources["S1"], explicit)

    def test_internal_thinking_only_never_leaves_renderer(self):
        raw = "[Thinking Process]\n1. Analyze user input.\nprivate synthetic context"
        result = render_answer(raw, self.sources)
        self.assertNotIn("Thinking Process", result)
        self.assertNotIn("private synthetic", result)
        self.assertTrue(result)

    def test_only_delimited_final_answer_survives(self):
        for raw in [
            "[Thinking Process]\nprivate synthetic context\n[Final Answer]\nสวัสดีครับ [S1]",
            "<think>private synthetic context</think>สวัสดีครับ [S1]",
            "<analysis>private synthetic context</analysis>สวัสดีครับ [S1]",
            "<|analysis|>private synthetic context<|final|>สวัสดีครับ [S1]",
        ]:
            with self.subTest(raw=raw):
                self.assertEqual(render_answer(raw, self.sources), "สวัสดีครับ")

    def test_incomplete_or_nested_internal_output_is_not_salvaged(self):
        for raw in [
            "<think>unfinished",
            "<think>outer<think>inner</think>unfinished",
            "[Analysis]\nunfinished",
            "[Thinking Process] unfinished",
            "**[Thinking Process]** unfinished",
        ]:
            with self.subTest(raw=raw):
                result = render_answer(raw, {})
                self.assertNotIn("unfinished", result)
                self.assertNotIn("<think>", result)

    def test_normal_thai_and_code_are_not_cut_by_thinking_guard(self):
        raw = "ลองคิดดูนะ 😺\n```python\nprint('thinking')\n```"
        self.assertEqual(render_answer(raw, {}), raw)

    def test_reported_direct_insults_are_blocked_but_mild_general_slang_survives(self):
        for raw in ["ไม่มีนะไอ้สัส", "อย่ามาหลอกถามฉัน", "ไอ้เหี้ย"]:
            result = render_answer(raw, {})
            self.assertNotIn(raw, result)
            self.assertNotIn("ไอ้", result)
        mild = "เกมนี้แม่งยาก ลองลดระดับก่อนก็ได้ 😺"
        self.assertEqual(render_answer(mild, {}), mild)

    def test_strip_volunteered_message_links_and_reference_section(self):
        result = render_answer("วันเสาร์\nแหล่งอ้างอิง:\n" + self.sources["S1"], self.sources)
        self.assertEqual(result, "วันเสาร์")

    def test_followup_source_question_and_full_question_differ(self):
        for phrase in ["เอามาจากไหน", "เอามาจากไหนครับ", "ขอแหล่งอ้างอิงหน่อย", "sources?"]:
            self.assertTrue(sources_only(phrase), phrase)
        self.assertFalse(sources_only("เมื่อวานคุยอะไร ขอแหล่งอ้างอิงด้วย"))
        self.assertTrue(wants_sources("เมื่อวานคุยอะไร ขอแหล่งอ้างอิงด้วย"))

    def test_split_preserves_full_long_thai_emoji_and_code_text(self):
        text = ("ยาวมาก 😺\n```python\nprint('hello')\n```\n" * 300).strip()
        parts = split_reply(text)
        self.assertGreater(len(parts), 2)
        self.assertEqual("".join(parts), text)
        self.assertTrue(all(len(part.encode("utf-16-le")) // 2 <= 1900 for part in parts))

    def test_no_whitespace_required_for_splitting(self):
        text = "😺" * 4000
        self.assertEqual("".join(split_reply(text)), text)
