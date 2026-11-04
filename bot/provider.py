from __future__ import annotations

from . import timing

import asyncio
import json
import time
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import aiohttp

from .config import Config
from .output import UnavailableOutput, final_text
from .provider_limits import capture
from .quota import Quota


class ProviderError(Exception):
    """Only safe, fixed messages escape the adapter; never echo provider bodies or headers."""


@dataclass
class Answer:
    text: str
    tokens: int
    truncated: bool = False


def retry_seconds(value: str | None) -> float:
    try:
        return max(1, float(value))
    except (ValueError, TypeError):
        try:
            return max(1, (parsedate_to_datetime(value) - datetime.now(timezone.utc)).total_seconds())
        except (ValueError, TypeError, OverflowError):
            return 60


class Provider:
    def __init__(self, config: Config, quota: Quota, session: aiohttp.ClientSession):
        self.config, self.quota, self.session = config, quota, session
        self.last_status = "not called"
        self.category = "chat"

    async def plan_public(self, question: str) -> tuple[str, str] | None:
        """Compact validated JSON planner: sees only this utterance, never Discord context.

        MCP orchestration is local. This avoids exposing arbitrary remote tool
        descriptions/schemas and keeps the existing final-answer adapter intact.
        """
        planner = Provider(replace(self.config, output_tokens=160), self.quota, self.session)
        planner.category = "planner"
        try:
            answer = await planner.chat(
                [
                    {
                        "role": "system",
                        "content": "Classify this public lookup request. Output only JSON with exactly action and value. "
                        "action is search, fetch, or none. Search for fresh facts/news/prices; fetch only an explicit "
                        "public HTTPS URL in the message. For banter, personal conversation, references to earlier "
                        "chat, or insufficient context choose none. value is a short public query/URL, or empty for none. "
                        "Never invent personal facts or a location. Current date: "
                        + datetime.now(timezone.utc).date().isoformat(),
                    },
                    {"role": "user", "content": question[:1000]},
                ]
            )
            text = answer.text.strip()
            if text.startswith("```json") and text.endswith("```"):
                text = text[7:-3].strip()
            plan = json.loads(text)
            if (
                not isinstance(plan, dict)
                or set(plan) != {"action", "value"}
                or plan["action"] not in {"none", "search", "fetch"}
                or not isinstance(plan["value"], str)
            ):
                raise ValueError
            if plan["action"] == "none":
                return None
            if not plan["value"] or len(plan["value"]) > (400 if plan["action"] == "search" else 2048):
                raise ValueError
            return plan["action"], plan["value"]
        except (ValueError, TypeError):
            raise ProviderError("ยังเลือกคำค้นสาธารณะไม่ได้ครับ ระบุคำค้นหรือส่งลิงก์ให้ชัดเจนได้เลย") from None
        finally:
            self.last_status = planner.last_status

    @timing.timed(lambda self: "ai_planner" if self.category == "planner" else "ai_answer")
    async def chat(self, messages: list[dict[str, str]]) -> Answer:
        # Byte count is a conservative upper estimate for Qwen's text tokens, plus message framing.
        estimate = sum(len(m["content"].encode()) + 32 for m in messages) + self.config.output_tokens
        attempt = self.quota.reserve(estimate, category=self.category)
        reported_usage = None
        payload = {
            "model": self.config.model,
            "messages": messages,
            "temperature": 0.7,
            "max_completion_tokens": self.config.output_tokens,
            "reasoning_effort": "none",
            "reasoning_format": "hidden",
        }
        try:
            async with self.session.post(
                self.config.base_url + "/chat/completions",
                json=payload,
                headers={"Authorization": "Bearer " + self.config.api_key},
                timeout=aiohttp.ClientTimeout(total=30),
                allow_redirects=False,
            ) as response:
                self.last_status = str(response.status)
                capture(self.quota.store, response.headers, self.config.model, base_url=self.config.base_url)
                self.quota.store.set_meta("provider_checked", time.time())
                if response.status == 429:
                    self.quota.defer(retry_seconds(response.headers.get("Retry-After")))
                    raise ProviderError("AI ติดโควตาผู้ให้บริการ พักตามเวลาที่แจ้งแล้วครับ")
                if response.status >= 500:
                    self.quota.defer(30)
                    raise ProviderError("AI ขัดข้องชั่วคราว ลองใหม่ภายหลังครับ")
                if response.status != 200:
                    raise ProviderError(f"AI ใช้งานไม่ได้ (HTTP {response.status}) ให้แอดมินตรวจคีย์และโมเดลครับ")
                raw = bytearray()
                async for chunk in response.content.iter_chunked(8192):
                    raw.extend(chunk)
                    if len(raw) > 262144:
                        raise ProviderError("AI ส่งคำตอบใหญ่เกินขีดจำกัด")
                try:
                    body = json.loads(raw)
                    choice = body["choices"][0]
                    text = choice["message"]["content"]
                    usage = body.get("usage", {}).get("total_tokens")
                    if (
                        not isinstance(text, str)
                        or not text.strip()
                        or (
                            usage is not None
                            and (not isinstance(usage, int) or isinstance(usage, bool) or usage < 0)
                        )
                    ):
                        raise ValueError
                except (KeyError, IndexError, TypeError, ValueError):
                    raise ProviderError("AI ส่งข้อมูลตอบกลับไม่สมบูรณ์") from None
                reported_usage = usage if usage else None
                try:
                    text = final_text(text)
                except UnavailableOutput as error:
                    self.last_status = "blocked internal output"
                    raise ProviderError(str(error)) from None
                self.quota.finish(attempt, usage if usage else None, "ok")
                return Answer(text, usage or 0, choice.get("finish_reason") == "length")
        except (aiohttp.ClientError, asyncio.TimeoutError):
            self.last_status = "network error"
            self.quota.finish(attempt, None, "network error")
            self.quota.defer(10)
            raise ProviderError("เชื่อมต่อ AI ไม่สำเร็จ ลองใหม่ภายหลังครับ") from None
        except asyncio.CancelledError:
            self.quota.finish(attempt, reported_usage, "cancelled")
            raise
        except ProviderError:
            self.quota.finish(attempt, reported_usage, self.last_status)
            raise
