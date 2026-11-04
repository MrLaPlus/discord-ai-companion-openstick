import asyncio
import json
import tempfile
import time
import unittest
from dataclasses import replace
from pathlib import Path

import aiohttp

from bot.config import Config
from bot.provider import Provider, ProviderError, retry_seconds
from bot.quota import Quota, QuotaExceeded
from bot.storage import Store


class Chunks:
    def __init__(self, data):
        self.data = data

    async def iter_chunked(self, size):
        # Deliberately fragmented HTTP content, including inside multi-byte Thai characters.
        for offset in range(0, len(self.data), 7):
            yield self.data[offset : offset + 7]


class Response:
    def __init__(self, status=200, data=None, headers=None):
        self.status = status
        self.headers = headers or {}
        self.content = Chunks(
            json.dumps(
                data or {"choices": [{"message": {"content": "สวัสดีแมว"}}], "usage": {"total_tokens": 50}},
                ensure_ascii=False,
            ).encode()
        )

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


class Session:
    def __init__(self, response=None, error=None):
        self.response, self.error = response or Response(), error
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if self.error:
            raise self.error
        return self.response


class ProviderTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / "db")
        self.config = Config("fake-discord", "fake-private-key")
        self.quota = Quota(self.store, self.config)

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    async def test_qwen_payload_and_fragmented_thai_reply(self):
        session = Session()
        answer = await Provider(self.config, self.quota, session).chat([{"role": "user", "content": "แมว"}])
        self.assertEqual(answer.text, "สวัสดีแมว")
        self.assertEqual(answer.tokens, 50)
        payload = session.calls[0][1]["json"]
        self.assertEqual(payload["model"], "qwen/qwen3.8-27b")
        self.assertEqual(payload["reasoning_effort"], "none")
        self.assertEqual(payload["reasoning_format"], "hidden")
        self.assertEqual(payload["max_completion_tokens"], 768)
        self.assertFalse(session.calls[0][1]["allow_redirects"])

    async def test_reasoning_only_completion_is_rejected_and_usage_is_still_counted(self):
        data = {
            "choices": [
                {
                    "message": {"content": "[Thinking Process]\n1. Analyze private context."},
                    "finish_reason": "length",
                }
            ],
            "usage": {"total_tokens": 90},
        }
        with self.assertRaises(ProviderError) as raised:
            await Provider(self.config, self.quota, Session(Response(data=data))).chat(
                [{"role": "user", "content": "test"}]
            )
        self.assertNotIn("private context", str(raised.exception))
        self.assertEqual(self.quota.summary()["reported_tokens_24h"], 90)

    async def test_separate_reasoning_field_never_enters_answer(self):
        data = {
            "choices": [
                {
                    "message": {"content": "ตอบสั้น ๆ ครับ", "reasoning": "private details"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"total_tokens": 50},
        }
        answer = await Provider(self.config, self.quota, Session(Response(data=data))).chat(
            [{"role": "user", "content": "hi"}]
        )
        self.assertEqual(answer.text, "ตอบสั้น ๆ ครับ")
        self.assertNotIn("private", answer.text)

    async def test_tagged_reasoning_removed_but_visible_truncated_answer_preserved(self):
        data = {
            "choices": [
                {"message": {"content": "<think>private</think>ข้อความที่ยังไม่จบ"}, "finish_reason": "length"}
            ],
            "usage": {"total_tokens": 50},
        }
        answer = await Provider(self.config, self.quota, Session(Response(data=data))).chat(
            [{"role": "user", "content": "hi"}]
        )
        self.assertEqual(answer.text, "ข้อความที่ยังไม่จบ")
        self.assertTrue(answer.truncated)

    async def test_429_persists_pause_without_retry_storm(self):
        session = Session(Response(429, {"error": "fake-private-key"}, {"Retry-After": "120"}))
        with self.assertRaises(ProviderError) as raised:
            await Provider(self.config, self.quota, session).chat([{"role": "user", "content": "test"}])
        self.assertNotIn("fake-private-key", str(raised.exception))
        with self.assertRaises(QuotaExceeded):
            self.quota.reserve(1)
        self.assertEqual(len(session.calls), 1)
        self.assertGreater(self.store.meta("provider_pause_until"), time.time() + 110)

    async def test_auth_error_does_not_echo_provider_body(self):
        session = Session(Response(401, {"error": "fake-private-key"}))
        with self.assertRaises(ProviderError) as raised:
            await Provider(self.config, self.quota, session).chat([{"role": "user", "content": "hi"}])
        self.assertIn("401", str(raised.exception))
        self.assertNotIn("fake-private-key", str(raised.exception))
        self.assertEqual(self.quota.summary()["requests_24h"], 1)

    async def test_malformed_or_empty_response_is_safe_error(self):
        for data in [{"choices": []}, {"choices": [{"message": {"content": None}}]}]:
            with self.assertRaises(ProviderError):
                await Provider(self.config, self.quota, Session(Response(data=data))).chat(
                    [{"role": "user", "content": "hi"}]
                )

    async def test_network_failure_counts_attempt_and_sets_backoff(self):
        for error in [aiohttp.ClientConnectionError("sensitive details"), asyncio.TimeoutError()]:
            self.store.set_meta("provider_pause_until", 0)
            with self.assertRaises(ProviderError) as raised:
                await Provider(self.config, self.quota, Session(error=error)).chat(
                    [{"role": "user", "content": "hi"}]
                )
            self.assertNotIn("sensitive", str(raised.exception))
        self.assertEqual(self.quota.summary()["requests_24h"], 2)

    async def test_oversized_response_is_rejected(self):
        response = Response()
        response.content = Chunks(b"x" * 262145)
        with self.assertRaises(ProviderError):
            await Provider(self.config, self.quota, Session(response)).chat(
                [{"role": "user", "content": "hi"}]
            )

    def test_request_limit_survives_restart(self):
        quota = Quota(self.store, replace(self.config, rpm=1))
        quota.reserve(100)
        self.store.close()
        self.store = Store(Path(self.temp.name) / "db")
        quota = Quota(self.store, replace(self.config, rpm=1))
        with self.assertRaises(QuotaExceeded) as raised:
            quota.reserve(100)
        self.assertFalse(raised.exception.daily)

    def test_pending_reservation_blocks_but_reported_usage_releases_overestimate(self):
        quota = Quota(self.store, replace(self.config, tpd=1000))
        first = quota.reserve(700)
        with self.assertRaises(QuotaExceeded) as raised:
            quota.reserve(301)
        self.assertTrue(raised.exception.daily)
        quota.finish(first, 10, "ok")
        quota.reserve(301)

    def test_reported_usage_larger_than_reservation_is_counted(self):
        quota = Quota(self.store, replace(self.config, tpd=1000))
        first = quota.reserve(100)
        quota.finish(first, 950, "ok")
        with self.assertRaises(QuotaExceeded):
            quota.reserve(51)

    def test_retry_after_invalid_header_is_bounded_default(self):
        self.assertEqual(retry_seconds("nonsense"), 60)
        self.assertEqual(retry_seconds("5"), 5)
