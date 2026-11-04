"""Bounded RAM-only assistant turns; never included in SQLite backups."""

from __future__ import annotations

import time
from collections import OrderedDict
from dataclasses import dataclass


def scope(message) -> tuple[int, int, int, bool]:
    return (
        message.guild.id,
        message.channel.id,
        message.author.id,
        getattr(message, "private", False) is True,
    )


@dataclass
class Turn:
    question: str
    answer: str
    sources: dict[str, str]
    fingerprints: dict[int, str]
    channels: list[int]
    message_ids: set[int]
    created: float


class Conversation:
    def __init__(self, config):
        self.config = config
        self.sessions: OrderedDict[tuple, list[Turn]] = OrderedDict()

    def clear(self):
        self.sessions.clear()

    def contains(self, mid: int) -> bool:
        return any(mid in turn.message_ids for turns in self.sessions.values() for turn in turns)

    def get(self, key, allowed: list[int]) -> list[Turn]:
        cutoff = time.monotonic() - self.config.conversation_seconds
        # Never reintroduce a turn derived from a now-ineligible source channel.
        turns = [
            t for t in self.sessions.get(key, []) if t.created > cutoff and set(t.channels) <= set(allowed)
        ]
        if turns:
            self.sessions[key] = turns
        else:
            self.sessions.pop(key, None)
        return turns

    def add(self, key, question, answer, sources, fingerprints, channels, message_ids):
        if not self.config.conversation_seconds:
            return
        turn = Turn(
            question.encode()[:1200].decode(errors="ignore"),
            answer.encode()[:8000].decode(errors="ignore"),
            dict(sources),
            dict(fingerprints),
            list(channels),
            set(message_ids),
            time.monotonic(),
        )
        turns = self.get(key, channels)
        self.sessions[key] = (turns + [turn])[-self.config.conversation_turns :]
        self.sessions.move_to_end(key)
        while len(self.sessions) > 200:
            self.sessions.popitem(last=False)

    def maintain(self):
        cutoff = time.monotonic() - self.config.conversation_seconds
        for key in list(self.sessions):
            turns = [t for t in self.sessions[key] if t.created > cutoff]
            if turns:
                self.sessions[key] = turns
            else:
                del self.sessions[key]
