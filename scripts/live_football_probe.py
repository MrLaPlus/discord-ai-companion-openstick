"""Two bounded public MCP calls; no Discord reads/messages or model request."""

import asyncio
import json

from bot.config import Config
from bot.football import browser_score_page, football_query, host_rank
from bot.storage import Store
from bot.web_tools import ToolError, WebTools, explicit_query


async def main():
    config = Config.load()
    store = Store(config.database)
    try:
        web = WebTools(config, store)
        question = "ตารางแข่งแมนซิตี้"
        query = football_query(question, explicit_query(question))
        pages = await web.call("search", {"query": query}, guild=0, user=0)
        pages.sort(key=lambda p: host_rank(p.url, question))
        assert pages, "No football search references"
        page = pages[0]
        reader = "browser_read" if browser_score_page(page.url) else "fetch_content"
        try:
            read = await web.call(reader, {"url": page.url}, guild=0, user=0)
            print(
                json.dumps(
                    {
                        "search_results": len(pages),
                        "selected_url": page.url,
                        "reader": reader,
                        "page_count": len(read),
                        "text_bytes": sum(len(p.text.encode()) for p in read),
                        "note": "Transport evidence only; does not certify live scores.",
                    }
                )
            )
        except ToolError:
            print(
                json.dumps(
                    {
                        "search_results": len(pages),
                        "selected_url": page.url,
                        "reader": reader,
                        "page_unavailable": True,
                        "note": "Only search snippets available; no verified live score.",
                    }
                )
            )
    finally:
        store.close()


asyncio.run(main())
