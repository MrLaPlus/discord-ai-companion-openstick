"""Read-only local dashboard. It shares the bot process and exposes no chat data."""

from __future__ import annotations

import asyncio
import hmac
import logging
import os
import secrets
import time
from collections import deque
from pathlib import Path
from urllib.parse import urlsplit

from aiohttp import web

from .board_status import board_status
from .provider_limits import summary as quota_summary

log = logging.getLogger(__name__)
ASSETS = Path(__file__).parent / "dashboard"
COOKIE = "usb_status_session"


class Dashboard:
    def __init__(self, bot, token: str):
        self.bot = bot
        self.token = token
        self.sessions: dict[str, float] = {}
        self.samples = deque(maxlen=120)
        self.cached = None
        self.cached_at = 0.0
        self.lock = asyncio.Lock()
        self.login_attempt = 0.0

    @staticmethod
    def host_ok(request):
        try:
            host = urlsplit("//" + request.host).hostname
            return host in {"localhost", "127.0.0.1"}
        except ValueError:
            return False

    @staticmethod
    def origin_ok(request):
        return request.headers.get("Origin") in {"http://127.0.0.1:8787", "http://localhost:8787"}

    def authenticated(self, request):
        session = request.cookies.get(COOKIE, "")
        return bool(session and self.sessions.get(session, 0) > time.monotonic())

    async def index(self, request):
        if not self.host_ok(request):
            raise web.HTTPBadRequest()
        name = "index.html" if self.authenticated(request) else "login.html"
        return self.asset_response(name, "text/html")

    @staticmethod
    def asset_response(name, content_type):
        text = (ASSETS / name).read_text(encoding="utf-8")
        return web.Response(
            text=text,
            content_type=content_type,
            headers={
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
                "Content-Security-Policy": "default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'",
            },
        )

    async def asset(self, request):
        if not self.host_ok(request):
            raise web.HTTPBadRequest()
        name = request.match_info["name"]
        allowed = {"style.css": "text/css", "app.js": "application/javascript"}
        if name not in allowed:
            raise web.HTTPNotFound()
        return self.asset_response(name, allowed[name])

    async def login(self, request):
        if not self.host_ok(request) or not self.origin_ok(request):
            raise web.HTTPForbidden()
        if time.monotonic() - self.login_attempt < 1:
            raise web.HTTPTooManyRequests()
        self.login_attempt = time.monotonic()
        try:
            values = await request.post()
            candidate = values.get("token", "")
            valid = isinstance(candidate, str) and hmac.compare_digest(
                candidate.encode(), self.token.encode()
            )
        except (ValueError, UnicodeError):
            valid = False
        if not valid:
            return web.Response(
                text="รหัสไม่ถูกต้อง กลับไปลองใหม่", status=401, headers={"Cache-Control": "no-store"}
            )
        now = time.monotonic()
        self.sessions = {k: v for k, v in self.sessions.items() if v > now}
        if len(self.sessions) >= 8:
            self.sessions.pop(next(iter(self.sessions)))
        session = secrets.token_urlsafe(32)
        self.sessions[session] = now + 12 * 3600
        response = web.HTTPSeeOther("/")
        response.set_cookie(COOKIE, session, httponly=True, samesite="Strict", max_age=12 * 3600, path="/")
        response.headers["Cache-Control"] = "no-store"
        raise response

    async def logout(self, request):
        if not self.host_ok(request) or not self.origin_ok(request):
            raise web.HTTPForbidden()
        self.sessions.pop(request.cookies.get(COOKIE, ""), None)
        response = web.HTTPSeeOther("/")
        response.del_cookie(COOKIE, path="/")
        raise response

    async def snapshot(self, request):
        if not self.host_ok(request):
            raise web.HTTPBadRequest()
        if request.headers.get("Origin") and not self.origin_ok(request):
            raise web.HTTPForbidden()
        if not self.authenticated(request):
            raise web.HTTPUnauthorized()
        async with self.lock:
            now = time.monotonic()
            if self.cached is None or now - self.cached_at >= 1.5:
                board = await board_status()
                bot = self.bot
                timestamp = time.time()
                self.samples.append(
                    {
                        "at": timestamp,
                        "cpu": board.get("cpu_percent"),
                        "ram": board.get("ram", {}).get("used"),
                        "temp": max(
                            (v for k, v in board.get("temperatures_c", {}).items() if k.startswith("cpu")),
                            default=None,
                        ),
                    }
                )
                source = bot.store.meta("search_provider_status", {})
                mcp = bot.store.meta("mcp_service_health", {})
                quota = quota_summary(bot.store)
                operations = bot.operations.snapshot()
                self.cached = {
                    "at": timestamp,
                    "board": board,
                    "history": list(self.samples),
                    "bot": {
                        "ready": bool(
                            bot.is_ready() and bot.workers and all(not t.done() for t in bot.workers)
                        ),
                        "queue": bot.queue.qsize(),
                        "workers_alive": sum(not t.done() for t in bot.workers),
                        "workers_total": len(bot.workers),
                        "guilds_allowed": (1 + len(bot.config.additional_guild_ids))
                        if bot.config.guild_id
                        else None,
                    },
                    "groq": {
                        "last_status": str(getattr(bot.provider, "last_status", "not called"))[:40],
                        "checked": bot.store.meta("provider_checked"),
                        "quota": {
                            key: {
                                field: quota.get(key, {}).get(field)
                                if isinstance(quota.get(key, {}).get(field), int)
                                else None
                                for field in ("limit", "remaining")
                            }
                            for key in ("requests_day", "tokens_minute")
                        },
                        "quota_checked": quota.get("checked"),
                    },
                    "search": {
                        "provider": source.get("provider")
                        if source.get("provider") in {"tavily", "duckduckgo"}
                        else None,
                        "reason": source.get("reason")
                        if source.get("reason")
                        in {"", "missing_key", "quota", "invalid_key", "rate_limit", "temporary_error"}
                        else None,
                        "checked": source.get("checked"),
                    },
                    "mcp": {
                        "status": mcp.get("status")
                        if mcp.get("status") in {"reachable", "unverified", "unavailable", "disabled"}
                        else "unknown",
                        "checked": mcp.get("checked"),
                    },
                    "backup": {
                        "created": operations.get("backup", {}).get("created"),
                        "error": bool(operations.get("backup_error")),
                        "recovery_ok": operations.get("recovery", {}).get("ok"),
                        "recovery_checked": operations.get("recovery", {}).get("checked"),
                    },
                }
                # Anchor at request start so a 2-second poll is not stretched by sampling time.
                self.cached_at = now
        return web.json_response(self.cached, headers={"Cache-Control": "no-store"})

    def application(self):
        app = web.Application(client_max_size=2048)
        app.add_routes(
            [
                web.get("/", self.index),
                web.get("/assets/{name}", self.asset),
                web.post("/login", self.login),
                web.post("/logout", self.logout),
                web.get("/api/snapshot", self.snapshot),
            ]
        )
        return app


async def start_dashboard(bot):
    token = os.environ.get("OPS_TOKEN", "").strip()
    if not token:
        log.warning("Local dashboard disabled: OPS_TOKEN missing")
        return None
    if len(token) < 32 or len(token) > 256:
        log.error("Local dashboard disabled: invalid OPS_TOKEN length")
        return None
    runner = web.AppRunner(Dashboard(bot, token).application(), access_log=None)
    try:
        await runner.setup()
        await web.TCPSite(runner, "127.0.0.1", 8787).start()
    except OSError:
        await runner.cleanup()
        log.error("Local dashboard could not start")
        return None
    return runner
