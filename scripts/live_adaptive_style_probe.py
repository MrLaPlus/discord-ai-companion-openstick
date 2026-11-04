"""Synthetic style checks with normal durable quota; no Discord reads or messages."""

import asyncio
import json

import aiohttp

from bot.config import Config
from bot.context import build_context
from bot.provider import Provider
from bot.quota import Quota
from bot.storage import Store


async def main():
    config = Config.load()
    accounting, context_store = Store(config.database), Store(":memory:")
    cases = [
        (3, "กูเล่นเกมแพ้สามตาติด แจกเงินจนเพื่อนเรียกตู้ ATM ประจำทีมละ มึงว่ายังไง 555"),
        (4, "จริงจังนะ อธิบายให้คนไม่รู้ Docker เข้าใจว่า container ต่างจาก virtual machine ยังไง ข้อดีข้อเสีย และใช้ตอนไหน"),
    ]
    try:
        async with aiohttp.ClientSession() as session:
            provider = Provider(config, Quota(accounting, config), session)
            for tone, question in cases:
                context = build_context(
                    context_store,
                    config,
                    guild=0,
                    channel=0,
                    user=0,
                    allowed=[],
                    question=question,
                    tone=tone,
                )
                answer = await provider.chat(context.messages)
                assert answer.text.strip()
                assert "[Thinking Process]" not in answer.text
                print(
                    json.dumps(
                        {"tone": tone, "tokens": answer.tokens, "synthetic_answer": answer.text},
                        ensure_ascii=False,
                    )
                )
                # Respect the same provider admission controls as ordinary requests.
                await asyncio.sleep(3)
    finally:
        accounting.close()
        context_store.close()


asyncio.run(main())
