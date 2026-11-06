import unittest
from unittest.mock import AsyncMock
from bot.report_workflow import parse_report, build_report
from bot.provider import Answer
from bot.web_tools import WebPage, ToolError


class ReportWorkflowTests(unittest.IsolatedAsyncioTestCase):
    def test_formats_and_unsafe_inputs(self):
        self.assertEqual(parse_report('/report md ข่าว AI'), ('md', 'ข่าว AI'))
        self.assertEqual(parse_report('/report txt AI'), ('txt', 'AI'))
        self.assertIsNone(parse_report('สวัสดี'))
        for text in ('/report pdf AI', '/report md เรื่องที่คุยในห้องเมื่อกี้', '/report txt https://private.example'):
            with self.assertRaises(ValueError): parse_report(text)

    async def test_report_contains_only_real_sources_and_bounded_pages(self):
        results = [WebPage('A', 'https://a.example/a', 'snippet', 'now'), WebPage('B', 'https://b.example/b', 'snippet', 'now')]
        async def tool(name, value):
            if name == 'search': return results
            if 'a.example' in value: return [WebPage('A', value, 'page', 'now')]
            raise ToolError('unavailable')
        model = AsyncMock(return_value=Answer('ข่าว [W1] [W9] https://fake.example/', 3))
        report = await build_report('AI', tool=tool, model=model)
        self.assertIn('https://a.example/a', report)
        self.assertIn('https://b.example/b', report)
        self.assertNotIn('https://fake.example/', report)
        self.assertNotIn('[W9]', report)
        self.assertIn('snippet', report)

    async def test_no_sources_does_not_call_model(self):
        tool = AsyncMock(return_value=[])
        model = AsyncMock()
        with self.assertRaises(ValueError): await build_report('AI', tool=tool, model=model)
        model.assert_not_awaited()
