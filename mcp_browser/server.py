"""A Playwright-based MCP reader. Only fixed reading code is callable, never page actions."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from urllib.parse import parse_qsl, urlsplit

from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from playwright.async_api import async_playwright

mcp = MCPServer("playwright-reader")
slots = asyncio.Semaphore(1)


def allowed_url(value):
    try:
        p = urlsplit(value)
        return bool(
            len(value) <= 2048
            and not any(ord(c) < 33 for c in value)
            and "\\" not in value
            and p.scheme == "https"
            and p.hostname
            and p.port in (None, 443)
            and p.username is None
            and p.password is None
            and not (
                p.hostname.lower().rstrip(".") in {"discord.com", "discordapp.com"}
                and p.path.startswith(("/channels/", "/api/"))
            )
            and not any(
                k.lower() in {"token", "key", "secret", "password", "session", "api_key"}
                for k, _ in parse_qsl(p.query)
            )
        )
    except (TypeError, ValueError):
        return False


async def read_page(url: str) -> dict:
    """Render a public HTTPS page using isolated Chromium; return text only, no arbitrary actions."""
    if not allowed_url(url):
        return {"ok": False, "error": "invalid_public_url"}
    try:
        async with slots, asyncio.timeout(20), async_playwright() as pw:
            browser = await pw.chromium.launch(
                headless=True,
                proxy={"server": "http://web-proxy:8080"},
                args=[
                    "--no-sandbox",
                    "--proxy-bypass-list=<-loopback>",
                    "--disable-quic",
                    "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
                ],
            )
            try:
                context = await browser.new_context(
                    accept_downloads=False, service_workers="block", permissions=[]
                )

                async def guard(route):
                    try:
                        good = route.request.method == "GET" and allowed_url(route.request.url)
                    except ValueError:
                        good = False
                    if good:
                        await route.continue_()
                    else:
                        await route.abort()

                await context.route("**/*", guard)
                await context.route_web_socket("**/*", lambda ws: ws.close())
                page = await context.new_page()
                response = await page.goto(url, wait_until="domcontentloaded", timeout=12000)
                if response is None or response.status >= 400:
                    raise ValueError("Page unavailable")
                try:
                    await page.wait_for_load_state("networkidle", timeout=1500)
                except Exception:
                    pass  # The bounded DOM may still be readable on continuously polling sites.
                if not allowed_url(page.url):
                    raise ValueError("Redirect not allowed")
                text = await page.evaluate(
                    "() => (document.querySelector('main,article') || document.body)?.innerText.slice(0,16000) || ''"
                )
                data = {
                    "title": (await page.title())[:200],
                    "url": page.url,
                    "text": text.encode()[:4096].decode(errors="ignore"),
                }
                return {"ok": True, "results": [data], "fetched_at": datetime.now(timezone.utc).isoformat()}
            finally:
                await browser.close()
    except Exception:
        return {"ok": False, "error": "browser_unavailable"}


mcp.add_tool(read_page)
if __name__ == "__main__":
    mcp.run(
        transport="streamable-http",
        host="0.0.0.0",
        port=8000,
        json_response=True,
        max_request_body_size=16384,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=["mcp-browser:8000", "localhost:8000", "127.0.0.1:8000"],
            allowed_origins=[],
        ),
    )
