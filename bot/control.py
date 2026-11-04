"""Host-enforced controls. Quoted/model content cannot change these decisions."""

from __future__ import annotations

import math
import time

TOOLS = ("search", "fetch_content")


def tool_allowed(store, guild, channel, member, tool):
    if tool not in TOOLS:
        return False
    rule = store.meta(f"tools:{guild}:{channel}:{tool}", {})
    if rule.get("enabled") is False:
        return False
    roles = {r.id for r in getattr(member, "roles", [])}
    denied = set(rule.get("deny", []))
    allowed = set(rule.get("allow", []))
    return not bool(roles & denied) and (not allowed or bool(roles & allowed))


def budget_settings(store):
    value = store.meta("budget", {})
    return {"daily_usd": value.get("daily_usd", 0), "usd_per_million": value.get("usd_per_million", 0)}


def set_budget(store, daily, rate):
    if not all(math.isfinite(v) and 0 <= v <= 100000 for v in (daily, rate)):
        raise ValueError("วงเงินและราคาต้องเป็นจำนวนไม่ติดลบที่มีค่าจำกัด")
    if daily and not rate:
        raise ValueError("ต้องตั้งราคาต่อหนึ่งล้าน token ก่อนเปิดเพดานค่าใช้จ่าย")
    store.set_meta("budget", {"daily_usd": daily, "usd_per_million": rate})


def usage_report(bot):
    rows = bot.store.db.execute(
        "SELECT category,count(*),coalesce(sum(actual),0),sum(max(reserved_cost,coalesce(actual_cost,0))),sum(reserved_cost=0) "
        "FROM attempts WHERE created>? GROUP BY category",
        (time.time() - 86400,),
    ).fetchall()
    usage = bot.store.meta("mcp_usage", [])
    return {
        "window": "rolling 24h / ย้อนหลัง 24 ชั่วโมง",
        "ai": [
            {
                "category": r[0],
                "requests": r[1],
                "reported_tokens": r[2],
                "reserved_usd": r[3] if not r[4] else None,
                "unpriced_requests": r[4],
            }
            for r in rows
        ],
        "tools": {name: sum(r[0] > time.time() - 86400 and r[2] == name for r in usage) for name in TOOLS},
        "budget": budget_settings(bot.store),
        "price_note": "ประมาณการจากราคาที่แอดมินตั้ง; ไม่ใช่ใบเรียกเก็บเงินจริง / manually configured estimate",
        "images": "disabled",
        "voice": "disabled",
    }


def panel_summary(page, details):
    """Small human-readable summaries instead of exposing configuration JSON."""

    def toggle(value):
        return "เปิด" if value else "ปิด"

    def stamp(value):
        return f"<t:{int(value)}:R>" if value else "ยังไม่ตรวจ"

    if page == "feedback":
        return details["text"]
    if page == "personal":
        return f"ระดับแซว: {details['tone']}/4\nความยาว: {details['length']}\nเก็บข้อมูล: {toggle(details['store_data'])}\nลบข้อมูลที่บอทเก็บได้ด้านล่าง ไม่ลบข้อความใน Discord"
    if page == "room":
        return (
            f"ตอบ: {toggle(details['respond'])} · ห้องบอท: {toggle(details['bot_room'])} · เพดานแซว {details['tone']}/4\n"
            f"พักรวม: {bool(details['paused'])}\nแทรกคุย {details['interject']:.0%} · พัก {details['cooldown']} วินาที · สูงสุด {details['hourly']} ครั้ง/ชั่วโมง\n"
            f"ช่วงเงียบ: {details['quiet_start']}–{details['quiet_end']} (-1=ปิด)\nการอ่าน/แชร์ประวัติตั้งแยกด้วย /settings channel"
        )
    if page == "tools":
        rule = details["policy"]
        return f"โหมดเว็บ: {details['mode']}\nเครื่องมือ: {details['tool']} · {toggle(rule.get('enabled', True))}\nอนุญาต: {', '.join(map(str, rule.get('allow', []))) or 'ทุกคนที่มีสิทธิ์พื้นฐาน'}\nปฏิเสธ: {', '.join(map(str, rule.get('deny', []))) or 'ไม่มี'}\nปฏิเสธมีผลเหนืออนุญาต ยังตรวจสิทธิ์ Discord ด้วย"
    if page == "news":
        return f"หัวข้อ: {', '.join(details.get('topics', [])) or 'ยังไม่ตั้ง'}\nคำที่ไม่เอา: {', '.join(details.get('exclude', [])) or 'ไม่มี'}\nRSS: {' '.join(details.get('feeds', [])) or 'ฟีดค่าเริ่มต้น'}\nใช้เมื่อมีคนถาม ไม่ส่งข่าวเอง"
    if page == "usage":
        from .provider_limits import display

        return display(details)
    if page == "health":
        lines = [
            f"AI ล่าสุด: {details['provider']['status']} · {stamp(details['provider']['checked'])}",
            f"MCP: {details['mcp_connection'].get('status')} · {stamp(details['mcp_connection'].get('checked'))} (ไม่รับรองทุกเว็บ)",
        ]
        lines += [
            f"{name}: {value['status']} · {stamp(value.get('checked'))}"
            for name, value in details["tools"].items()
        ]
        search = details.get("search_provider", {})
        if search.get("provider"):
            reasons = {
                "missing_key": "ไม่มีคีย์ Tavily",
                "quota": "Tavily ถึงเพดาน",
                "invalid_key": "คีย์ Tavily ใช้ไม่ได้",
                "rate_limit": "Tavily จำกัดความถี่",
                "temporary_error": "Tavily ขัดข้องชั่วคราว",
            }
            reason = reasons.get(search.get("reason"), "")
            lines.append(f"ค้นหาล่าสุด: {search['provider']}" + (f" · {reason}" if reason else ""))
        ops = details["operations"]
        lines += [
            f"Backup: {stamp(ops['backup'].get('created'))} · ผิดพลาด: {ops['backup_error']}",
            f"ทดสอบกู้คืน: {ops['recovery'].get('ok', 'ยังไม่ตรวจ')} · {stamp(ops['recovery'].get('checked'))}",
        ]
        return "\n".join(lines)
    return f"พักเซิร์ฟเวอร์: {bool(details['guild_paused'])}\nพักห้อง: {bool(details['room_paused'])}\nหยุดจะยกเลิกงานในขอบเขต เปิดกลับไม่เล่นงานเก่าซ้ำ คำขอที่ถึงผู้ให้บริการแล้วอาจเสียเงิน"
