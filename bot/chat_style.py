"""Bounded recent activity and deterministic conversation preferences."""

from __future__ import annotations

import re
import time
from collections import OrderedDict

STOP = re.compile(
    r"^(?:พอ(?:แล้ว)?|หยุด(?:แซว|กวน)?|ไม่เล่น(?:แล้ว)?|จริงจัง(?:นะ)?|เลิกแซว)(?:[!！.\s]|ครับ|ค่ะ|ดิ|เว้ย)*$", re.I
)
SERIOUS = re.compile(r"จริงจัง|ไม่เล่น|เครียด|เสียใจ|เสียชีวิต|ป่วย|ฆ่าตัว|ทำร้าย|ทะเลาะ|ฉุกเฉิน|ร้องไห้", re.I)


def length_instruction(question, preference="auto"):
    if re.search(r"ละเอียด|อธิบายยาว|ทีละขั้น|step.by.step", question, re.I):
        mode = "detailed"
    elif re.search(r"สั้น.?ๆ|กระชับ|brief|สรุปสั้น", question, re.I):
        mode = "short"
    else:
        mode = preference
    return {
        "short": "Answer in 1-3 short sentences unless essential steps need more space. ",
        "detailed": "Explain enough detail and steps to solve the request; avoid filler. ",
        "auto": (
            "Match length to intent: casual conversation typically 2-5 natural sentences when there is "
            "something to add; a quick reaction can be shorter. This is a guide, not a sentence cap. "
            "Give factual questions enough context to be useful; explain complex or serious requests "
            "with sufficient detail, paragraphs or steps. Do not enforce a 1-2 sentence or three-message "
            "limit. Avoid filler and needless repetition; the app splits long answers for Discord. "
        ),
    }.get(mode, "")


class Activity:
    def __init__(self):
        self.rooms = OrderedDict()

    def clear(self):
        self.rooms.clear()

    def add(self, guild, channel, user, text, message_id, now=None):
        now = time.monotonic() if now is None else now
        key = guild, channel
        rows = [r for r in self.rooms.get(key, []) if now - r["at"] < 30]
        rows.append({"user": str(user), "text": text[:240], "at": now, "id": message_id})
        self.rooms[key] = rows[-6:]
        self.rooms.move_to_end(key)
        while len(self.rooms) > 100:
            self.rooms.popitem(last=False)

    def scene(self, guild, channel, now=None):
        now = time.monotonic() if now is None else now
        return [r for r in self.rooms.get((guild, channel), []) if now - r["at"] < 30]

    def ready(self, guild, channel):
        rows = self.scene(guild, channel)
        return (
            len(rows) >= 3
            and len({r["user"] for r in rows}) >= 2
            and not any(SERIOUS.search(r["text"]) for r in rows)
        )
