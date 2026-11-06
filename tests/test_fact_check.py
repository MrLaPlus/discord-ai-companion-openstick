import unittest
from unittest.mock import AsyncMock

from bot.fact_check import fact_check, fact_check_input
from bot.provider import Answer
from bot.web_tools import ToolError, WebPage


class FactCheckTests(unittest.IsolatedAsyncioTestCase):
    def test_intent_and_missing_claim(self):
        self.assertEqual(fact_check_input('เช็กข่าวนี้'), '')
        self.assertEqual(fact_check_input('เช็กข่าวนี้ว่าจริงไหม'), '')
        self.assertEqual(fact_check_input('เช็กข่าว โลกกลม'), 'โลกกลม')
        self.assertEqual(fact_check_input('/factcheck โลกกลม'), 'โลกกลม')
        self.assertIsNone(fact_check_input('เล่าเรื่องข่าว'))

    async def test_explicit_claim_reads_bounded_sources_and_removes_fake_urls(self):
        pages = [WebPage('A', 'https://a.example/a', 'snippet a', 'now'), WebPage('B', 'https://b.example/b', 'snippet b', 'now')]
        async def tool(name, value):
            if name == 'search': return pages
            if 'a.example' in value: return [WebPage('A', value, 'evidence a', 'now')]
            raise ToolError('unavailable')
        model = AsyncMock(return_value=Answer('หลักฐานบางส่วน [W1] และ [W9] https://invented.example', 10))
        result = await fact_check('โลกกลม', tool=tool, model=model)
        self.assertIn('https://a.example/a', result)
        self.assertIn('https://b.example/b', result)
        self.assertNotIn('https://invented.example', result)
        self.assertNotIn('[W9]', result)
        self.assertIn('ข้อความสั้นจากผลค้นหา', result)
        prompt = str(model.await_args.args[0])
        self.assertIn('search snippet', prompt)
        self.assertIn('page extract', prompt)

    async def test_no_results_and_private_references_do_not_invent_verdict(self):
        tool = AsyncMock(return_value=[])
        model = AsyncMock()
        result = await fact_check('โลกกลม', tool=tool, model=model)
        self.assertIn('ยังสรุปว่าจริงหรือเท็จไม่ได้', result)
        model.assert_not_awaited()
        tool.reset_mock()
        result = await fact_check('เรื่องที่คุยในห้องเมื่อกี้', tool=tool, model=model)
        self.assertIn('สาธารณะ', result)
        tool.assert_not_awaited()

    async def test_article_derived_query_excludes_original_host(self):
        article = WebPage('Article', 'https://news.example/story', 'claim from article', 'now')
        external = WebPage('External', 'https://other.example/story', 'source', 'now')
        async def tool(name, value):
            if name == 'search': return [article, external]
            if value == article.url: return [article]
            return [external]
        model = AsyncMock(side_effect=[Answer('public claim query', 1), Answer('ข้อมูล [W1]', 1)])
        result = await fact_check('https://news.example/story', tool=tool, model=model)
        self.assertNotIn('[W2]', result)
        self.assertIn('https://other.example/story', result)
        self.assertIn('เพียงหนึ่งเว็บไซต์', result)
        self.assertEqual(model.await_count, 2)

    async def test_multiple_or_private_url_rejected_before_network(self):
        tool = AsyncMock()
        model = AsyncMock()
        result = await fact_check('https://a.example https://b.example', tool=tool, model=model)
        self.assertIn('หนึ่งลิงก์', result)
        with self.assertRaises(ToolError):
            await fact_check('https://127.0.0.1', tool=tool, model=model)
        tool.assert_not_awaited()
