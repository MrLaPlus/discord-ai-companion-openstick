"""Reviewed DuckDuckGo backend behind a small, bounded MCP tool surface."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from datetime import datetime, timezone

import httpx
from duckduckgo_mcp_server import server as ddg
from mcp.server.transport_security import TransportSecuritySettings

from .safe_http import fetch_page, public_url


class QuietContext:
    """Upstream logs may contain queries; never relay them into logs or MCP notifications."""

    def __init__(self):
        self.failed = False

    async def info(self, message):
        pass

    async def error(self, message):
        self.failed = True


class Searcher(ddg.DuckDuckGoSearcher):
    async def _request(self, data, ctx):
        html = await super()._request(data, ctx)
        if len(html.encode()) > 2 * 1024 * 1024:
            raise ValueError("Search response too large")
        if re.search(r"anomaly\.js|challenge-form|captcha|Unfortunately, bots", html, re.I):
            raise ValueError("Search blocked")
        return html


searcher = Searcher(
    safe_search=ddg.SafeSearchMode.STRICT,
    backend="auto",
    ssl_verify=True,
    requests_per_minute=6,
    ref_url_threshold=0,
)


# This process holds only the search key; never log requests, queries, or response bodies.
tavily_lock = asyncio.Lock()
tavily_pause_until = 0.0
tavily_pause_reason = ""


async def tavily_search(query: str, max_results: int) -> tuple[list[dict] | None, str]:
    global tavily_pause_until, tavily_pause_reason
    key = os.environ.get("TAVILY_API_KEY", "").strip()
    if not key:
        return None, "missing_key"
    async with tavily_lock:
        if time.monotonic() < tavily_pause_until:
            return None, tavily_pause_reason
        reason = "temporary_error"
        cooldown = 30
        try:
            async with (
                asyncio.timeout(5),
                httpx.AsyncClient(trust_env=False, follow_redirects=False, timeout=4.5) as client,
            ):
                async with client.stream(
                    "POST",
                    "https://api.tavily.com/search",
                    headers={"Authorization": "Bearer " + key, "Accept-Encoding": "identity"},
                    json={
                        "query": query,
                        "max_results": max_results,
                        "search_depth": "basic",
                        "auto_parameters": False,
                        "include_raw_content": False,
                        "include_answer": False,
                        "include_images": False,
                        "safe_search": True,
                    },
                ) as response:
                    if response.status_code in {432, 433}:
                        reason, cooldown = "quota", 3600
                    elif response.status_code in {401, 403}:
                        reason, cooldown = "invalid_key", 3600
                    elif response.status_code == 429:
                        reason, cooldown = "rate_limit", 60
                    elif response.status_code != 200:
                        reason, cooldown = "temporary_error", 30
                    else:
                        if response.headers.get("content-encoding", "identity") not in {"", "identity"}:
                            raise ValueError("compressed response")
                        body = bytearray()
                        async for chunk in response.aiter_bytes():
                            body.extend(chunk)
                            if len(body) > 131072:
                                raise ValueError("response too large")
                        data = json.loads(body)
                        raw = data.get("results")
                        if not isinstance(raw, list) or len(raw) > max_results:
                            raise ValueError("invalid response")
                        clean = []
                        for item in raw:
                            if not isinstance(item, dict) or not all(
                                isinstance(item.get(field), str) for field in ("title", "url", "content")
                            ):
                                raise ValueError("invalid result")
                            try:
                                url = public_url(item["url"])
                            except ValueError:
                                continue
                            clean.append(
                                {"title": item["title"][:200], "url": url, "text": item["content"][:600]}
                            )
                        return clean, ""
        except asyncio.CancelledError:
            raise
        except Exception:
            reason, cooldown = "temporary_error", 30
        tavily_pause_reason = reason
        tavily_pause_until = time.monotonic() + cooldown
        return None, reason


async def search(query: str, max_results: int = 5) -> dict:
    """Search Tavily first, falling back to DuckDuckGo without exposing credentials."""
    if not query.strip() or len(query) > 400 or not 1 <= max_results <= 5:
        return {"ok": False, "error": "invalid_arguments"}
    if re.search(r"gsk_|tvly-|sk-[a-zA-Z0-9]{12}|password\s*[:=]|secret\s*[:=]", query, re.I):
        return {"ok": False, "error": "invalid_arguments"}
    results, reason = await tavily_search(query, max_results)
    if results is not None:
        return {
            "ok": True,
            "results": results,
            "provider": "tavily",
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        }
    context = QuietContext()
    try:
        async with asyncio.timeout(9):
            results = await searcher.search(query, context, max_results=max_results)
        if context.failed:
            return {"ok": False, "error": "search_unavailable"}
        clean = []
        for result in results[:max_results]:
            try:
                url = public_url(result.link)
            except ValueError:
                continue
            clean.append({"title": result.title[:200], "url": url, "text": result.snippet[:600]})
        return {
            "ok": True,
            "results": clean,
            "provider": "duckduckgo",
            "fallback_reason": reason,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        }
    except Exception:
        return {"ok": False, "error": "search_unavailable"}


async def search_images(query: str) -> dict:
    """Return bounded public image URLs and their originating pages."""
    if not query.strip() or len(query) > 300 or re.search(r"gsk_|tvly-|sk-[a-zA-Z0-9]{12}|password\s*[:=]|secret\s*[:=]", query, re.I):
        return {"ok": False, "error": "invalid_arguments"}
    async def body(response):
        if response.status_code != 200:
            raise ValueError('image search unavailable')
        data = bytearray()
        async for chunk in response.aiter_bytes():
            data.extend(chunk)
            if len(data) > 262144:
                raise ValueError('image response too large')
        return bytes(data)
    def valid(image, source, title):
        try:
            return {"title": str(title)[:200], "url": public_url(source), "text": public_url(image)}
        except (ValueError, TypeError):
            return None
    key = os.environ.get('TAVILY_API_KEY', '').strip()
    try:
        async with asyncio.timeout(16), httpx.AsyncClient(trust_env=False, follow_redirects=False, timeout=7, headers={'Accept-Encoding': 'identity', 'User-Agent': 'Mozilla/5.0'}) as client:
            if key and time.monotonic() >= tavily_pause_until:
                try:
                    async with client.stream('POST', 'https://api.tavily.com/search', headers={'Authorization': 'Bearer ' + key}, json={'query': query, 'max_results': 3, 'search_depth': 'basic', 'include_images': True, 'include_image_descriptions': False, 'safe_search': True, 'auto_parameters': False}) as response:
                        payload = json.loads(await body(response))
                    images = payload.get('images', [])
                    if isinstance(images, list):
                        cleaned = [item for image in images[:5] if isinstance(image, str) and (item := valid(image, image, 'รูปจาก Tavily'))]
                        if cleaned:
                            return {'ok': True, 'results': cleaned, 'provider': 'tavily', 'fetched_at': datetime.now(timezone.utc).isoformat()}
                except asyncio.CancelledError:
                    raise
                except Exception:
                    pass
            async with client.stream('GET', 'https://duckduckgo.com/', params={'q': query, 'kp': '1'}) as response:
                html = (await body(response)).decode('utf-8', 'replace')
            match = re.search(r'vqd=[\"\']?([0-9-]+)', html)
            if not match:
                raise ValueError('search token unavailable')
            async with client.stream('GET', 'https://duckduckgo.com/i.js', params={'q': query, 'vqd': match[1], 'o': 'json', 'p': '1'}) as response:
                payload = json.loads(await body(response))
            raw = payload.get('results', [])
            if not isinstance(raw, list):
                raise ValueError('invalid image results')
            cleaned = [item for result in raw[:5] if isinstance(result, dict) and isinstance(result.get('image'), str) and isinstance(result.get('url'), str) and (item := valid(result['image'], result['url'], result.get('title', 'รูปจาก DuckDuckGo')))]
            return {'ok': True, 'results': cleaned, 'provider': 'duckduckgo', 'fetched_at': datetime.now(timezone.utc).isoformat()}
    except asyncio.CancelledError:
        raise
    except Exception:
        return {'ok': False, 'error': 'search_unavailable'}


# HTML parsing is deliberately serialized to bound peak memory on the USB board.
fetch_slots = asyncio.Semaphore(1)


async def fetch_content(url: str) -> dict:
    """Read one bounded public HTTPS text page, never credentials or private addresses."""
    try:
        async with asyncio.timeout(9), fetch_slots:
            page = await fetch_page(url)
        return {"ok": True, "results": [page], "fetched_at": datetime.now(timezone.utc).isoformat()}
    except asyncio.CancelledError:
        raise
    except Exception:
        return {"ok": False, "error": "fetch_unavailable"}


# A distinct schema pin in the bot preserves the original deployment's pin.
for name in ("search", "fetch_content", "expand_link"):
    ddg.mcp.remove_tool(name)
ddg.mcp.add_tool(search)
ddg.mcp.add_tool(fetch_content)
ddg.mcp.add_tool(search_images)


if __name__ == "__main__":
    logging.getLogger("httpx").setLevel(logging.CRITICAL)
    logging.getLogger("httpx2").setLevel(logging.CRITICAL)
    ddg.mcp.run(
        transport="streamable-http",
        host="127.0.0.1" if os.environ.get("MCP_LOOPBACK_ONLY") == "1" else "0.0.0.0",
        port=8000,
        json_response=True,
        max_request_body_size=16384,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=["mcp-web:8000", "localhost:8000", "127.0.0.1:8000"],
            allowed_origins=[],
        ),
    )
