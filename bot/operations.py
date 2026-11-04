"""Local backup scheduling and rate-limited, content-free operator alerts."""

from __future__ import annotations

import asyncio
import logging
import re
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import discord

from .backup import backup, recovery_drill

log = logging.getLogger(__name__)
AUTO_FILE = re.compile(r"auto-(\d{8}T\d{6}Z)-[a-f0-9]{8}\.sqlite3\Z")


def automatic_backup(source: Path, days: int, now: float) -> Path:
    folder = source.parent / "backups"
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    folder.chmod(0o700)
    stamp = datetime.fromtimestamp(now, timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    destination = folder / f"auto-{stamp}-{uuid.uuid4().hex[:8]}.sqlite3"
    partial = destination.with_suffix(".partial")
    try:
        backup(source, partial)
        partial.rename(destination)
    except Exception:
        partial.unlink(missing_ok=True)
        raise
    # Prune only filenames owned by this scheduler, and only after a verified new snapshot.
    snapshots = []
    for path in folder.iterdir():
        match = AUTO_FILE.fullmatch(path.name)
        if not match or path.is_symlink() or not path.is_file():
            continue
        try:
            created = datetime.strptime(match[1], "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc).timestamp()
        except ValueError:
            continue
        snapshots.append((created, path))
    for index, (created, path) in enumerate(sorted(snapshots, reverse=True)):
        if path != destination and (created < now - days * 86400 or index >= 32):
            path.unlink()
    return destination


class Operations:
    def __init__(self, bot):
        self.bot = bot
        self.retry_backup = 0.0
        self.drilling = False
        self.failures: dict[tuple[int, str], float] = {}

    def fault(self, guild: int, category: str):
        if len(self.failures) < 200 or (guild, category) in self.failures:
            self.failures[guild, category] = time.time()

    def snapshot(self) -> dict:
        store, config = self.bot.store, self.bot.config
        last = store.meta("last_auto_backup", {})
        return {
            "backup_interval_hours": config.backup_hours,
            "backup_retention_days": config.backup_days,
            "backup": last,
            "backup_error": store.meta("auto_backup_error", False),
            "recovery": store.meta("last_recovery", {}),
            "last_alert": store.meta("last_operator_alert", {}),
        }

    async def tick(self, now: float | None = None):
        now = time.time() if now is None else now
        bot, store, config = self.bot, self.bot.store, self.bot.config
        last = store.meta("last_auto_backup", {})
        if (
            config.backup_hours
            and now >= self.retry_backup
            and now >= last.get("created", 0) + config.backup_hours * 3600
        ):
            try:
                saved = await asyncio.to_thread(automatic_backup, config.database, config.backup_days, now)
            except Exception as error:
                store.set_meta("auto_backup_error", True)
                self.retry_backup = now + 600
                log.error("Automatic backup failed: %s (details omitted)", type(error).__name__)
            else:
                store.set_meta("last_auto_backup", {"created": now, "file": saved.name})
                store.set_meta("auto_backup_error", False)
                self.retry_backup = 0
        last_drill = store.meta("last_recovery", {})
        snapshot = store.meta("last_auto_backup", {})
        if (
            not self.drilling
            and config.backup_hours
            and snapshot.get("file")
            and now >= last_drill.get("checked", now) + 7 * 86400
        ):
            self.drilling = True
            try:
                result = await asyncio.to_thread(
                    recovery_drill,
                    config.database.parent / "backups" / snapshot["file"],
                    config.database.parent,
                )
            except Exception:
                result = {"ok": False}
            finally:
                self.drilling = False
            store.set_meta("last_recovery", {**result, "checked": now})
        elif not last_drill:
            store.set_meta("last_recovery", {"ok": None, "checked": now})
        if (
            getattr(bot, "web", None) is not None
            and config.mcp_enabled
            and now - store.meta("mcp_service_health", {}).get("checked", 0) >= 300
        ):
            await bot.web.check_health()
        conditions = {}
        if store.meta("last_recovery", {}).get("ok") is False:
            conditions["recovery"] = "ทดสอบกู้คืน backup ไม่สำเร็จ ตรวจ /panel หรือ /status"
        if store.meta("auto_backup_error", False):
            conditions["backup"] = "สำรองข้อมูลอัตโนมัติไม่สำเร็จ ตรวจพื้นที่และสิทธิ์เขียน /status"
        if bot.provider and bot.provider.last_status in {"401", "403", "404"}:
            conditions["provider"] = "AI ใช้งานไม่ได้ ตรวจคีย์ สิทธิ์ และชื่อโมเดล /status"
        elif bot.provider and bot.provider.last_status == "429":
            conditions["provider"] = "AI ติดโควตาผู้ให้บริการ บอทพักตาม Retry-After แล้ว ตรวจ /status"
        from .provider_limits import utilization

        usage = utilization(store, now)
        if max(usage.values(), default=0) >= config.alert_percent:
            conditions["quota"] = f"โควตาที่ผู้ให้บริการรายงานใช้ถึง {config.alert_percent}% แล้ว ตรวจ /status"
        if any(t.done() for t in bot.workers):
            conditions["worker"] = "ตัวประมวลผลคำถามหยุดทำงาน ตรวจ log และ /status"
        for guild in bot.guilds:
            if not bot.in_scope(guild.id):
                continue
            notices = dict(conditions)
            for (gid, category), created in list(self.failures.items()):
                if now - created > 3600:
                    del self.failures[gid, category]
                elif gid == guild.id:
                    notices[category] = "บอทพบข้อขัดข้องระหว่างทำงาน ตรวจ /status และ log (ไม่มีเนื้อหาแชทในแจ้งเตือน)"
            for category, text in notices.items():
                await self.notify(guild, category, text, now)

    async def notify(self, guild, category: str, text: str, now: float):
        bot, store = self.bot, self.bot.store
        settings = store.meta(f"alerts:{guild.id}", {"enabled": True, "channel": 0})
        if not settings.get("enabled", True):
            return
        key = f"alert_time:{guild.id}:{category}"
        previous = store.meta(key, {})
        if now < previous.get("retry", 0) or now - previous.get("sent", 0) < 21600:
            return
        try:
            if settings.get("channel"):
                channels, member = await asyncio.gather(
                    guild.fetch_channels(), guild.fetch_member(bot.user.id)
                )
                recipient = next((c for c in channels if c.id == settings["channel"]), None)
                if not isinstance(recipient, discord.TextChannel):
                    raise ValueError("Alert channel unavailable")
                perms = recipient.permissions_for(member)
                if not perms.view_channel or not perms.send_messages:
                    raise ValueError("Alert channel not writable")
            else:
                fresh = await bot.fetch_guild(guild.id)
                recipient = await bot.fetch_user(fresh.owner_id)
            await recipient.send(
                f"แจ้งเตือนบอท — {guild.name}\n{text}",
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except (discord.HTTPException, ValueError):
            store.set_meta(key, {**previous, "retry": now + 600})
            store.set_meta(
                "last_operator_alert", {"guild": guild.id, "category": category, "sent": False, "time": now}
            )
        else:
            store.set_meta(key, {"sent": now})
            store.set_meta(
                "last_operator_alert", {"guild": guild.id, "category": category, "sent": True, "time": now}
            )

    async def run(self):
        await self.bot.wait_until_ready()
        while True:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception as error:
                log.error("Operations loop failed: %s (details omitted)", type(error).__name__)
            await asyncio.sleep(30)
