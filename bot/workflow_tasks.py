"""Private, bounded task checkpoints; deleted IDs survive backup restoration."""
import json
import re
import time
import uuid

from .privacy import forget_workflow_task


def create_task(store, platform, guild, channel, owner, kind, topic):
    if platform not in {'telegram', 'discord'} or kind != 'report' or not topic or len(topic) > 250:
        raise ValueError('รายละเอียดงานไม่ถูกต้องครับ')
    if store.user(guild, owner)['optout']:
        return None
    count = store.db.execute('SELECT count(*) FROM workflow_tasks WHERE platform=? AND guild=? AND owner=?', (platform, guild, owner)).fetchone()[0]
    total = store.db.execute('SELECT count(*) FROM workflow_tasks').fetchone()[0]
    if count >= 30 or total >= 300:
        raise ValueError('รายการงานถึงขีดจำกัด กรุณาลบงานที่ไม่ต้องการครับ')
    task_id, now = uuid.uuid4().hex, time.time()
    with store.db:
        store.db.execute("INSERT INTO workflow_tasks VALUES (?,?,?,?,?,?,?,'เริ่มงาน','running','[]','',?,?)", (task_id, platform, guild, channel, owner, kind, topic, now, now))
    return task_id


def checkpoint(store, task_id, stage, *, status='running', evidence=None, result=None):
    if task_id is None:
        return
    if stage not in {'เริ่มงาน', 'ค้นข้อมูล', 'อ่านแหล่งข้อมูล', 'สรุปข้อมูล', 'รายงานพร้อม', 'งานไม่สำเร็จ', 'รอทำต่อ'} or status not in {'running', 'finished', 'failed', 'interrupted'}:
        raise ValueError('invalid task state')
    fields, args = ['stage=?', 'status=?', 'updated=?'], [stage, status, time.time()]
    if evidence is not None:
        encoded = json.dumps(evidence, ensure_ascii=False)
        if len(encoded.encode()) > 20000:
            raise ValueError('evidence too large')
        fields.append('evidence=?');args.append(encoded)
    if result is not None:
        if len(result.encode()) > 20000:
            raise ValueError('result too large')
        fields.append('result=?');args.append(result)
    with store.db:
        store.db.execute('UPDATE workflow_tasks SET ' + ','.join(fields) + ' WHERE id=?', (*args, task_id))


def get_task(store, platform, guild, channel, owner, prefix):
    if not re.fullmatch(r'[0-9a-f]{8,32}', prefix):
        return None
    rows = store.db.execute('SELECT * FROM workflow_tasks WHERE platform=? AND guild=? AND channel=? AND owner=? AND id LIKE ?', (platform, guild, channel, owner, prefix + '%')).fetchall()
    return dict(rows[0]) if len(rows) == 1 else None


def delete_task(store, platform, guild, channel, owner, prefix):
    row = get_task(store, platform, guild, channel, owner, prefix)
    if not row or row['status'] == 'running':
        return False
    forget_workflow_task(store.privacy_folder, row['id'])
    with store.db:
        store.db.execute('DELETE FROM workflow_tasks WHERE id=?', (row['id'],))
    return True


def mark_interrupted(store):
    with store.db:
        store.db.execute("UPDATE workflow_tasks SET status='interrupted',stage='รอทำต่อ',updated=? WHERE status='running'", (time.time(),))
