"""Public-only real planner + MCP + final-answer probe, using durable production quotas.
No Discord login/history/messages; run via compose stdin. Never prints credentials.
"""

import asyncio
import json
from dataclasses import replace

import aiohttp

from bot.config import Config
from bot.context import build_context, format_answer
from bot.provider import Provider
from bot.quota import Quota
from bot.storage import Store
from bot.web_tools import ToolError, WebTools, explicit_query


async def main():
    config = replace(Config.load(), mcp_enabled=True, output_tokens=256)
    accounting, context_store = Store(config.database), Store(":memory:")
    try:
        quota = Quota(accounting, config)
        if quota.remaining_tokens() < 6000:
            raise SystemExit("Insufficient quota headroom; retry after rolling window resets.")
        async with aiohttp.ClientSession() as session:
            provider = Provider(config, quota, session)
            plan = await provider.plan_public("Manchester United latest football score")
            assert plan and plan[0] == "search", "public planner failed"
            web = WebTools(config, accounting)
            pages = await web.call("search", {"query": explicit_query("สกอร์บอลแมนยูล่าสุด")}, guild=0, user=0)
            assert pages, "search has no references"
            fetched = False
            try:
                extra = await web.call("fetch_content", {"url": pages[0].url}, guild=0, user=0)
                fetched = bool(extra)
                pages = extra + [p for p in pages if not any(p.url == e.url for e in extra)]
            except ToolError:
                pass
            context = build_context(
                context_store,
                replace(config, tpm=quota.remaining_tokens()),
                guild=0,
                channel=0,
                user=0,
                allowed=[],
                tone=1,
                question="สกอร์บอลแมนยูล่าสุด ถ้าผลค้นยังยืนยันไม่ได้บอกตรง ๆ",
                web_pages=pages,
                web_enabled=True,
            )
            assert context.web_sources, "no references fit budget"
            answer = await provider.chat(context.messages)
            text = format_answer(answer.text, {}, web_sources=context.web_sources)
            assert "[Thinking Process]" not in text and "ไอ้สัส" not in text
            print(
                json.dumps(
                    {
                        "planner": plan[0],
                        "search_results": len(pages),
                        "page_read": fetched,
                        "sources_in_context": len(context.web_sources),
                        "final_tokens": answer.tokens,
                        "public_answer": text,
                    },
                    ensure_ascii=False,
                )
            )
    finally:
        context_store.close()
        accounting.close()


asyncio.run(main())
