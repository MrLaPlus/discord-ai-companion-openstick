import json
import os
import unittest
from unittest.mock import patch

import httpx
from mcp_web import server


class ImageSearchTests(unittest.IsolatedAsyncioTestCase):
    def client(self, handler):
        real = httpx.AsyncClient
        return lambda **kwargs: real(transport=httpx.MockTransport(handler), **kwargs)

    async def test_tavily_returns_public_image(self):
        def handler(request):
            self.assertEqual(json.loads(request.content)['include_images'], True)
            return httpx.Response(200, json={'images': ['https://example.com/pic.jpg', 'https://127.0.0.1/pic.jpg']})
        with patch.dict(os.environ, {'TAVILY_API_KEY': 'test'}), patch.object(server.httpx, 'AsyncClient', self.client(handler)):
            result = await server.search_images('ริมุรุ')
        self.assertTrue(result['ok'])
        self.assertEqual(result['provider'], 'tavily')
        self.assertEqual(len(result['results']), 1)
        self.assertEqual(result['results'][0]['text'], 'https://example.com/pic.jpg')

    async def test_ddg_fallback_preserves_page_source(self):
        def handler(request):
            if request.url.path == '/':
                return httpx.Response(200, text="vqd='123-456'")
            return httpx.Response(200, json={'results': [{'image': 'https://example.com/a.jpg', 'url': 'https://example.com/source', 'title': 'A'}]})
        with patch.dict(os.environ, {'TAVILY_API_KEY': ''}), patch.object(server.httpx, 'AsyncClient', self.client(handler)):
            result = await server.search_images('ริมุรุ')
        self.assertEqual(result['provider'], 'duckduckgo')
        self.assertEqual(result['results'][0]['url'], 'https://example.com/source')

    async def test_invalid_secrets_never_go_to_network(self):
        with patch.object(server.httpx, 'AsyncClient') as client:
            result = await server.search_images('find tvly-secret')
        self.assertFalse(result['ok'])
        client.assert_not_called()
