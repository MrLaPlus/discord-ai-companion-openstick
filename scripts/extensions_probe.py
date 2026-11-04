"""Actual public-only MCP probes; no Discord login, chat access or real credentials."""

import asyncio
import base64
import json
import tempfile
from pathlib import Path
from urllib.parse import quote

from bot.config import Config
from bot.storage import Store
from bot.web_tools import ToolError, WebTools


async def main():
    with tempfile.TemporaryDirectory() as folder:
        store = Store(Path(folder) / "db")
        web = WebTools(Config("unused", "unused", mcp_enabled=True, mcp_rpm=20, mcp_user_daily=20), store)
        rss = await web.call("rss_news", {"query": "RSS ข่าวล่าสุด"}, guild=0, user=0)
        assert rss and any("Published:" in page.text for page in rss), "No dated feed entries"
        print("RSS actual entries=" + str(len(rss)), flush=True)
        try:
            captions = await web.call(
                "youtube_transcript", {"url": "https://youtu.be/dQw4w9WgXcQ"}, guild=0, user=0
            )
            assert captions and "&t=" in captions[0].url
            print("YouTube actual caption chunks=" + str(len(captions)), flush=True)
        except ToolError as error:
            print("YouTube unavailable: " + str(error), flush=True)
        html = b"<title>Browser probe</title><main id='proof'>WAIT</main><script>document.querySelector('#proof').textContent='JS_RENDER_OK';fetch('https://127.0.0.1/').catch(()=>document.querySelector('#proof').textContent+=' PRIVATE_FETCH_BLOCKED')</script>"
        encoded = quote(base64.b64encode(html).decode(), safe="")
        success = False
        for host in ("httpbingo.org", "httpbin.org"):
            try:
                pages = await web.call(
                    "browser_read", {"url": f"https://{host}/base64/{encoded}"}, guild=0, user=0
                )
                text = " ".join(page.text for page in pages)
                assert "JS_RENDER_OK" in text and "PRIVATE_FETCH_BLOCKED" in text, (
                    "JS execution/private-fetch rejection missing"
                )
                print(
                    json.dumps(
                        {"playwright_js": True, "private_fetch_blocked": True, "source_count": len(pages)}
                    ),
                    flush=True,
                )
                success = True
                break
            except ToolError:
                continue
        assert success, "No actual JavaScript probe succeeded"
        print("Reviewed six-tool schema: " + store.meta("mcp_web_schema"), flush=True)
        store.close()


asyncio.run(main())
