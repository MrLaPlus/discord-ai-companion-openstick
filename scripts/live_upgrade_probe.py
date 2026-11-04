"""Post-deploy checks with synthetic input only; never log Discord/chat/credentials."""

from __future__ import annotations

import asyncio
import json
import tempfile
import time
from pathlib import Path

import aiohttp

from bot.backup import recovery_drill
from bot.config import Config
from bot.context import build_context
from bot.provider import Provider
from bot.quota import Quota
from bot.storage import Store
from bot.web_tools import WebTools


async def main():
    config = Config.load()
    store = Store(config.database)
    web = WebTools(config, store)
    health = await web.check_health()
    assert health["status"] == "reachable", health
    assert store.meta("heartbeat", 0) > time.time() - 90
    assert (config.database.parent / "privacy-ledger.sqlite3").stat().st_mode & 0o777 == 0o600
    saved = config.database.parent / "backups" / "pre-companion-controls-2026-10-28.sqlite3"
    result = await asyncio.to_thread(recovery_drill, saved, config.database.parent)
    store.set_meta("last_recovery", {**result, "checked": time.time()})
    async with aiohttp.ClientSession() as session:
        async with session.get(
            "https://discord.com/api/v10/oauth2/applications/@me",
            headers={"Authorization": "Bot " + config.discord_token},
        ) as response:
            assert response.status == 200
            application = await response.json()
        async with session.get(
            f"https://discord.com/api/v10/applications/{application['id']}/guilds/{config.guild_id}/commands",
            headers={"Authorization": "Bot " + config.discord_token},
            timeout=aiohttp.ClientTimeout(total=15),
        ) as response:
            assert response.status == 200, f"Command registration HTTP {response.status}"
            commands = await response.json()
        names = {c["name"] for c in commands}
        assert {"panel", "stop", "joke", "ask", "privacy", "tone"} <= names
        # Synthetic scene, using production durable quota but no persisted real conversation.
        with tempfile.TemporaryDirectory() as folder:
            synthetic = Store(Path(folder) / "db")
            context = build_context(
                synthetic,
                config,
                guild=1,
                channel=2,
                user=3,
                allowed=[],
                question="เล่นเกมแพ้ติดสามตา แซวสั้นๆหน่อย",
                tone=4,
            )
            answer = await Provider(config, Quota(store, config), session).chat(context.messages)
            assert answer.text and "[Thinking Process]" not in answer.text
            synthetic.close()
    print(
        json.dumps(
            {
                "mcp_discovery": health["status"],
                "commands": sorted(names & {"panel", "stop", "joke", "ask", "privacy", "tone"}),
                "recovery": result["ok"],
                "privacy_ledger_0600": True,
                "synthetic_style_tokens": answer.tokens,
                "synthetic_answer": answer.text,
                "no_discord_messages_sent": True,
            },
            ensure_ascii=False,
        )
    )
    store.close()


if __name__ == "__main__":
    asyncio.run(main())
