"""Real loopback HTTP tests. Run inside Docker with --network none (no external requests)."""

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

import aiohttp
from aiohttp import web

from bot.config import Config
from bot.provider import Provider, ProviderError
from bot.quota import Quota, QuotaExceeded
from bot.storage import Store


class HTTPIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / "db")
        self.status = 200
        self.calls = 0
        self.app = web.Application()
        self.app.router.add_post("/openai/v1/chat/completions", self.handle)
        self.runner = web.AppRunner(self.app, access_log=None)
        await self.runner.setup()
        self.site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await self.site.start()
        port = self.site._server.sockets[0].getsockname()[1]
        self.config = replace(
            Config("fake-discord", "fake-ai"), base_url=f"http://127.0.0.1:{port}/openai/v1"
        )
        self.session = aiohttp.ClientSession()
        self.provider = Provider(self.config, Quota(self.store, self.config), self.session)

    async def asyncTearDown(self):
        await self.session.close()
        await self.runner.cleanup()
        self.store.close()
        self.temp.cleanup()

    async def handle(self, request):
        self.calls += 1
        body = await request.json()
        self.assertEqual(body["model"], "qwen/qwen3.8-27b")
        self.assertEqual(request.headers["Authorization"], "Bearer fake-ai")
        if self.status == 429:
            return web.json_response(
                {"error": "private provider details"}, status=429, headers={"Retry-After": "10"}
            )
        return web.json_response(
            {"choices": [{"message": {"content": "คุยไทยผ่าน HTTP ได้แล้ว"}}], "usage": {"total_tokens": 42}}
        )

    async def test_real_http_success(self):
        answer = await self.provider.chat([{"role": "user", "content": "ทดสอบ"}])
        self.assertEqual(answer.text, "คุยไทยผ่าน HTTP ได้แล้ว")
        self.assertEqual(answer.tokens, 42)
        self.assertEqual(self.calls, 1)

    async def test_real_http_429_blocks_second_request(self):
        self.status = 429
        with self.assertRaises(ProviderError):
            await self.provider.chat([{"role": "user", "content": "ทดสอบ"}])
        with self.assertRaises(QuotaExceeded):
            await self.provider.chat([{"role": "user", "content": "ทดสอบ"}])
        self.assertEqual(self.calls, 1)
