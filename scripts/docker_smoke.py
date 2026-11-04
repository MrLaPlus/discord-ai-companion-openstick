"""Smoke-test a built image using a disposable volume, never production data or live credentials."""

from __future__ import annotations

import subprocess
import uuid


def run(*args):
    subprocess.run(args, check=True)


def main():
    volume = "companion-smoke-" + uuid.uuid4().hex[:12]
    run("docker", "volume", "create", volume)
    command = [
        "docker",
        "run",
        "--rm",
        "--network",
        "none",
        "--read-only",
        "--tmpfs",
        "/tmp:size=16m",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges:true",
        "--mount",
        f"source={volume},target=/app/data",
        "discord-ai-companion:local",
        "python",
        "-c",
    ]
    try:
        run(
            *command,
            """
import os
from pathlib import Path
from bot.storage import Store
assert os.getuid() == 10001
try:
    Path('/app/root-write-must-fail').write_text('test')
except OSError:
    pass
else:
    raise AssertionError('root filesystem must be read-only')
store = Store('/app/data/bot.sqlite3')
store.set_channel(1, 2, readable=1, respond=1, bot_room=1)
store.remember(1, 2, 3, 'persisted Thai memory: ชอบเกมแมว')
store.set_user(1, 4, optout=1)
store.close()
print('Container 1: non-root, read-only root, persistent write: PASS')
""",
        )
        run(
            *command,
            """
from pathlib import Path
from bot.storage import Store
from bot.backup import backup, restore
from bot.health import healthy
store = Store('/app/data/bot.sqlite3')
assert 'เกมแมว' in store.own_memories(1, 3)[0]['content']
assert store.channel(1, 2)['bot_room'] == 1
assert store.user(1, 4)['optout'] == 1
source = Path('/app/data/bot.sqlite3')
saved = Path('/app/data/backups/smoke.sqlite3')
backup(source, saved)
restore(saved, Path('/app/data/restored.sqlite3'))
restored = Store('/app/data/restored.sqlite3')
assert restored.own_memories(1, 3)[0]['content'] == store.own_memories(1, 3)[0]['content']
assert restored.user(1, 4)['optout'] == 1
assert not healthy(Path('/app/data/restored.sqlite3'))
restored.close()
store.close()
print('Container 2: data survives recreation; backup + new-path restore: PASS')
""",
        )
    finally:
        run("docker", "volume", "rm", volume)


if __name__ == "__main__":
    main()
