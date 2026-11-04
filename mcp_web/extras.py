"""RSS, keyless captions and the isolated Playwright MCP adapter."""

from __future__ import annotations

import asyncio
import json
import os
import re
from datetime import datetime, timezone
from urllib.parse import parse_qs, urljoin, urlsplit

import httpx2
import requests
from bs4 import BeautifulSoup
from defusedxml.ElementTree import fromstring
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from youtube_transcript_api import YouTubeTranscriptApi

from .safe_http import MAX_BODY, fetch_page, public_url


def envelope(results):
    return {"ok": True, "results": results[:5], "fetched_at": datetime.now(timezone.utc).isoformat()}


def parse_feed(body, url):
    root = fromstring(body)

    def tag(e):
        return e.tag.rsplit("}", 1)[-1]

    def text(item, names):
        element = next((e for e in item if tag(e) in names), None)
        return "".join(element.itertext()).strip() if element is not None else ""

    rows, seen = [], set()
    for item in root.iter():
        if tag(item) not in {"item", "entry"}:
            continue
        link = next(
            (
                e.attrib.get("href", "")
                for e in item
                if tag(e) == "link" and e.attrib.get("rel", "alternate") == "alternate"
            ),
            "",
        )
        link = link or text(item, {"link"})
        try:
            target = public_url(urljoin(url, link))
        except ValueError:
            continue
        if target in seen or not link:
            continue
        seen.add(target)
        title = text(item, {"title"})[:200]
        published = text(item, {"pubDate", "published", "updated"})[:100]
        summary = BeautifulSoup(text(item, {"summary", "description", "content"}), "html.parser").get_text(
            " ", strip=True
        )
        rows.append(
            {"title": title, "url": target, "text": f"Published: {published or 'unknown'}. {summary}"[:1200]}
        )
        if len(rows) >= 100:
            break
    return rows


async def read_feed(url: str) -> dict:
    """Read a public RSS/Atom feed, returning up to five dated entries."""
    try:
        async with asyncio.timeout(9):
            data = await fetch_page(url, raw=True)
        return envelope(parse_feed(data["body"], data["url"]))
    except Exception:
        return {"ok": False, "error": "feed_unavailable"}


async def rss_news(query: str) -> dict:
    """Search the configured RSS feed sample, not the whole web; no push notifications."""
    if len(query) > 400:
        return {"ok": False, "error": "invalid_query"}
    feeds = json.loads(
        os.environ.get("RSS_FEEDS")
        or '["https://feeds.bbci.co.uk/news/technology/rss.xml","https://feeds.arstechnica.com/arstechnica/index"]'
    )
    if not isinstance(feeds, list) or not 1 <= len(feeds) <= 3:
        return {"ok": False, "error": "invalid_feed_configuration"}
    results = await asyncio.gather(*(read_all_feed(url) for url in feeds), return_exceptions=True)
    successful = [r for r in results if isinstance(r, list)]
    if not successful:
        return {"ok": False, "error": "feed_unavailable"}
    terms = [
        word
        for word in re.findall(r"[A-Za-z0-9-]{2,}|[ก-๙]+", query.lower())
        if word
        not in {
            "rss",
            "ข่าว",
            "ข่าวล่าสุด",
            "ล่าสุด",
            "อ่านข่าว",
            "ฟีด",
            "news",
            "latest",
            "feed",
            "ทั้งหมด",
            "อ่าน",
            "เกี่ยวกับ",
        }
    ]
    rows, seen = [], set()
    for items in successful:
        for row in items:
            haystack = (row["title"] + " " + row["text"]).lower()
            if row["url"] not in seen and (not terms or any(term in haystack for term in terms)):
                rows.append(row)
                seen.add(row["url"])
    return envelope(rows)


async def read_all_feed(url):
    async with asyncio.timeout(8):
        data = await fetch_page(public_url(url), raw=True)
    return parse_feed(data["body"], data["url"])


def video_id(url):
    p = urlsplit(public_url(url))
    host = p.hostname.lower()
    if host == "youtu.be":
        value = p.path.strip("/")
    elif host in {"youtube.com", "www.youtube.com", "m.youtube.com"}:
        value = (
            parse_qs(p.query).get("v", [""])[0]
            if p.path == "/watch"
            else p.path.rsplit("/", 1)[-1]
            if p.path.startswith(("/shorts/", "/embed/", "/live/"))
            else ""
        )
    else:
        value = ""
    if not re.fullmatch(r"[A-Za-z0-9_-]{11}", value):
        raise ValueError("Invalid video URL")
    return value


class CaptionSession(requests.Session):
    def __init__(self):
        super().__init__()
        self.trust_env = False
        self.proxies = {"https": "http://web-proxy:8080"}
        self.headers["Accept-Encoding"] = "identity"

    def request(self, method, url, **kwargs):
        p = urlsplit(url)
        if (
            p.scheme != "https"
            or p.hostname not in {"www.youtube.com", "youtube.com", "youtubei.googleapis.com"}
            or p.port not in (None, 443)
            or p.username is not None
        ):
            raise ValueError("Caption endpoint not allowed")
        kwargs.update(timeout=5, allow_redirects=False, stream=True)
        response = super().request(method, url, **kwargs)
        if response.status_code in {301, 302, 303, 307, 308} or response.headers.get(
            "Content-Encoding", "identity"
        ) not in {"", "identity"}:
            response.close()
            raise ValueError("Caption redirect/encoding not allowed")
        data = bytearray()
        try:
            for chunk in response.iter_content(8192):
                data.extend(chunk)
                if len(data) > MAX_BODY:
                    raise ValueError("Caption response too large")
            response._content = bytes(data)
            response._content_consumed = True
            return response
        finally:
            response.close()


def caption_rows(vid):
    with CaptionSession() as session:
        transcript = YouTubeTranscriptApi(http_client=session).fetch(vid, languages=["th", "en"])
    groups, current, start, size = [], [], 0, 0
    for entry in transcript:
        text = str(entry.text).replace("\n", " ")
        if not current:
            start = int(entry.start)
        current.append(f"[{int(entry.start) // 60}:{int(entry.start) % 60:02d}] {text}")
        size += len(text.encode())
        if size >= 900:
            groups.append(
                {
                    "title": f"YouTube captions ({transcript.language_code})",
                    "url": f"https://www.youtube.com/watch?v={vid}&t={start}s",
                    "text": "\n".join(current)[:1400],
                }
            )
            current, size = [], 0
            if len(groups) >= 5:
                break
    if current and len(groups) < 5:
        groups.append(
            {
                "title": f"YouTube captions ({transcript.language_code})",
                "url": f"https://www.youtube.com/watch?v={vid}&t={start}s",
                "text": "\n".join(current)[:1400],
            }
        )
    return groups


async def youtube_transcript(url: str) -> dict:
    """Read available Thai/English public subtitles, not audio or image understanding."""
    try:
        vid = video_id(url)
        async with asyncio.timeout(12):
            return envelope(await asyncio.to_thread(caption_rows, vid))
    except Exception:
        return {"ok": False, "error": "transcript_unavailable"}


async def browser_read(url: str) -> dict:
    """Render a public HTTPS URL using the isolated read-only Playwright MCP service."""
    try:
        target = public_url(url)
        async with asyncio.timeout(25), httpx2.AsyncClient(trust_env=False, timeout=24) as http:
            transport = streamable_http_client(
                "http://mcp-browser:8000/mcp", http_client=http, max_sse_event_size=131072
            )
            async with Client(
                transport, read_timeout_seconds=24, cache=None, input_required_max_rounds=0
            ) as client:
                names = {t.name for t in (await client.list_tools()).tools}
                if names != {"read_page"}:
                    raise ValueError("Unexpected browser tools")
                result = await client.session.call_tool("read_page", {"url": target}, read_timeout_seconds=24)
                if result.is_error:
                    raise ValueError("Browser failure")
                data = result.structured_content
                if data is None and len(result.content) == 1 and result.content[0].type == "text":
                    data = json.loads(result.content[0].text)
                if not isinstance(data, dict) or data.get("ok") is not True:
                    raise ValueError("Browser unavailable")
                return data
    except Exception:
        return {"ok": False, "error": "browser_unavailable"}
