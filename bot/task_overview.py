"""Owner-scoped view of existing scheduled tasks without extra background work."""
from datetime import datetime
from zoneinfo import ZoneInfo


def task_overview(store, platform, guild, channel, owner, zone):
    if platform not in {'telegram', 'discord'}:
        raise ValueError('unsupported platform')
    # Discord summaries are scoped to this channel so loss of access to an old
    # channel cannot disclose its saved task contents in another channel.
    sql = "SELECT * FROM scheduled_jobs WHERE platform=? AND guild=? AND owner=?"
    args = [platform, guild, owner]
    if platform == 'discord':
        sql += ' AND channel=?'
        args.append(channel)
    sql += " AND (status IN ('active','sending') OR last_result IN ('missed','uncertain','sent','skipped')) ORDER BY CASE WHEN status IN ('active','sending') THEN 0 ELSE 1 END, next_at ASC LIMIT 20"
    rows = store.db.execute(sql, args).fetchall()
    workflows = store.db.execute('SELECT * FROM workflow_tasks WHERE platform=? AND guild=? AND channel=? AND owner=? ORDER BY updated DESC LIMIT 20', (platform, guild, channel, owner)).fetchall()
    if not rows and not workflows:
        return 'ไม่มีรายการตั้งเวลาหรืองานที่บันทึกไว้ครับ'
    labels = {'active': 'รอทำ', 'sending': 'กำลังส่ง', 'finished': 'เสร็จแล้ว'}
    outcomes = {'missed': 'เคยพลาด', 'uncertain': 'ผลส่งไม่แน่ชัด', 'sent': 'ส่งแล้ว', 'skipped': 'ข้ามตามที่ตั้ง'}
    lines = []
    for row in rows:
        when = datetime.fromtimestamp(row['next_at'], ZoneInfo(zone)).strftime('%d/%m/%Y %H:%M')
        recurrence = 'รายสัปดาห์' if row['weekday'] is not None else 'ทุกวัน' if row['daily'] else 'ครั้งเดียว'
        state = labels.get(row['status'], 'ไม่ทราบ')
        previous = outcomes.get(row['last_result'])
        suffix = f' · ล่าสุด: {previous}' if previous else ''
        lines.append(f"{row['id'][:8]} · {state} · {recurrence} · {when} · {row['body']}{suffix}")
    work_states = {'running': 'กำลังทำ', 'finished': 'เสร็จแล้ว', 'failed': 'ไม่สำเร็จ', 'interrupted': 'รอทำต่อ'}
    for row in workflows[:max(0, 20 - len(lines))]:
        lines.append(f"{row['id'][:8]} · {work_states.get(row['status'], 'ไม่ทราบ')} · {row['stage']} · {row['topic']}")
    scope = 'ห้องนี้' if platform == 'discord' else 'Telegram ของคุณ'
    return f'งานของคุณ ({scope}; แสดงสูงสุด 20 รายการ):\n' + '\n'.join(lines)
