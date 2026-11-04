"""Allowlisted Groq response-header telemetry; never infer absent provider quotas."""

from __future__ import annotations

import re
import time
from urllib.parse import urlsplit


def duration(value):
    if not isinstance(value, str) or len(value) > 48:
        return None
    parts = re.findall(r"(\d+(?:\.\d+)?)(ms|s|m|h|d)", value)
    if not parts or "".join(n + unit for n, unit in parts) != value:
        return None
    result = sum(float(n) * {"ms": 0.001, "s": 1, "m": 60, "h": 3600, "d": 86400}[unit] for n, unit in parts)
    return result if 0 <= result <= 86400 else None


def capture(store, headers, model, *, base_url="https://api.groq.com/openai/v1"):
    supported = urlsplit(base_url).hostname == "api.groq.com"
    if not supported:
        headers = {}
    # Header keys are case-insensitive on aiohttp; normalize mock/plain mappings too.
    headers = {k.lower(): v for k, v in headers.items()}

    def number(key):
        value = headers.get(key)
        return int(value) if isinstance(value, str) and re.fullmatch(r"\d{1,12}", value) else None

    report = {
        "model": model,
        "checked": time.time(),
        "source": "Groq response headers" if supported else "unsupported provider quota headers",
    }
    for name, unit in [("requests", "requests_day"), ("tokens", "tokens_minute")]:
        report[unit] = {
            "limit": number("x-ratelimit-limit-" + name),
            "remaining": number("x-ratelimit-remaining-" + name),
            "reset_seconds": duration(headers.get("x-ratelimit-reset-" + name)),
        }
    store.set_meta("provider_limits", report)
    return report


def summary(store):
    return store.meta(
        "provider_limits",
        {
            "checked": None,
            "source": "not yet reported",
            "requests_day": {"limit": None, "remaining": None},
            "tokens_minute": {"limit": None, "remaining": None},
        },
    )


def utilization(store, now):
    report = summary(store)
    checked = report.get("checked")
    if not checked or not 0 <= now - checked < 300:
        return {}
    result = {}
    for key in ["requests_day", "tokens_minute"]:
        row = report.get(key, {})
        cap, remaining = row.get("limit"), row.get("remaining")
        reset = row.get("reset_seconds")
        if cap and remaining is not None and (reset is None or now - checked < reset):
            result[key] = max(0, (cap - remaining) * 100 / cap)
    return result


def display(report):
    def amount(value):
        return f"{value:,}" if isinstance(value, int) else "ยังไม่มีข้อมูล"

    lines = ["โควตาจากผู้ให้บริการ (ค่าที่รายงานครั้งล่าสุด)"]
    for key, label in [("requests_day", "คำขอ/วัน (RPD)"), ("tokens_minute", "Token/นาที (TPM)")]:
        row = report.get(key, {})
        lines.append(f"{label}: เหลือ {amount(row.get('remaining'))} / {amount(row.get('limit'))}")
    checked = report.get("checked")
    lines.append(f"อัปเดต <t:{int(checked)}:R>" if checked else "ยังไม่ได้รับข้อมูลจากผู้ให้บริการ")
    lines.append("RPM/TPD ที่ไม่ได้รายงาน: ยังไม่มีข้อมูล; ไม่มีการคาดเดาจากตัวนับบอท")
    return "\n".join(lines)
