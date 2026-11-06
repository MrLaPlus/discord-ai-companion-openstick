import asyncio
import unittest
from unittest.mock import AsyncMock

from bot.research import read_search_results, evidence_kind
from bot.web_tools import ToolError, WebPage


class ResearchTests(unittest.IsolatedAsyncioTestCase):
    async def test_reads_at_most_three_and_retains_failed_snippet(self):
        pages = [WebPage(str(n), f'https://example.com/{n}', f'snippet {n}', 'now') for n in range(4)]
        fetch = AsyncMock(side_effect=[[WebPage('read', 'https://example.com/0', 'content', 'now')], ToolError('failed'), []])
        result = await read_search_results(pages, fetch)
        self.assertEqual(fetch.await_count, 3)
        self.assertEqual([evidence_kind(p) for p in result], ['page extract', 'search snippet', 'search snippet'])
        self.assertIn('snippet 1', result[1].text)

    async def test_total_deadline_preserves_remaining_snippets(self):
        pages = [WebPage(str(n), f'https://example.com/{n}', 'snippet', 'now') for n in range(2)]
        async def slow(url):
            await asyncio.sleep(1)
        result = await read_search_results(pages, slow, deadline=0.01)
        self.assertEqual([evidence_kind(p) for p in result], ['search snippet', 'search snippet'])
