"""Bounded, content-free timings for the current job."""
from __future__ import annotations

import asyncio
import contextvars
import functools
import time

current = contextvars.ContextVar("job_durations", default=None)


def add(name: str, seconds: float) -> None:
    trace = current.get()
    if trace is not None:
        trace[name] = trace.get(name, 0.0) + max(0.0, seconds)


def timed(name):
    def decorate(fn):
        @functools.wraps(fn)
        async def wrapped(*args, **kwargs):
            started = time.monotonic()
            try:
                return await fn(*args, **kwargs)
            finally:
                label = name(args[0]) if callable(name) else name
                add(label, time.monotonic() - started)
        return wrapped
    return decorate


async def sleep(seconds: float) -> None:
    started = time.monotonic()
    try:
        await asyncio.sleep(seconds)
    finally:
        add("quota_wait", time.monotonic() - started)


def timed_sync(name):
    def decorate(fn):
        @functools.wraps(fn)
        def wrapped(*args, **kwargs):
            started = time.monotonic()
            try:
                return fn(*args, **kwargs)
            finally:
                add(name, time.monotonic() - started)
        return wrapped
    return decorate


async def awaited(name, awaitable):
    started = time.monotonic()
    try:
        return await awaitable
    finally:
        add(name, time.monotonic() - started)
