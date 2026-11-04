from __future__ import annotations

import argparse
import asyncio
import logging
import os
import signal

import aiohttp
import discord

from .app import Companion
from .config import Config


async def run(config: Config):
    client = Companion(config)
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, lambda: asyncio.create_task(client.close()))
    try:
        async with client:
            await client.start(config.discord_token)
    except discord.LoginFailure:
        raise RuntimeError("Discord token is invalid; update DISCORD_TOKEN") from None
    except (discord.HTTPException, aiohttp.ClientError):
        raise RuntimeError(
            "Discord connection or command registration failed; check connectivity and permissions"
        ) from None


def main():
    parser = argparse.ArgumentParser(description="Headless Discord AI companion")
    parser.add_argument("--check", action="store_true", help="Validate config/commands without logging in")
    args = parser.parse_args()
    try:
        config = Config.load(require_secrets=not args.check)
        logging.basicConfig(
            level=os.environ.get("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(name)s %(message)s"
        )
        if args.check:
            from .commands import register

            client = Companion(config)
            register(client)
            for command in client.tree.get_commands():
                command.to_dict(client.tree)
            client.store.close()
            print("Configuration, database schema, and slash command definitions: OK")
            print(
                "Secrets present: Discord="
                + str(bool(config.discord_token))
                + "; AI="
                + str(bool(config.api_key))
            )
            print("No network connection was made.")
            return
        asyncio.run(run(config))
    except (ValueError, RuntimeError) as error:
        print(f"Startup failed: {error}")
        raise SystemExit(1) from None
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
