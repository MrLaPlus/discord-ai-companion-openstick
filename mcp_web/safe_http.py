"""Public HTTPS reader with DNS validation pinned to the actual TLS connection."""

from __future__ import annotations

import asyncio
import ipaddress
import re
import socket
from urllib.parse import parse_qsl, urlsplit, urlunsplit

import httpx
from bs4 import BeautifulSoup

MAX_BODY = 512 * 1024


class BlockedURL(ValueError):
    pass


def public_ip(value: str) -> bool:
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return False
    if isinstance(address, ipaddress.IPv6Address) and (
        address.ipv4_mapped or address.sixtofour or address.teredo
    ):
        return False
    return address.is_global and not any(
        (
            address.is_private,
            address.is_loopback,
            address.is_link_local,
            address.is_reserved,
            address.is_multicast,
            address.is_unspecified,
        )
    )


def public_url(value: str) -> str:
    if not isinstance(value, str) or len(value) > 2048 or any(ord(c) < 33 for c in value) or "\\" in value:
        raise BlockedURL("Invalid public URL")
    try:
        parsed = urlsplit(value)
        host = (parsed.hostname or "").encode("idna").decode().lower().rstrip(".")
        port = parsed.port
    except (ValueError, UnicodeError):
        raise BlockedURL("Invalid public URL") from None
    if (
        parsed.scheme != "https"
        or not host
        or parsed.username is not None
        or parsed.password is not None
        or port not in (None, 443)
        or host == "localhost"
        or host.endswith((".localhost", ".local", ".internal"))
    ):
        raise BlockedURL("Only public HTTPS URLs are allowed")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        if not re.fullmatch(r"[a-z0-9.-]+", host) or "." not in host:
            raise BlockedURL("Invalid public host") from None
    else:
        if not public_ip(host):
            raise BlockedURL("Non-public address")
    if host in {"discord.com", "discordapp.com"} and parsed.path.startswith(("/channels/", "/api/")):
        raise BlockedURL("Discord data must use native access checks")
    if any(
        re.search(
            r"token|secret|password|session|signature|credential|api.?key|^auth$|^key$|^code$|^sig$|^jwt$",
            key,
            re.I,
        )
        for key, _ in parse_qsl(parsed.query)
    ):
        raise BlockedURL("Credential-bearing URL is not allowed")
    authority = f"[{host}]" if ":" in host else host
    return urlunsplit(("https", authority, parsed.path or "/", parsed.query, ""))


class PinnedTransport(httpx.AsyncBaseTransport):
    def __init__(self, inner=None, resolver=None):
        self.inner = inner or httpx.AsyncHTTPTransport(retries=0)
        self.resolver = resolver

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        url = httpx.URL(public_url(str(request.url)))
        if request.method != "GET":
            raise BlockedURL("Only public reads are allowed")
        resolver = self.resolver or asyncio.get_running_loop().getaddrinfo
        async with asyncio.timeout(4):
            addresses = await resolver(url.host, 443, type=socket.SOCK_STREAM)
        ips = {info[4][0] for info in addresses}
        if not ips or not all(public_ip(ip) for ip in ips):
            raise BlockedURL("Non-public DNS destination")
        # Literal validated IP reaches connect_tcp; original hostname is used
        # for TLS SNI/certificate verification, not resolved a second time.
        ip = sorted(ips, key=lambda item: (":" in item, item))[0]
        pinned = httpx.Request(
            "GET",
            url.copy_with(host=ip),
            headers={**dict(request.headers), "host": url.host},
            extensions={**request.extensions, "sni_hostname": url.host},
        )
        return await self.inner.handle_async_request(pinned)

    async def aclose(self):
        await self.inner.aclose()


async def fetch_page(url: str, *, transport=None, raw=False) -> dict:
    target = public_url(url)
    async with httpx.AsyncClient(
        transport=transport or PinnedTransport(),
        trust_env=False,
        follow_redirects=False,
        timeout=8,
    ) as client:
        for _ in range(6):
            async with client.stream(
                "GET", target, headers={"User-Agent": "DiscordAICompanion/1.0", "Accept-Encoding": "identity"}
            ) as response:
                if response.status_code in {301, 302, 303, 307, 308}:
                    location = response.headers.get("location")
                    if not location:
                        raise BlockedURL("Invalid redirect")
                    target = public_url(str(httpx.URL(target).join(location)))
                    continue
                response.raise_for_status()
                # Reject compression before decoding to avoid an allocation-heavy decompression bomb.
                if response.headers.get("content-encoding", "identity") not in {"", "identity"}:
                    raise BlockedURL("Compressed pages are unsupported")
                content_type = response.headers.get("content-type", "").split(";")[0].strip().lower()
                if not (
                    content_type.startswith("text/")
                    or content_type in {"application/xhtml+xml", "application/json"}
                    or (
                        raw
                        and content_type in {"application/rss+xml", "application/atom+xml", "application/xml"}
                    )
                ):
                    raise BlockedURL("Only text pages are supported")
                if int(response.headers.get("content-length", "0")) > MAX_BODY:
                    raise BlockedURL("Page too large")
                body = bytearray()
                async for chunk in response.aiter_bytes(8192):
                    body.extend(chunk)
                    if len(body) > MAX_BODY:
                        raise BlockedURL("Page too large")
                if raw:
                    return {"url": target, "body": bytes(body)}
                html = body.decode(response.encoding or "utf-8", errors="replace")
                soup = BeautifulSoup(html, "html.parser")
                title = soup.title.get_text(" ", strip=True)[:200] if soup.title else "Web page"
                for item in soup(["script", "style", "nav", "header", "footer", "form", "noscript"]):
                    item.decompose()
                root = soup.find("main") or soup.find("article") or soup
                text = " ".join(root.get_text(" ", strip=True).split())
                return {"title": title, "url": target, "text": text.encode()[:4096].decode(errors="ignore")}
    raise BlockedURL("Too many redirects")
