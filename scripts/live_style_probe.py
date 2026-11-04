"""One real-model level4 probe with synthetic content and durable quota; no Discord messages."""

import asyncio
import json
from dataclasses import replace

import aiohttp

from bot.config import Config
from bot.context import build_context
from bot.provider import Provider
from bot.quota import Quota
from bot.storage import Store


async def main():
    config = replace(Config.load(), output_tokens=160)
    accounting, context_store = Store(config.database), Store(":memory:")
    try:
        quota = Quota(accounting, config)
        context = build_context(
            context_store,
            config,
            guild=0,
            channel=0,
            user=0,
            allowed=[],
            tone=4,
            question="วันนี้เล่นเกมแพ้สามตาติด แซวกูแบบเพื่อนด้วยมุกเปรียบเทียบเพี้ยน ๆ หนึ่งประโยค",
            web_enabled=True,
        )
        async with aiohttp.ClientSession() as session:
            answer = await Provider(config, quota, session).chat(context.messages)
        assert "[Thinking Process]" not in answer.text and "ไอ้สัส" not in answer.text
        print(
            json.dumps(
                {"mode": 4, "reported_tokens": answer.tokens, "synthetic_answer": answer.text},
                ensure_ascii=False,
            )
        )
    finally:
        accounting.close()
        context_store.close()


asyncio.run(main())
