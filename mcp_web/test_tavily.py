import asyncio
import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx

from mcp_web import server


class TavilyTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        server.tavily_pause_until = 0
        server.tavily_pause_reason = ""
        self.real_client = httpx.AsyncClient
        self.ddg_result = [SimpleNamespace(title="fallback", link="https://example.com", snippet="snippet")]

    def client(self, handler):
        transport = httpx.MockTransport(handler)
        return lambda **kwargs: self.real_client(transport=transport, **kwargs)

    async def test_tavily_success_and_empty_are_not_fallbacks(self):
        for results in ([{"title": "T", "url": "https://example.com", "content": "fact"}], []):
            with self.subTest(results=results):
                with (
                    patch.dict(os.environ, {"TAVILY_API_KEY": "test-key"}),
                    patch.object(
                        server.httpx,
                        "AsyncClient",
                        self.client(lambda r: httpx.Response(200, json={"results": results})),
                    ),
                    patch.object(server.searcher, "search", new_callable=AsyncMock) as fallback,
                ):
                    result = await server.search("public query")
                self.assertEqual(result["provider"], "tavily")
                self.assertEqual(len(result["results"]), len(results))
                fallback.assert_not_awaited()

    async def test_http_errors_fall_back_and_cooldown(self):
        for status, reason in (
            (432, "quota"),
            (433, "quota"),
            (429, "rate_limit"),
            (401, "invalid_key"),
            (500, "temporary_error"),
        ):
            server.tavily_pause_until = 0
            calls = []

            def handler(request):
                calls.append(request)
                return httpx.Response(status, text="private provider response")

            with (
                patch.dict(os.environ, {"TAVILY_API_KEY": "test-key"}),
                patch.object(server.httpx, "AsyncClient", self.client(handler)),
                patch.object(server.searcher, "search", new=AsyncMock(return_value=self.ddg_result)),
            ):
                result = await server.search("public query")
                again = await server.search("another query")
            self.assertEqual((result["provider"], result["fallback_reason"]), ("duckduckgo", reason))
            self.assertEqual(again["fallback_reason"], reason)
            self.assertEqual(len(calls), 1)
            self.assertNotIn("private provider response", str(result))

    async def test_missing_key_timeout_and_both_fail(self):
        with (
            patch.dict(os.environ, {"TAVILY_API_KEY": ""}),
            patch.object(server.searcher, "search", new=AsyncMock(return_value=self.ddg_result)),
        ):
            self.assertEqual((await server.search("public query"))["fallback_reason"], "missing_key")
        server.tavily_pause_until = 0

        def timeout(request):
            raise httpx.ReadTimeout("private detail")

        with (
            patch.dict(os.environ, {"TAVILY_API_KEY": "test-key"}),
            patch.object(server.httpx, "AsyncClient", self.client(timeout)),
            patch.object(server.searcher, "search", new=AsyncMock(side_effect=ValueError("secret"))),
        ):
            self.assertEqual((await server.search("public query"))["error"], "search_unavailable")
        self.assertEqual(server.tavily_pause_reason, "temporary_error")

    async def test_key_in_query_rejected_before_network(self):
        with patch.object(server, "tavily_search", new_callable=AsyncMock) as tavily:
            result = await server.search("find tvly-test-secret")
        self.assertEqual(result["error"], "invalid_arguments")
        tavily.assert_not_awaited()

    async def test_cancellation_propagates(self):
        def cancel(request):
            raise asyncio.CancelledError()

        with (
            patch.dict(os.environ, {"TAVILY_API_KEY": "test-key"}),
            patch.object(server.httpx, "AsyncClient", self.client(cancel)),
        ):
            with self.assertRaises(asyncio.CancelledError):
                await server.search("public query")
