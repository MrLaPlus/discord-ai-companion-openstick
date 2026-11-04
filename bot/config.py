from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo


def number(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except ValueError:
        raise ValueError(f"{name} must be an integer") from None
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value


@dataclass(frozen=True)
class Config:
    discord_token: str = field(repr=False)
    api_key: str = field(repr=False)
    base_url: str = "https://api.groq.com/openai/v1"
    model: str = "qwen/qwen3.8-27b"
    guild_id: int | None = None
    additional_guild_ids: tuple[int, ...] = ()
    database: Path = Path("data/bot.sqlite3")
    name: str = "เพื่อนดิส"
    timezone: str = "Asia/Bangkok"
    rpm: int = 20
    rpd: int = 900
    tpm: int = 7000
    tpd: int = 180000
    output_tokens: int = 768
    context_bytes: int = 3500
    queue_size: int = 20
    workers: int = 1
    cooldown: int = 3
    history_days: int = 30
    memory_days: int = 180
    max_messages: int = 100000
    auto_history: bool = True
    history_batch: int = 300
    history_channels: int = 3
    history_total: int = 600
    history_timeout: int = 20
    history_cache: int = 60
    conversation_seconds: int = 1800
    conversation_turns: int = 3
    memory_suggestions: bool = True
    backup_hours: int = 24
    backup_days: int = 7
    alert_percent: int = 80
    auto_respond_channels: bool = False
    mcp_enabled: bool = False
    mcp_url: str = "http://mcp-web:8000/mcp"
    mcp_rpm: int = 6
    mcp_daily: int = 100
    mcp_user_daily: int = 0  # Zero disables the optional per-person limit.

    @classmethod
    def load(cls, require_secrets: bool = True) -> Config:
        from dotenv import load_dotenv

        load_dotenv(override=False)
        token = os.environ.get("DISCORD_TOKEN", "").strip()
        key = os.environ.get("GROQ_API_KEY", "").strip()
        if require_secrets and (not token or not key):
            raise ValueError("Set DISCORD_TOKEN and GROQ_API_KEY in .env before starting")
        base = os.environ.get("GROQ_BASE_URL", cls.base_url).rstrip("/")
        parsed = urlparse(base)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("GROQ_BASE_URL must be an HTTPS URL without credentials")
        timezone = os.environ.get("TIMEZONE", "Asia/Bangkok")
        try:
            ZoneInfo(timezone)
        except KeyError:
            raise ValueError("TIMEZONE must be a valid IANA zone, e.g. Asia/Bangkok") from None
        guild = os.environ.get("DISCORD_GUILD_ID", "").strip()
        additional_raw = os.environ.get("DISCORD_ADDITIONAL_GUILD_IDS", "").strip()
        additional = additional_raw.split(",") if additional_raw else []
        if additional and not guild:
            raise ValueError("DISCORD_ADDITIONAL_GUILD_IDS requires DISCORD_GUILD_ID")
        if len(additional) > 10 or any(
            not value.strip().isascii() or not value.strip().isdecimal() or not 0 < int(value.strip()) < 2**64
            for value in additional
        ):
            raise ValueError("DISCORD_ADDITIONAL_GUILD_IDS must contain at most 10 numeric server IDs")
        additional_ids = tuple(dict.fromkeys(int(value.strip()) for value in additional))
        mcp_url = os.environ.get("MCP_URL", cls.mcp_url)
        mcp_endpoint = urlparse(mcp_url)
        if (
            mcp_endpoint.username
            or mcp_endpoint.password
            or mcp_endpoint.query
            or mcp_endpoint.fragment
            or not mcp_endpoint.hostname
            or (mcp_endpoint.scheme != "https" and mcp_url not in {cls.mcp_url, "http://127.0.0.1:8000/mcp"})
        ):
            raise ValueError("MCP_URL must be the internal sidecar or an operator-approved HTTPS endpoint")
        if guild and (not guild.isascii() or not guild.isdecimal() or not 0 < int(guild) < 2**64):
            raise ValueError("DISCORD_GUILD_ID must be a valid numeric server ID")
        config = cls(
            discord_token=token,
            api_key=key,
            base_url=base,
            model=os.environ.get("GROQ_MODEL", cls.model),
            guild_id=int(guild) if guild else None,
            additional_guild_ids=tuple(value for value in additional_ids if value != int(guild))
            if guild
            else (),
            database=Path(os.environ.get("DATABASE_PATH", "data/bot.sqlite3")),
            name=os.environ.get("BOT_NAME", "เพื่อนดิส")[:50],
            timezone=timezone,
            rpm=number("REQUESTS_PER_MINUTE", 20, 1, 10000),
            rpd=number("REQUESTS_PER_DAY", 900, 1, 1000000),
            tpm=number("TOKENS_PER_MINUTE", 7000, 512, 10000000),
            tpd=number("TOKENS_PER_DAY", 180000, 512, 1000000000),
            output_tokens=number("MAX_OUTPUT_TOKENS", 768, 32, 4096),
            context_bytes=number("MAX_CONTEXT_BYTES", 3500, 256, 20000),
            queue_size=number("QUEUE_SIZE", 20, 1, 100),
            workers=number("WORKERS", 1, 1, 4),
            cooldown=number("USER_COOLDOWN_SECONDS", 3, 0, 300),
            history_days=number("HISTORY_RETENTION_DAYS", 30, 1, 3650),
            memory_days=number("MEMORY_RETENTION_DAYS", 180, 1, 3650),
            max_messages=number("MAX_MESSAGES_PER_GUILD", 100000, 1000, 1000000),
            auto_history=os.environ.get("AUTO_HISTORY_ENABLED", "true").lower() in {"true", "1", "yes"},
            history_batch=number("AUTO_HISTORY_MESSAGES", 300, 1, 1000),
            history_channels=number("AUTO_HISTORY_CHANNELS", 3, 1, 10),
            history_total=number("AUTO_HISTORY_TOTAL", 600, 1, 3000),
            history_timeout=number("AUTO_HISTORY_TIMEOUT", 20, 1, 60),
            history_cache=number("AUTO_HISTORY_CACHE_SECONDS", 60, 0, 3600),
            conversation_seconds=number("CONVERSATION_TTL_SECONDS", 1800, 0, 86400),
            conversation_turns=number("CONVERSATION_TURNS", 3, 1, 8),
            memory_suggestions=os.environ.get("MEMORY_SUGGESTIONS", "true").lower() in {"true", "1", "yes"},
            backup_hours=number("BACKUP_INTERVAL_HOURS", 24, 0, 168),
            backup_days=number("BACKUP_RETENTION_DAYS", 7, 1, 90),
            alert_percent=number("QUOTA_ALERT_PERCENT", 80, 50, 100),
            auto_respond_channels=os.environ.get("AUTO_RESPOND_ACCESSIBLE_CHANNELS", "false").lower()
            in {"true", "1", "yes"},
            mcp_enabled=os.environ.get("MCP_ENABLED", "false").lower() in {"true", "1", "yes"},
            mcp_url=mcp_url,
            mcp_rpm=number("MCP_REQUESTS_PER_MINUTE", 6, 1, 60),
            mcp_daily=number("MCP_REQUESTS_PER_DAY", 100, 1, 10000),
            mcp_user_daily=number("MCP_USER_REQUESTS_PER_DAY", 0, 0, 1000),
        )
        if min(config.tpm, config.tpd) < config.output_tokens + 2000:
            raise ValueError("Token limits must allow MAX_OUTPUT_TOKENS plus at least 2000 input tokens")
        return config
