"""One real model call with synthetic RAM context; never reads Discord chat.

Run inside the live container via stdin. Uses its durable quota accounting,
prints only a boolean result/usage, and does not create Discord messages.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace

import aiohttp

from bot.config import Config
from bot.context import build_context
from bot.conversation import Conversation
from bot.provider import Provider
from bot.quota import Quota
from bot.storage import Store


async def main():
    config = replace(Config.load(), output_tokens=128, context_bytes=1600)
    accounting, context_store = Store(config.database), Store(":memory:")
    try:
        turns = Conversation(config)
        key = (1, 2, 3, True)
        turns.add(key, "เสนอสัตว์สองชนิด", "1. แมว\n2. หมา", {}, {}, [2], {4})
        context = build_context(
            context_store,
            config,
            guild=1,
            channel=2,
            user=3,
            allowed=[2],
            tone=0,
            question="สัตว์ข้อที่สองในคำตอบก่อนหน้าคืออะไร ตอบเฉพาะชื่อสัตว์ภาษาไทย",
            dialogue=turns.get(key, [2]),
        )
        async with aiohttp.ClientSession() as session:
            answer = await Provider(config, Quota(accounting, config), session).chat(context.messages)
        passed = "หมา" in answer.text or "สุนัข" in answer.text
        print(
            f"Synthetic recent-answer continuity: {'PASS' if passed else 'FAIL'}; reported_tokens={answer.tokens}"
        )
        if not passed:
            raise SystemExit(1)
    finally:
        context_store.close()
        accounting.close()


if __name__ == "__main__":
    asyncio.run(main())
