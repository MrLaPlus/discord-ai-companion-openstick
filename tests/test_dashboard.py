import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from aiohttp import CookieJar
from aiohttp.test_utils import TestClient, TestServer

from bot.dashboard import COOKIE, Dashboard, start_dashboard


class FakeStore:
    def meta(self, key, default=None):
        return {
            "provider_checked": 1000,
            "provider_limits": {
                "checked": 1000,
                "requests_day": {"limit": 100, "remaining": 80},
                "tokens_minute": {"limit": 1000, "remaining": 500},
            },
            "search_provider_status": {"provider": "duckduckgo", "reason": "quota", "checked": 1000},
            "mcp_service_health": {"status": "reachable", "checked": 1000},
        }.get(key, default)


class DashboardTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.bot = SimpleNamespace(
            config=SimpleNamespace(guild_id=1, additional_guild_ids=(2,), discord_token="NEVER_EXPOSE"),
            store=FakeStore(),
            queue=asyncio.Queue(),
            workers=[],
            is_ready=lambda: True,
            provider=SimpleNamespace(last_status="200"),
            operations=SimpleNamespace(
                snapshot=lambda: {
                    "backup": {"created": 1000, "file": "PRIVATE_FILE"},
                    "recovery": {},
                    "backup_error": False,
                }
            ),
        )
        self.dashboard = Dashboard(self.bot, "x" * 40)
        self.client = TestClient(TestServer(self.dashboard.application()), cookie_jar=CookieJar(unsafe=True))
        await self.client.start_server()
        self.host = {"Host": "127.0.0.1:8787"}
        self.post_headers = {**self.host, "Origin": "http://127.0.0.1:8787"}

    async def asyncTearDown(self):
        await self.client.close()

    async def test_auth_origin_host_and_logout(self):
        self.assertEqual((await self.client.get("/api/snapshot", headers=self.host)).status, 401)
        self.assertEqual((await self.client.get("/", headers={"Host": "evil.example"})).status, 400)
        self.assertEqual(
            (
                await self.client.post(
                    "/login",
                    data={"token": "x" * 40},
                    headers={**self.host, "Origin": "https://evil.example"},
                )
            ).status,
            403,
        )
        bad = await self.client.post("/login", data={"token": "bad"}, headers=self.post_headers)
        self.assertEqual(bad.status, 401)
        self.dashboard.login_attempt = 0
        ok = await self.client.post(
            "/login", data={"token": "x" * 40}, headers=self.post_headers, allow_redirects=False
        )
        self.assertEqual(ok.status, 303)
        self.assertEqual(
            (
                await self.client.get("/api/snapshot", headers={**self.host, "Origin": "http://evil.example"})
            ).status,
            403,
        )
        self.assertTrue(ok.cookies[COOKIE]["httponly"])
        self.assertEqual(ok.cookies[COOKIE]["samesite"], "Strict")
        self.assertEqual(
            (await self.client.post("/logout", headers=self.post_headers, allow_redirects=False)).status, 303
        )
        self.assertEqual((await self.client.get("/api/snapshot", headers=self.host)).status, 401)

    async def test_snapshot_is_bounded_cached_and_excludes_private_values(self):
        await self.client.post(
            "/login", data={"token": "x" * 40}, headers=self.post_headers, allow_redirects=False
        )
        with patch(
            "bot.dashboard.board_status",
            new=AsyncMock(
                return_value={"cpu_percent": 10, "ram": {"used": 20}, "temperatures_c": {"cpu-test": 42}}
            ),
        ) as read:
            first = await self.client.get("/api/snapshot", headers=self.host)
            data = await first.json()
            second = await self.client.get("/api/snapshot", headers=self.host)
            self.assertEqual(second.status, 200)
            self.assertEqual(read.await_count, 1)
        self.assertEqual(data["bot"]["guilds_allowed"], 2)
        self.assertEqual(data["search"]["reason"], "quota")
        self.assertNotIn("NEVER_EXPOSE", str(data))
        self.assertNotIn("PRIVATE_FILE", str(data))
        self.assertEqual(first.headers["Cache-Control"], "no-store")
        for i in range(150):
            self.dashboard.samples.append({"at": i})
        self.assertEqual(len(self.dashboard.samples), 120)

    async def test_two_second_poll_refreshes_after_sampling_delay(self):
        await self.client.post(
            "/login", data={"token": "x" * 40}, headers=self.post_headers, allow_redirects=False
        )

        async def delayed_sample():
            await asyncio.sleep(0.05)
            return {"cpu_percent": 10, "temperatures_c": {}}

        with patch("bot.dashboard.board_status", new=AsyncMock(side_effect=delayed_sample)) as read:
            first = await self.client.get("/api/snapshot", headers=self.host)
            self.assertEqual(first.status, 200)
            import time

            self.assertGreater(time.monotonic() - self.dashboard.cached_at, 0.04)
            # Model the next 2-second poll from the start of the previous sample.
            self.dashboard.cached_at -= 1.6
            second = await self.client.get("/api/snapshot", headers=self.host)
            self.assertEqual(second.status, 200)
            self.assertEqual(read.await_count, 2)
            self.assertEqual(len((await second.json())["history"]), 2)

    async def test_missing_token_never_starts_listener(self):
        with patch.dict("os.environ", {}, clear=True):
            self.assertIsNone(await start_dashboard(self.bot))
