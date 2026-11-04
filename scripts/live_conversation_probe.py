"""Actual model/header checks with synthetic contexts only; no Discord messages/chat reads."""

from __future__ import annotations

import asyncio
import json
import re
import tempfile
import time
from pathlib import Path

import aiohttp

from bot.config import Config
from bot.context import build_context
from bot.conversation import Conversation
from bot.conversation_tools import catchup_start, repair_question
from bot.provider import Provider
from bot.provider_limits import summary
from bot.quota import Quota
from bot.storage import Store


async def main():
    config = Config.load()
    store = Store(config.database)
    assert store.meta("heartbeat", 0) > time.time() - 90, "Stale heartbeat"
    assert store.db.execute("SELECT 1 FROM sqlite_master WHERE name='feedback'").fetchone()
    quota = Quota(store, config)
    results = []
    async with aiohttp.ClientSession() as session:
        async with session.get(
            "https://discord.com/api/v10/oauth2/applications/@me",
            headers={"Authorization": "Bot " + config.discord_token},
            timeout=aiohttp.ClientTimeout(total=15),
        ) as response:
            assert response.status == 200
            app = await response.json()
        async with session.get(
            f"https://discord.com/api/v10/applications/{app['id']}/guilds/{config.guild_id}/commands",
            headers={"Authorization": "Bot " + config.discord_token},
            timeout=aiohttp.ClientTimeout(total=15),
        ) as response:
            assert response.status == 200
            commands = await response.json()
        assert {"feedback", "catchup", "panel"} <= {c["name"] for c in commands}
        memory = next(c for c in commands if c["name"] == "memory")
        assert all(option["name"] != "search" for option in memory.get("options", [])), (
            "Cancelled search command must stay absent"
        )
        with tempfile.TemporaryDirectory() as folder:
            synthetic = Store(Path(folder) / "db")
            cache = Conversation(config)
            cache.add((1, 2, 3, False), "อธิบายว่าแมนยูคืออะไร", "แมนยูเป็นตัวละครอนิเมะ", {}, {}, [2], {10})
            question = repair_question("หมายถึงทีมฟุตบอล ไม่ใช่อนิเมะ", cache.get((1, 2, 3, False), [2]))
            correction = build_context(
                synthetic,
                config,
                guild=1,
                channel=2,
                user=3,
                allowed=[2],
                question=question,
                tone=1,
                dialogue=cache.get((1, 2, 3, False), [2]),
                repair=True,
            )
            since = catchup_start("ตั้งแต่เมื่อคืนพลาดอะไรบ้าง", config.timezone)
            stamp = time.time() - 1800
            synthetic.put_message(1, 1, 2, 3, "ตกลงนัดไปกินข้าวที่ร้านลมเย็นวันศุกร์", stamp)
            synthetic.put_message(2, 1, 2, 4, "แต่ยังไม่ได้เลือกเวลากัน เดี๋ยวค่อยตกลงอีกที", stamp + 1)
            catchup = build_context(
                synthetic,
                config,
                guild=1,
                channel=2,
                user=3,
                allowed=[2],
                question="ตั้งแต่เมื่อคืนพลาดอะไรบ้าง สรุปหัวข้อ ข้อสรุป และเรื่องที่ยังค้าง",
                tone=1,
                catchup_since=since,
            )
            for name, context in [("correction", correction), ("catchup", catchup)]:
                # Use the normal production ledger. Never bypass rate/budget controls for probes.
                needed = sum(len(m["content"].encode()) + 32 for m in context.messages) + config.output_tokens
                deadline = time.monotonic() + 90
                while quota.remaining_tokens() < needed:
                    assert time.monotonic() < deadline, "Probe admission deadline"
                    await asyncio.sleep(1)
                answer = await Provider(config, quota, session).chat(context.messages)
                assert answer.text and "[Thinking Process]" not in answer.text
                if name == "correction":
                    assert re.search("ฟุตบอล|football", answer.text, re.I), "Correction failed"
                else:
                    assert re.search("เวลา|ค้าง|ยังไม่", answer.text), "Unresolved item missing"
                results.append({"case": name, "tokens": answer.tokens, "answer": answer.text})
            synthetic.close()
    provider = summary(store)
    print(
        json.dumps(
            {
                "cases": results,
                "provider_quota": provider,
                "commands_registered": True,
                "cancelled_semantic_command_absent": True,
                "no_discord_chat_read_or_messages_sent": True,
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    store.close()


if __name__ == "__main__":
    asyncio.run(main())
