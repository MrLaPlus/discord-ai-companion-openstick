"""Public-only MCP integration probe. No Discord login, messages, history or real secrets."""

import asyncio
import json
import tempfile
from pathlib import Path

from bot.config import Config
from bot.storage import Store
from bot.web_tools import ToolError, WebTools


async def main():
    with tempfile.TemporaryDirectory() as folder:
        store = Store(Path(folder) / "probe.sqlite3")
        web = WebTools(Config("unused", "unused", mcp_enabled=True), store)
        pages = await web.call("search", {"query": "Manchester United latest match result"}, guild=1, user=1)
        assert pages, "search returned no usable references"
        public = await web.call("fetch_content", {"url": "https://example.com"}, guild=1, user=1)
        assert public and "Example Domain" in public[0].text, "public HTTPS read failed"
        print(
            json.dumps(
                {
                    "search_results": len(pages),
                    "https_read": True,
                    "schema_pinned": bool(store.meta("mcp_web_schema")),
                }
            )
        )
        # Ensure remote enforcement too, without allowing the client to contact private IPs.
        try:
            await web.call("fetch_content", {"url": "https://127.0.0.1"}, guild=1, user=1)
        except ToolError:
            print("private_url_blocked=true")
        else:
            raise AssertionError("private address accepted")
        store.close()


asyncio.run(main())
