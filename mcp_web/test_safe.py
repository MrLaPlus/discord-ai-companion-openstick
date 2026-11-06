"""Run inside the sidecar image; synthetic HTTP/DNS fixtures never access a private network."""

import json
import socket
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
from mcp import Client

from mcp_web.safe_http import BlockedURL, PinnedTransport, fetch_page, public_ip, public_url
from mcp_web.server import ddg, fetch_content, search


def dns(*ips):
    return [
        (socket.AF_INET6 if ":" in ip else socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 443)) for ip in ips
    ]


class ReaderTests(unittest.IsolatedAsyncioTestCase):
    def test_address_and_url_rejections(self):
        for value in [
            "127.0.0.1",
            "10.0.0.1",
            "169.254.169.254",
            "::1",
            "fc00::1",
            "fe80::1",
            "::ffff:8.8.8.8",
            "2002:0808:0808::1",
            "224.0.0.1",
            "0.0.0.0",
        ]:
            self.assertFalse(public_ip(value), value)
        self.assertTrue(public_ip("8.8.8.8"))
        for url in [
            "file:///etc/passwd",
            "http://example.com",
            "https://127.0.0.1",
            "https://[::1]",
            "https://a:b@example.com",
            "https://example.com:22",
            "https://localhost.local",
            "https://example.com/?api_key=private",
            "https://discord.com/channels/1/2/3",
        ]:
            with self.subTest(url=url), self.assertRaises(BlockedURL):
                public_url(url)

    async def test_dns_pins_actual_connection_and_original_tls_hostname(self):
        seen = []

        async def respond(request):
            seen.append(request)
            return httpx.Response(
                200, headers={"content-type": "text/html"}, text="<title>Match</title><main>Score 2–1</main>"
            )

        resolver = AsyncMock(return_value=dns("8.8.8.8"))
        transport = PinnedTransport(httpx.MockTransport(respond), resolver)
        page = await fetch_page("https://example.com/match", transport=transport)
        self.assertEqual(page["text"], "Score 2–1")
        self.assertEqual(str(seen[0].url), "https://8.8.8.8/match")
        self.assertEqual(seen[0].headers["host"], "example.com")
        self.assertEqual(seen[0].extensions["sni_hostname"], "example.com")
        resolver.assert_awaited_once()  # DNS cannot rebind between validation and connect.

    async def test_mixed_dns_rejected_before_connect(self):
        inner = AsyncMock(spec=httpx.AsyncBaseTransport)
        transport = PinnedTransport(inner, AsyncMock(return_value=dns("8.8.8.8", "127.0.0.1")))
        with self.assertRaises(BlockedURL):
            await fetch_page("https://example.com", transport=transport)
        inner.handle_async_request.assert_not_awaited()

    async def test_redirect_to_metadata_rejected_before_second_request(self):
        requests = []

        def response(request):
            requests.append(request)
            return httpx.Response(302, headers={"location": "https://169.254.169.254/latest"})

        with self.assertRaises(BlockedURL):
            await fetch_page("https://example.com", transport=httpx.MockTransport(response))
        self.assertEqual(len(requests), 1)

    async def test_oversized_binary_and_compressed_pages_rejected(self):
        import gzip

        cases = [
            ({"content-type": "image/png"}, b"PNG"),
            ({"content-type": "text/html", "content-length": "9999999"}, b"abc"),
            ({"content-type": "text/html", "content-encoding": "gzip"}, gzip.compress(b"A" * 2100000)),
        ]
        for headers, content in cases:
            with self.subTest(headers=headers), self.assertRaises(BlockedURL):
                await fetch_page(
                    "https://example.com",
                    transport=httpx.MockTransport(
                        lambda r: httpx.Response(200, headers=headers, content=content)
                    ),
                )

    async def test_failed_search_is_distinct_from_empty_results(self):
        async def failed(query, ctx, **kwargs):
            await ctx.error("secret details should not escape")
            return []

        with patch("mcp_web.server.searcher.search", side_effect=failed):
            self.assertEqual((await search("public query"))["error"], "search_unavailable")
        with patch("mcp_web.server.searcher.search", AsyncMock(return_value=[])):
            self.assertEqual((await search("public query"))["results"], [])
            self.assertTrue((await search("public query"))["ok"])

    async def test_fetch_tool_uses_bounded_reader_and_hides_errors(self):
        page = {"title": "Page", "url": "https://example.com/article", "text": "public text"}
        with patch("mcp_web.server.fetch_page", new=AsyncMock(return_value=page)) as fetch:
            result = await fetch_content("https://example.com/article")
        self.assertTrue(result["ok"])
        self.assertEqual(result["results"], [page])
        fetch.assert_awaited_once_with("https://example.com/article")
        with patch("mcp_web.server.fetch_page", new=AsyncMock(side_effect=ValueError("private detail"))):
            result = await fetch_content("https://example.com/article")
        self.assertEqual(result, {"ok": False, "error": "fetch_unavailable"})

    async def test_direct_private_and_sensitive_redirects_are_rejected(self):
        requests = []

        def redirect(request):
            requests.append(request)
            return httpx.Response(302, headers={"location": "https://example.com/?code=private"})

        with self.assertRaises(BlockedURL):
            await fetch_page("https://127.0.0.1", transport=httpx.MockTransport(redirect))
        self.assertEqual(requests, [])
        with self.assertRaises(BlockedURL):
            await fetch_page("https://example.org/", transport=httpx.MockTransport(redirect))
        self.assertEqual(len(requests), 1)

    async def test_fetch_respects_half_mebibyte_response_cap(self):
        body = b"x" * (512 * 1024 + 1)
        with self.assertRaises(BlockedURL):
            await fetch_page(
                "https://example.com",
                transport=httpx.MockTransport(
                    lambda request: httpx.Response(200, headers={"content-type": "text/html"}, content=body)
                ),
            )

    async def test_real_sdk_only_reviewed_tools_and_json_envelope(self):
        with patch(
            "mcp_web.server.searcher.search",
            AsyncMock(
                return_value=[
                    SimpleNamespace(
                        title="Fixture", link="https://example.com", snippet="public result", position=1
                    )
                ]
            ),
        ):
            async with Client(ddg.mcp, read_timeout_seconds=5) as client:
                names = {tool.name for tool in (await client.list_tools()).tools}
                self.assertEqual(names, {"search", "fetch_content", "search_images"})
                result = await client.session.call_tool("search", {"query": "public query"})
                data = result.structured_content or json.loads(result.content[0].text)
                self.assertTrue(data["ok"])
                self.assertEqual(len(data["results"]), 1)
                page = {"title": "Page", "url": "https://example.com/", "text": "bounded page"}
                with patch("mcp_web.server.fetch_page", new=AsyncMock(return_value=page)):
                    page_result = await client.session.call_tool(
                        "fetch_content", {"url": "https://example.com/"}
                    )
                page_data = page_result.structured_content or json.loads(page_result.content[0].text)
                self.assertEqual(page_data["results"], [page])
