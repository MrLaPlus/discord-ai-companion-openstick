import re

from .output import UNAVAILABLE_ANSWER, UnavailableOutput, final_text


def wants_sources(question: str) -> bool:
    return bool(
        re.search(r"เอามาจากไหน|อ้างอิง|แหล่งที่มา|แหล่งข้อมูล|ที่มาของคำตอบ|\bsources?\b|citations?", question, re.I)
    )


def sources_only(question: str) -> bool:
    compact = re.sub(r"[\s?!？]", "", question.lower())
    compact = re.sub(r"(?:ครับ|ค่ะ|คะ|หน่อย|ที|นะ)+$", "", compact)
    return compact in {
        "เอามาจากไหน",
        "ขอแหล่งอ้างอิง",
        "ขออ้างอิง",
        "อ้างอิงจากไหน",
        "ขอแหล่งที่มา",
        "ขอแหล่งข้อมูล",
        "sources",
        "source",
        "ที่มาของคำตอบ",
    }


def used_sources(text: str, sources: dict[str, str]) -> dict[str, str]:
    labels = set(re.findall(r"\[((?:S|W)\d+)\]", text))
    return {label: url for label, url in sources.items() if label in labels}


def render_answer(text: str, sources: dict[str, str], show_sources: bool = False, *, web_sources=None) -> str:
    try:
        text = final_text(text)
    except UnavailableOutput:
        return UNAVAILABLE_ANSWER
    combined = {**sources, **(web_sources or {})}
    text = re.sub(r"\[((?:S|W)\d+)\]", lambda match: match[0] if match[1] in combined else "", text)
    if show_sources:
        chosen = used_sources(text, combined) or combined
        links = "\n".join(f"[{label}] <{url}>" for label, url in chosen.items())
        return text.strip() + ("\n\n" + links if links else "")
    text = re.sub(r"\[(?:S|W)\d+\]", "", text)
    for url in (web_sources or {}).values():
        text = re.sub(r"\[[^\]]*\]\(" + re.escape(url) + r"\)", "", text)
        text = text.replace("<" + url + ">", "").replace(url, "")
    text = re.sub(r"\[[^\]]*\]\(https://(?:www\.)?discord(?:app)?\.com/channels/[^)]+\)", "", text)
    text = re.sub(r"<?https://(?:www\.)?discord(?:app)?\.com/channels/\d+/\d+/\d+>?", "", text)
    text = re.split(r"(?im)^\s*(?:#{1,3}\s*)?(?:แหล่งอ้างอิง|แหล่งที่มา|sources|references)\s*:", text)[0]
    return text.strip()


def split_reply(text: str, limit: int = 1900) -> list[str]:
    """Split by UTF-16 units without discarding text; prefer paragraph/word boundaries."""
    if limit < 2:
        raise ValueError("Message limit must be at least two UTF-16 units")
    parts = []
    while text:
        prefix = text.encode("utf-16-le")[: limit * 2].decode("utf-16-le", errors="ignore")
        if len(prefix) < len(text):
            boundary = max(prefix.rfind("\n"), prefix.rfind(" "))
            if boundary >= len(prefix) // 2:
                prefix = prefix[: boundary + 1]
        if prefix.strip():
            parts.append(prefix)
        text = text[len(prefix) :]
    return parts or ["ยังไม่มีคำตอบครับ"]
