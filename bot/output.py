"""Keep recognized reasoning envelopes out of user-visible answers.

This is a deterministic boundary for known model formats, not a universal
classifier for arbitrary prose. Unknown/incomplete marked traces fail closed.
"""

import re

UNAVAILABLE_ANSWER = "AI ยังไม่ได้ส่งคำตอบที่พร้อมแสดง ลองถามใหม่อีกครั้งได้ครับ"

INTERNAL_HEADER = re.compile(
    r"(?im)^[ \t]*(?:#{1,6}[ \t]*)?(?:\*\*)?"
    r"(?:\[(?:thinking process|internal analysis|analysis|reasoning)\](?:\*\*)?(?=\s|$)"
    r"|(?:thinking process|internal analysis)[ \t]*:?(?:\*\*)?[ \t]*(?:\r?$|\r?\n))"
)
FINAL_HEADER = re.compile(
    r"(?im)^[ \t]*(?:#{1,6}[ \t]*)?(?:\*\*)?"
    r"(?:\[(?:final answer|final response)\]|(?:final answer|final response|คำตอบสุดท้าย)[ \t]*[:：])"
    r"(?:\*\*)?[ \t]*(?:\r?\n)?"
)
INTERNAL_TAG = re.compile(r"</?(?:think|analysis|reasoning)\b[^>]*>", re.I)
INTERNAL_TOKEN = re.compile(r"<\|(?:analysis|thinking|reasoning)\|>", re.I)
FINAL_TOKEN = re.compile(r"<\|final\|>", re.I)
DIRECT_ABUSE = re.compile(r"(?:ไอ้|อี)[ \t]*(?:สัส|เหี้ย|ควาย|โง่)|อย่ามาหลอกถาม", re.I)


class UnavailableOutput(ValueError):
    pass


def final_text(text: str) -> str:
    text = text.strip()
    token = INTERNAL_TOKEN.search(text)
    if token:
        final = FINAL_TOKEN.search(text, token.end())
        if not final:
            raise UnavailableOutput(UNAVAILABLE_ANSWER)
        text = text[final.end() :].strip()
    # Validate BEFORE removal: regex alone can confuse an inner closing tag
    # with the outer close and leave unmarked private text behind.
    stack = []
    for token in INTERNAL_TAG.finditer(text):
        name = re.match(r"</?([a-z]+)", token[0], re.I)[1].lower()
        if token[0].startswith("</"):
            if not stack or stack.pop() != name:
                raise UnavailableOutput(UNAVAILABLE_ANSWER)
        else:
            if stack:
                raise UnavailableOutput(UNAVAILABLE_ANSWER)
            stack.append(name)
    if stack:
        raise UnavailableOutput(UNAVAILABLE_ANSWER)
    # Remove only complete explicit envelopes, never infer missing boundaries.
    for tag in ("think", "analysis", "reasoning"):
        text = re.sub(rf"<{tag}\b[^>]*>.*?</{tag}\s*>", "", text, flags=re.I | re.S).strip()
    header = INTERNAL_HEADER.search(text)
    if header:
        final = FINAL_HEADER.search(text, header.end())
        if not final:
            raise UnavailableOutput(UNAVAILABLE_ANSWER)
        text = text[final.end() :].strip()
    if (
        not text
        or INTERNAL_TAG.search(text)
        or INTERNAL_HEADER.search(text)
        or INTERNAL_TOKEN.search(text)
        or DIRECT_ABUSE.search(text)
    ):
        raise UnavailableOutput(UNAVAILABLE_ANSWER)
    return text
