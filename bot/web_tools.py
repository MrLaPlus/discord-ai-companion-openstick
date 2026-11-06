"""Bounded MCP client and RAM-only public evidence; no Discord context is sent here."""

from __future__ import annotations

from . import timing

import asyncio
import hashlib
import hmac
import ipaddress
import json
import re
import secrets
import time
from collections import OrderedDict
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlsplit

import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

TOOL_FIELDS = {
    "search": {"query": "string", "max_results": "integer"},
    "fetch_content": {"url": "string"},
    "search_images": {"query": "string"},
}


class ToolError(Exception):
    """Fixed safe text only, never server response bodies/URLs/queries."""


def safe_tool_error(error):
    # SDK/AnyIO wraps errors raised inside its context in nested exception groups.
    if isinstance(error, ToolError):
        return error
    if isinstance(error, BaseExceptionGroup) and len(error.exceptions) == 1:
        return safe_tool_error(error.exceptions[0])
    return None


def checked_url(value: str) -> str:
    try:
        parsed = urlsplit(value)
        host = parsed.hostname or ""
        port = parsed.port
    except ValueError:
        raise ToolError("ลิงก์ไม่ถูกต้องครับ") from None
    if (
        len(value) > 2048
        or any(ord(c) < 33 for c in value)
        or "\\" in value
        or parsed.scheme != "https"
        or parsed.username is not None
        or parsed.password is not None
        or not host
        or port not in (None, 443)
        or host.lower().endswith((".local", ".localhost", ".internal"))
        or host.lower() == "localhost"
        or (
            host.lower() in {"discord.com", "discordapp.com"}
            and parsed.path.startswith(("/channels/", "/api/"))
        )
        or any(
            re.search(
                r"token|secret|password|session|signature|credential|api.?key|^auth$|^key$|^code$|^sig$|^jwt$",
                k,
                re.I,
            )
            for k, _ in parse_qsl(parsed.query)
        )
    ):
        raise ToolError("อ่านได้เฉพาะหน้าเว็บ HTTPS สาธารณะที่ไม่มีข้อมูลล็อกอินครับ")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        if "." not in host or not re.fullmatch(r"[a-zA-Z0-9.-]+", host):
            raise ToolError("ลิงก์ไม่ถูกต้องครับ") from None
    else:
        if not address.is_global or address.is_private or address.is_reserved or address.is_multicast:
            raise ToolError("ไม่อ่านปลายทางในเครือข่ายภายในครับ")
    return value.split("#", 1)[0]


def needs_query_confirmation(question: str) -> bool:
    return bool(
        re.search(
            r"<#|<@|ในแชท|ในห้อง|ห้องลับ|เมื่อวาน.*คุย|เมื่อกี้|ที่คุย|ที่บอก|จำได้|ที่อยู่|เบอร์โทร|"
            r"รหัส|โทเค็น|password|secret|api.?key|\b\d{9,}\b|\S+@\S+|"
            r"เขา|เค้า|คนนั้น|อันนั้น|เรื่องนั้น|ที่สอง|that|earlier|previous|my friend|"
            r"สุขภาพ|โรค|เพศ|ศาสนา|การเมือง|ข้อมูลส่วนตัว",
            question,
            re.I,
        )
    )


def lookup_candidate(question: str) -> bool:
    from .football import football_request

    return bool(
        re.search(
            r"https?://|ค้นเว็บ|ค้นหา|หาในเว็บ|เช็กเว็บ|เช็คเว็บ|google|search|ล่าสุด|วันนี้|ตอนนี้|"
            r"ผลบอล|สกอ|ข่าว|ราคา|แพตช์|อัปเดต|อัพเดต|เมื่อไร|เท่าไหร่|ใครเป็น|rss|ฟีด|playwright|เบราว์เซอร์",
            question,
            re.I,
        )
    ) or football_request(question)


def explicit_query(question: str) -> str:
    query = re.sub(r"<[@#][!&]?\d+>", "", question).strip()
    query = re.sub(r"^(?:ค้นเว็บ|ค้นหา|หาในเว็บ|search)\s*[:：]?\s*", "", query, flags=re.I)
    if re.search(r"แมนยู", query) and re.search(r"สกอ|ผลบอล|ฟุตบอล", query):
        query = re.sub(r"สกอบอน|สกอบอล|สกอร์(?:บอล)?|ผลบอล", " latest football match result score ", query)
        query = query.replace("แมนยู", " Manchester United ").replace("ล่าสุด", " latest ")
    query = " ".join(query.split())
    if not query or len(query) > 400:
        raise ToolError("ระบุคำค้นสาธารณะสั้น ๆ ไม่เกิน400ตัวอักษรครับ")
    return query


class LimitedStream(httpx2.AsyncByteStream):
    def __init__(self, stream):
        self.stream = stream

    async def __aiter__(self):
        size = 0
        async for chunk in self.stream:
            size += len(chunk)
            if size > 131072:
                raise ToolError("ข้อมูลจากเครื่องมือใหญ่เกินกำหนดครับ")
            yield chunk

    async def aclose(self):
        await self.stream.aclose()


class LimitedTransport(httpx2.AsyncBaseTransport):
    def __init__(self, inner=None):
        self.inner = inner or httpx2.AsyncHTTPTransport(retries=0)

    async def handle_async_request(self, request):
        response = await self.inner.handle_async_request(request)
        if response.headers.get("content-encoding", "identity") not in {"", "identity"}:
            await response.aclose()
            raise ToolError("รูปแบบข้อมูลจากเครื่องมือไม่รองรับครับ")
        if int(response.headers.get("content-length", "0")) > 131072:
            await response.aclose()
            raise ToolError("ข้อมูลจากเครื่องมือใหญ่เกินกำหนดครับ")
        response.stream = LimitedStream(response.stream)
        return response

    async def aclose(self):
        await self.inner.aclose()


@dataclass
class WebPage:
    title: str
    url: str
    text: str
    fetched_at: str


class WebTools:
    def __init__(self, config, store):
        self.config, self.store = config, store
        self.last_status = "disabled" if not config.mcp_enabled else "not checked"
        self.pause_until = 0.0
        self.failures = 0
        self.references: OrderedDict[tuple, list[dict]] = OrderedDict()
        self.semaphore = asyncio.Semaphore(2)
        self.lookups: OrderedDict[tuple, dict] = OrderedDict()

    def clear(self):
        self.references.clear()
        self.lookups.clear()

    def lookup(self, key):
        value = self.lookups.get(key)
        if value and time.monotonic() - value["created"] < 1800:
            return value
        self.lookups.pop(key, None)
        return None

    def save_lookup(self, key, query, pages, *, more=False):
        previous = self.lookup(key) if more else None
        self.lookups[key] = {
            "query": query[:400],
            "seen": list(dict.fromkeys((previous["seen"] if previous else []) + [p.url for p in pages]))[
                -50:
            ],
            "page": previous["page"] + 1 if previous else 1,
            "created": time.monotonic(),
        }
        self.lookups.move_to_end(key)
        while len(self.lookups) > 200:
            self.lookups.popitem(last=False)

    def contains(self, mid):
        return any(mid in r["ids"] for refs in self.references.values() for r in refs)

    def saved_sources(self, key, reply=None):
        refs = [r for r in self.references.get(key, []) if time.monotonic() - r["created"] < 1800]
        if refs:
            self.references[key] = refs
        else:
            self.references.pop(key, None)
        selected = next((r for r in reversed(refs) if reply is None or reply in r["ids"]), None)
        return selected["sources"] if selected else {}

    def save_sources(self, key, ids, sources):
        refs = [r for r in self.references.get(key, []) if time.monotonic() - r["created"] < 1800]
        self.references[key] = (refs + [{"ids": set(ids), "sources": sources, "created": time.monotonic()}])[
            -3:
        ]
        self.references.move_to_end(key)
        while len(self.references) > 200:
            self.references.popitem(last=False)

    def maintain(self):
        for key in list(self.lookups):
            self.lookup(key)
        for key in list(self.references):
            self.saved_sources(key)

    @staticmethod
    def result_data(result):
        # SDK 2.3 emits plain dict return annotations as a single JSON TextContent.
        # Accept only this exact bounded envelope, never concatenate arbitrary text.
        data = result.structured_content
        if data is None and len(result.content) == 1 and result.content[0].type == "text":
            data = json.loads(result.content[0].text)
        if isinstance(data, dict) and data.get("error") == "transcript_unavailable":
            raise ToolError("ดึงซับคลิปนี้ไม่ได้ครับ อาจไม่มีซับไทย/อังกฤษ หรือ YouTube จำกัดการเข้าถึง ยังไม่ได้อ่านเนื้อหาคลิปครับ")
        if result.is_error or not isinstance(data, dict) or data.get("ok") is not True:
            raise ToolError("ค้นหรืออ่านเว็บไม่สำเร็จครับ ยังไม่มีผลที่ยืนยันได้ ลองใหม่ภายหลังได้")
        return data

    def reserve(self, guild, user, tool):
        now = time.time()
        salt = self.store.meta("mcp_usage_salt")
        if not salt:
            salt = secrets.token_hex(32)
            self.store.set_meta("mcp_usage_salt", salt)
        identity = hmac.new(salt.encode(), f"{guild}:{user}".encode(), hashlib.sha256).hexdigest()
        # One SQLite transaction across read/count/write; no await or nested metadata commit.
        with self.store.db:
            self.store.db.execute("BEGIN IMMEDIATE")
            row = self.store.db.execute("SELECT value FROM metadata WHERE key='mcp_usage'").fetchone()
            usage = [r for r in json.loads(row[0] if row else "[]") if r[0] > now - 172800]
            if (
                sum(r[0] > now - 60 for r in usage) >= self.config.mcp_rpm
                or sum(r[0] > now - 86400 for r in usage) >= self.config.mcp_daily
                or (
                    self.config.mcp_user_daily > 0
                    and sum(r[0] > now - 86400 and r[1] == identity for r in usage)
                    >= self.config.mcp_user_daily
                )
            ):
                raise ToolError("เครื่องมือค้นข้อมูลยังรับงานเพิ่มไม่ได้ในรอบนี้ ลองใหม่ภายหลังครับ")
            # Short-lived pseudonymous counters only; no query text, URLs or raw user IDs.
            usage.append([now, identity, tool])
            self.store.db.execute(
                "INSERT OR REPLACE INTO metadata VALUES ('mcp_usage',?)", (json.dumps(usage),)
            )

    async def check_health(self):
        if not self.config.mcp_enabled:
            result = {"status": "disabled", "checked": time.time()}
        else:
            try:
                async with asyncio.timeout(12):
                    async with httpx2.AsyncClient(
                        transport=LimitedTransport(),
                        trust_env=False,
                        follow_redirects=False,
                        timeout=10,
                        headers={"Accept-Encoding": "identity"},
                    ) as http:
                        transport = streamable_http_client(
                            self.config.mcp_url, http_client=http, max_sse_event_size=131072
                        )
                        async with Client(
                            transport, read_timeout_seconds=10, cache=None, input_required_max_rounds=0
                        ) as client:
                            listing = await client.list_tools()
                            tools = {t.name: t for t in listing.tools}
                            schemas = {name: t.input_schema for name, t in tools.items()}
                            pin = hashlib.sha256(json.dumps(schemas, sort_keys=True).encode()).hexdigest()
                            if set(tools) != set(TOOL_FIELDS):
                                raise ToolError("schema mismatch")
                            pinned = self.store.meta("mcp_web_schema_usb_search_fetch_images_v3")
                            if pinned and pin != pinned:
                                raise ToolError("schema mismatch")
                result = {
                    "status": "reachable" if pinned else "unverified",
                    "checked": time.time(),
                    "note": "discovery only; schema will be validated on first search",
                }
            except asyncio.CancelledError:
                raise
            except Exception:
                result = {"status": "unavailable", "checked": time.time()}
        self.store.set_meta("mcp_service_health", result)
        return result

    @timing.timed("web_tool")
    async def call(self, name: str, arguments: dict, *, guild: int, user: int) -> list[WebPage]:
        if not self.config.mcp_enabled or name not in TOOL_FIELDS:
            raise ToolError("เครื่องมือเว็บยังไม่ได้เปิดใช้ครับ")
        if time.monotonic() < self.pause_until:
            raise ToolError("เครื่องมือเว็บพักชั่วคราวครับ ลองใหม่อีกสักครู่")
        if name in {"search", "rss_news", "search_images"}:
            if set(arguments) != {"query"} or not isinstance(arguments["query"], str):
                raise ToolError("คำค้นไม่ถูกต้องครับ")
            arguments = {"query": explicit_query(arguments["query"])}
            if name == "search":
                arguments["max_results"] = 5
            if re.search(
                r"gsk_|tvly-|sk-[a-zA-Z0-9]{12}|password\s*[:=]|secret\s*[:=]", arguments["query"], re.I
            ):
                raise ToolError("ไม่ส่งรหัสหรือคีย์ไปค้นเว็บครับ ลบข้อมูลลับจากคำค้นก่อน")
        else:
            if set(arguments) != {"url"} or not isinstance(arguments["url"], str):
                raise ToolError("ลิงก์ไม่ถูกต้องครับ")
            arguments = {"url": checked_url(arguments["url"])}
        self.reserve(guild, user, name)
        try:
            # Two searches may queue behind Tavily before a bounded fallback begins.
            timeout = 24
            read_timeout = timeout - 2
            async with self.semaphore, asyncio.timeout(timeout):
                async with httpx2.AsyncClient(
                    transport=LimitedTransport(),
                    trust_env=False,
                    follow_redirects=False,
                    timeout=read_timeout,
                    headers={"Accept-Encoding": "identity"},
                ) as http:
                    transport = streamable_http_client(
                        self.config.mcp_url, http_client=http, max_sse_event_size=131072
                    )
                    async with Client(
                        transport, read_timeout_seconds=read_timeout, cache=None, input_required_max_rounds=0
                    ) as client:
                        listing = await client.list_tools()
                        tools = {t.name: t for t in listing.tools}
                        if set(tools) != set(TOOL_FIELDS):
                            raise ToolError("รายการเครื่องมือเปลี่ยน ต้องให้ผู้ดูแลตรวจครับ")
                        schemas = {t: tools[t].input_schema for t in tools}
                        expected = TOOL_FIELDS
                        for tool, schema in schemas.items():
                            encoded = json.dumps(schema, sort_keys=True)
                            if (
                                len(encoded) > 8192
                                or '"$ref"' in encoded
                                or set(schema.get("properties", {})) != set(expected[tool])
                                or schema.get("type") != "object"
                                or schema.get("required")
                                != (["query"] if tool in {"search", "rss_news", "search_images"} else ["url"])
                                or any(
                                    schema["properties"][field].get("type") != expected[tool][field]
                                    for field in expected[tool]
                                )
                            ):
                                raise ToolError("schema เครื่องมือไม่ตรงกับแบบที่อนุญาตครับ")
                        fingerprint = hashlib.sha256(json.dumps(schemas, sort_keys=True).encode()).hexdigest()
                        pinned = self.store.meta("mcp_web_schema_usb_search_fetch_images_v3")
                        if pinned and pinned != fingerprint:
                            raise ToolError("schema เครื่องมือเปลี่ยน ต้องให้ผู้ดูแลตรวจครับ")
                        if not pinned:
                            self.store.set_meta("mcp_web_schema_usb_search_fetch_images_v3", fingerprint)
                        # Low-level one-shot call avoids SDK auto-retry/relist on schema mismatch.
                        result = await client.session.call_tool(
                            name, arguments, read_timeout_seconds=read_timeout
                        )
                        data = self.result_data(result)
                        provider = data.get("provider")
                        reason = data.get("fallback_reason", "")
                        if provider in {"tavily", "duckduckgo"} and reason in {
                            "",
                            "missing_key",
                            "quota",
                            "invalid_key",
                            "rate_limit",
                            "temporary_error",
                        }:
                            self.store.set_meta(
                                "search_provider_status",
                                {"provider": provider, "reason": reason, "checked": time.time()},
                            )
                        raw = data.get("results")
                        if not isinstance(raw, list) or len(raw) > 5:
                            raise ToolError("ข้อมูลจากเครื่องมือไม่ถูกต้องครับ")
                        pages = []
                        for page in raw:
                            if not isinstance(page, dict) or not all(
                                isinstance(page.get(k), str) for k in ("url", "title", "text")
                            ):
                                raise ToolError("ข้อมูลจากเครื่องมือไม่ถูกต้องครับ")
                            pages.append(
                                WebPage(
                                    page["title"][:200],
                                    checked_url(page["url"]),
                                    checked_url(page["text"]) if name == "search_images" else page["text"].encode()[:4096].decode(errors="ignore"),
                                    str(data.get("fetched_at", ""))[:40],
                                )
                            )
            self.failures = 0
            self.last_status = "ok"
            self.store.set_meta(f"tool_health:{name}", {"status": "ok", "checked": time.time()})
            return pages
        except asyncio.CancelledError:
            raise
        except Exception as error:
            self.failures += 1
            self.last_status = "unavailable"
            self.store.set_meta(f"tool_health:{name}", {"status": "unavailable", "checked": time.time()})
            if self.failures >= 3:
                self.pause_until = time.monotonic() + 60
            safe = safe_tool_error(error)
            if safe is not None:
                raise safe from None
            raise ToolError("เชื่อมเครื่องมือเว็บไม่สำเร็จครับ ลองใหม่ภายหลังได้") from None
