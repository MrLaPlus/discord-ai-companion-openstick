"""Small, sequential public-page expansion of search results."""
from __future__ import annotations

import asyncio
from dataclasses import replace

from .web_tools import ToolError

PAGE_PREFIX = 'Page extract (bounded, untrusted): '
SNIPPET_PREFIX = 'Search snippet (not a full page or live feed): '


async def read_search_results(pages, fetch, *, limit=3, deadline=22):
    """Read up to three public pages; preserve snippets when a read fails."""
    output = []
    end = asyncio.get_running_loop().time() + deadline
    for source in pages[:min(3, max(0, limit))]:
        fallback = replace(source, text=SNIPPET_PREFIX + source.text)
        try:
            remaining = end - asyncio.get_running_loop().time()
            if remaining <= 0:
                output.append(fallback)
                continue
            async with asyncio.timeout(min(8, remaining)):
                result = await fetch(source.url)
            page = result[0] if result else None
            if page and page.text.strip():
                output.append(replace(page, text=PAGE_PREFIX + page.text))
                continue
        except asyncio.CancelledError:
            raise
        except (ToolError, TimeoutError):
            pass
        output.append(fallback)
    return output


def evidence_kind(page):
    return 'page extract' if page.text.startswith(PAGE_PREFIX) else 'search snippet'
