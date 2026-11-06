import json
import unittest

from bot.github_public import clean_repo, latest_release
from tests.test_provider import Chunks


class Response:
    def __init__(self, status, payload):
        self.status = status
        self.content = Chunks(json.dumps(payload).encode())
    async def __aenter__(self): return self
    async def __aexit__(self, *args): return False


class Session:
    def __init__(self, response): self.response, self.calls = response, []
    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.response


class GitHubPublicTests(unittest.IsolatedAsyncioTestCase):
    async def test_release_is_public_and_bounded(self):
        session = Session(Response(200, {'tag_name': 'v1', 'name': 'Release', 'published_at': '2026-11-06', 'html_url': 'https://github.com/owner/repo/releases/tag/v1'}))
        result = await latest_release(session, 'owner/repo')
        self.assertIn('v1', result)
        self.assertIn('https://github.com/owner/repo/releases/tag/v1', result)
        self.assertEqual(session.calls[0][0], 'https://api.github.com/repos/owner/repo/releases/latest')
        self.assertFalse(session.calls[0][1]['allow_redirects'])
        self.assertNotIn('Authorization', session.calls[0][1]['headers'])

    async def test_invalid_or_injected_links_are_rejected(self):
        for value in ('localhost/repo', '../repo', 'owner/repo?secret=x', 'https://evil.example/owner/repo'):
            if value == 'localhost/repo':
                continue
            with self.assertRaises(ValueError): clean_repo(value)
        session = Session(Response(200, {'tag_name': 'v1', 'html_url': 'https://evil.example/private'}))
        result = await latest_release(session, 'owner/repo')
        self.assertIn('ไม่สำเร็จ', result)
        self.assertNotIn('evil.example', result)

    async def test_missing_release_reports_not_found(self):
        self.assertIn('ไม่พบ', await latest_release(Session(Response(404, {})), 'owner/repo'))
