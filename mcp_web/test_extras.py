import socket
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from mcp_web.extras import CaptionSession, caption_rows, parse_feed, rss_news, video_id, youtube_transcript
from mcp_web.proxy import connect_host, resolve_public


class ExtrasTests(unittest.IsolatedAsyncioTestCase):
    def test_feed_dates_links_and_deduplication(self):
        body = b"<rss><channel><item><title>Qwen release</title><link>https://example.com/a</link><pubDate>28 Oct 2026</pubDate><description>Public news</description></item><item><title>dup</title><link>https://example.com/a</link></item><item><link>https://127.0.0.1</link></item></channel></rss>"
        rows = parse_feed(body, "https://example.com/rss")
        self.assertEqual(len(rows), 1)
        self.assertIn("28 Oct 2026", rows[0]["text"])
        atom = b'<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>Update</title><link href="/post"/><updated>2026-10-28</updated><summary>Text</summary></entry></feed>'
        self.assertEqual(parse_feed(atom, "https://example.com/feed")[0]["url"], "https://example.com/post")

    def test_xml_entities_rejected(self):
        with self.assertRaises(Exception):
            parse_feed(
                b'<!DOCTYPE x [<!ENTITY a SYSTEM "file:///etc/passwd">]><rss>&a;</rss>', "https://example.com"
            )

    async def test_news_filter_and_failure_are_distinct(self):
        rows = [
            {"title": "Qwen update", "url": "https://example.com", "text": "today"},
            {"title": "Other", "url": "https://example.com/b", "text": "today"},
        ]
        with patch("mcp_web.extras.read_all_feed", AsyncMock(return_value=rows)):
            r = await rss_news("qwen")
            self.assertTrue(r["ok"])
            self.assertEqual(len(r["results"]), 1)
            self.assertEqual((await rss_news("no_match"))["results"], [])
        with patch("mcp_web.extras.read_all_feed", AsyncMock(side_effect=ValueError)):
            self.assertFalse((await rss_news("qwen"))["ok"])

    def test_youtube_url_validation_and_no_private_endpoints(self):
        self.assertEqual(video_id("https://youtu.be/dQw4w9WgXcQ"), "dQw4w9WgXcQ")
        self.assertEqual(video_id("https://www.youtube.com/watch?v=dQw4w9WgXcQ"), "dQw4w9WgXcQ")
        for value in [
            "https://example.com/watch?v=dQw4w9WgXcQ",
            "https://youtube.com.evil.com/watch?v=dQw4w9WgXcQ",
            "https://youtube.com/watch?v=bad",
        ]:
            with self.assertRaises(ValueError):
                video_id(value)
        with CaptionSession() as session:
            self.assertFalse(session.trust_env)
            with self.assertRaises(ValueError):
                session.get("https://127.0.0.1")
            with self.assertRaises(ValueError):
                session.get("http://www.youtube.com")

    def test_caption_timestamp_sources_and_bounded_sample(self):
        entries = [SimpleNamespace(start=n * 20, text="public caption " * 100) for n in range(20)]

        class Transcript(list):
            language_code = "en"

        manager = SimpleNamespace(fetch=lambda *args, **kwargs: Transcript(entries))
        with patch("mcp_web.extras.YouTubeTranscriptApi", return_value=manager):
            rows = caption_rows("dQw4w9WgXcQ")
        self.assertEqual(len(rows), 5)
        self.assertIn("&t=0s", rows[0]["url"])
        self.assertIn("[0:00]", rows[0]["text"])
        self.assertLessEqual(len(rows[0]["text"]), 1400)

    async def test_missing_captions_returns_fixed_failure(self):
        with patch("mcp_web.extras.caption_rows", side_effect=ValueError("private detail")):
            result = await youtube_transcript("https://youtu.be/dQw4w9WgXcQ")
        self.assertEqual(result, {"ok": False, "error": "transcript_unavailable"})

    async def test_connect_only_public_443_and_actual_ip_pinning(self):
        for line in [
            "CONNECT 127.0.0.1:443 HTTP/1.1",
            "CONNECT 169.254.169.254:443 HTTP/1.1",
            "CONNECT example.com:22 HTTP/1.1",
            "GET https://example.com HTTP/1.1",
        ]:
            with self.assertRaises(ValueError):
                connect_host(line)
        self.assertEqual(connect_host("CONNECT example.com:443 HTTP/1.1"), "example.com")
        resolver = AsyncMock(return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443))])
        self.assertEqual(await resolve_public("example.com", resolver), "8.8.8.8")
        resolver.return_value.append((socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.1", 443)))
        with self.assertRaises(ValueError):
            await resolve_public("example.com", resolver)
        resolver.assert_awaited()
