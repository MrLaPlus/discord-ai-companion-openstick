from __future__ import annotations

from . import timing

import asyncio
import base64
import contextvars
import json
import time
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import aiohttp

from .config import Config
from .output import UnavailableOutput, final_text
from .provider_limits import capture
from .quota import Quota, QuotaExceeded


model_choice = contextvars.ContextVar('model_choice', default='auto')


def user_model(store, key):
    value = store.meta(key, 'auto')
    value = value.get('value', 'auto') if isinstance(value, dict) else value
    return value if value in {'auto', 'deepseek', 'qwen', 'groq'} else 'auto'


class ProviderError(Exception):
    """Only safe, fixed messages escape the adapter; never echo provider bodies or headers."""


@dataclass
class Answer:
    text: str
    tokens: int
    truncated: bool = False
    notice: str = ''


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
    async def chat(self, messages: list[dict[str, str]], *, image_bytes: bytes | None = None) -> Answer:
        choice = model_choice.get()
        if choice == 'groq' and self.config.tokenme_key:
            fallback = Provider(replace(self.config, tokenme_key='', openrouter_key=''), self.quota, self.session)
            fallback.category = self.category
            return await fallback.chat(messages, image_bytes=image_bytes)
        if self.config.tokenme_key:
            if image_bytes is not None:
                fallback = Provider(replace(self.config, tokenme_key='', openrouter_key=''), self.quota, self.session)
                fallback.category = self.category
                answer = await fallback.chat(messages, image_bytes=image_bytes)
                answer.notice = 'ℹ️ ใช้ Groq วิเคราะห์ภาพ'
                return answer
            try:
                model = 'unsloth/qwen3.8-27b-nvfp4' if choice == 'qwen' else 'deepseek-ai/deepseek-v4-flash-0731'
                return await self._tokenme(messages, model)
            except ProviderError as error:
                fallback = Provider(replace(self.config, tokenme_key='', openrouter_key=''), self.quota, self.session)
                fallback.category = self.category
                try:
                    answer = await fallback.chat(messages)
                except QuotaExceeded as exc:
                    reason = 'ถึงขีดจำกัดรายวัน' if exc.daily else f'โควตาภายในยังไม่พอ รอประมาณ {int(exc.retry_after + 0.999)} วินาที'
                    raise ProviderError(f'{error}; Groq สำรอง {reason} ครับ') from None
                except ProviderError as exc:
                    raise ProviderError(f'{error}; Groq สำรองใช้งานไม่ได้: {exc}') from None
                answer.notice = f'⚠️ {error} — ใช้ Groq สำรอง'
                return answer
        if self.config.openrouter_key:
            try:
                return await self._openrouter(messages, image_bytes=image_bytes)
            except ProviderError as error:
                try:
                    answer = await self._openrouter(messages, image_bytes=image_bytes, model='dots-studio/dots-3-note-preview:free')
                    answer.notice = f'⚠️ {error} — ใช้ Dots3-Note สำรอง'
                    return answer
                except ProviderError as secondary:
                    prior = f'{error}; Dots3-Note {secondary}'
                fallback = Provider(replace(self.config, openrouter_key=''), self.quota, self.session)
                fallback.category = self.category
                try:
                    answer = await fallback.chat(messages, image_bytes=image_bytes)
                except QuotaExceeded as exc:
                    wait = f'{int(exc.retry_after + 0.999)} วินาที'
                    reason = 'ถึงขีดจำกัดรายวัน' if exc.daily else f'โควตาภายในยังไม่พอ รอประมาณ {wait}'
                    raise ProviderError(f'{prior}; Groq สำรอง {reason} แล้วลองใหม่ครับ') from None
                except ProviderError as exc:
                    raise ProviderError(f'{prior}; Groq สำรองใช้งานไม่ได้: {exc}') from None
                answer.notice = f'⚠️ {prior} — ใช้ Groq สำรอง'
                return answer
        # Byte count is a conservative upper estimate for Qwen's text tokens, plus message framing.
        estimate = sum(len(m["content"].encode()) + 32 for m in messages) + self.config.output_tokens
        if image_bytes is not None:
            if not isinstance(image_bytes, bytes) or not image_bytes.startswith(b"\xff\xd8\xff") or len(image_bytes) > 1_048_576:
                raise ProviderError("รูปไม่รองรับหรือใหญ่เกิน 1 MiB ครับ")
            estimate += 2048  # Groq Qwen 3.8 bills one image as 2048 input tokens.
            messages = [dict(m) for m in messages]
            messages[-1]["content"] = [
                {"type": "text", "text": messages[-1]["content"]},
                {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(image_bytes).decode("ascii")}},
            ]
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
                from .usage_stats import record
                detail = body.get('usage') or {}
                record(self.quota.store, 'groq', success=True, prompt=detail.get('prompt_tokens'), completion=detail.get('completion_tokens'), zone=self.config.timezone)
                self.quota.finish(attempt, usage if usage else None, "ok")
                return Answer(text, usage or 0, choice.get("finish_reason") == "length")
        except (aiohttp.ClientError, asyncio.TimeoutError):
            from .usage_stats import record
            record(self.quota.store, 'groq', success=False, zone=self.config.timezone)
            self.last_status = "network error"
            self.quota.finish(attempt, None, "network error")
            self.quota.defer(10)
            raise ProviderError("เชื่อมต่อ AI ไม่สำเร็จ ลองใหม่ภายหลังครับ") from None
        except asyncio.CancelledError:
            self.quota.finish(attempt, reported_usage, "cancelled")
            raise
        except ProviderError:
            from .usage_stats import record
            record(self.quota.store, 'groq', success=False, zone=self.config.timezone)
            self.quota.finish(attempt, reported_usage, self.last_status)
            raise

    async def _openrouter(self, messages, *, image_bytes=None, model='google/gemma-4-31b-it:free'):
        from .usage_stats import record
        secondary = model == 'dots-studio/dots-3-note-preview:free'
        pause_key = 'openrouter_dots_pause_until' if secondary else 'openrouter_pause_until'
        reason_key = 'openrouter_dots_pause_reason' if secondary else 'openrouter_pause_reason'
        pause = self.quota.store.meta(pause_key, 0)
        if time.time() < pause:
            reason = self.quota.store.meta(reason_key, 'รอหลังข้อผิดพลาด')
            raise ProviderError(f'OpenRouter {reason}')
        if image_bytes is not None:
            if not isinstance(image_bytes, bytes) or not image_bytes.startswith(b'\xff\xd8\xff') or len(image_bytes) > 1048576:
                raise ProviderError('รูปไม่รองรับหรือใหญ่เกิน 1 MiB ครับ')
            messages = [dict(m) for m in messages]
            messages[-1]['content'] = [{'type': 'text', 'text': messages[-1]['content']}, {'type': 'image_url', 'image_url': {'url': 'data:image/jpeg;base64,' + base64.b64encode(image_bytes).decode('ascii')}}]
        payload = {'model': model, 'messages': messages, 'temperature': 0.7, 'max_tokens': self.config.output_tokens}
        success = False
        prompt = completion = None
        try:
            async with self.session.post('https://openrouter.ai/api/v1/chat/completions', json=payload,
                    headers={'Authorization': 'Bearer ' + self.config.openrouter_key}, timeout=aiohttp.ClientTimeout(total=30), allow_redirects=False) as response:
                if response.status != 200:
                    reason = f'HTTP {response.status}'
                    if response.status == 429:
                        reason = 'ติดลิมิต (HTTP 429)'
                        delay = min(retry_seconds(response.headers.get('Retry-After')), 86400)
                    else:
                        delay = 30
                    self.quota.store.set_meta(pause_key, time.time() + delay)
                    self.quota.store.set_meta(reason_key, reason)
                    raise ProviderError(f'OpenRouter {reason}')
                raw = bytearray()
                async for chunk in response.content.iter_chunked(8192):
                    raw.extend(chunk)
                    if len(raw) > 262144:
                        raise ProviderError('OpenRouter ส่งคำตอบใหญ่เกินกำหนด')
                try:
                    body = json.loads(raw)
                    choice = body['choices'][0]
                    content = choice['message']['content']
                    if not isinstance(content, str):
                        raise ValueError
                    value = final_text(content)
                    usage = body.get('usage') or {}
                    if not isinstance(usage, dict):
                        raise ValueError
                    prompt, completion = usage.get('prompt_tokens'), usage.get('completion_tokens')
                except (KeyError, IndexError, TypeError, ValueError, UnavailableOutput):
                    raise ProviderError('OpenRouter ส่งข้อมูลตอบกลับไม่สมบูรณ์') from None
                success = True
                total = usage.get('total_tokens')
                return Answer(value, total if type(total) is int and total >= 0 else 0, choice.get('finish_reason') == 'length')
        except (aiohttp.ClientError, asyncio.TimeoutError):
            self.quota.store.set_meta(pause_key, time.time() + 10)
            self.quota.store.set_meta(reason_key, 'เชื่อมต่อไม่สำเร็จหรือหมดเวลา')
            raise ProviderError('OpenRouter เชื่อมต่อไม่สำเร็จหรือหมดเวลา') from None
        finally:
            record(self.quota.store, 'openrouter', success=success, prompt=prompt, completion=completion, zone=self.config.timezone)

    async def _tokenme(self, messages, model):
        from .usage_stats import record
        success = False
        prompt = completion = None
        try:
            async with self.session.post('https://api.tokenme.limited/api/v1/chat/completions',
                    json={'model':model, 'messages':messages, 'temperature':0.7},
                    headers={'Authorization':'Bearer '+self.config.tokenme_key},
                    timeout=aiohttp.ClientTimeout(total=45), allow_redirects=False) as response:
                if response.status != 200:
                    raise ProviderError(f'TokenMe ใช้งานไม่ได้ (HTTP {response.status})')
                raw=bytearray()
                async for chunk in response.content.iter_chunked(8192):
                    raw.extend(chunk)
                    if len(raw)>262144: raise ProviderError('TokenMe ส่งคำตอบใหญ่เกินกำหนด')
                try:
                    body=json.loads(raw); choice=body['choices'][0]; content=choice['message']['content']
                    if not isinstance(content,str): raise ValueError
                    text=final_text(content); usage=body.get('usage') or {}
                    if not isinstance(usage,dict): raise ValueError
                    prompt,completion=usage.get('prompt_tokens'),usage.get('completion_tokens')
                    total=usage.get('total_tokens')
                except (KeyError,IndexError,TypeError,ValueError,UnavailableOutput):
                    raise ProviderError('TokenMe ส่งข้อมูลตอบกลับไม่สมบูรณ์') from None
                success=True
                return Answer(text,total if type(total) is int and total>=0 else 0,choice.get('finish_reason')=='length')
        except (aiohttp.ClientError,asyncio.TimeoutError):
            raise ProviderError('TokenMe เชื่อมต่อไม่สำเร็จหรือหมดเวลา') from None
        finally:
            record(self.quota.store,'tokenme',success=success,prompt=prompt,completion=completion,zone=self.config.timezone)
