"""Content-free per-day provider usage, independent of transient quota reservations."""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo


def initialize(store):
    store.db.execute('''CREATE TABLE IF NOT EXISTS usage_daily (
        day TEXT NOT NULL, provider TEXT NOT NULL, requests INTEGER NOT NULL DEFAULT 0,
        successful INTEGER NOT NULL DEFAULT 0, failed INTEGER NOT NULL DEFAULT 0,
        prompt_tokens INTEGER NOT NULL DEFAULT 0, completion_tokens INTEGER NOT NULL DEFAULT 0,
        unknown_usage INTEGER NOT NULL DEFAULT 0, audio_seconds REAL NOT NULL DEFAULT 0,
        PRIMARY KEY(day, provider))''')
    store.db.commit()


def record(store, provider, *, success, prompt=None, completion=None, audio_seconds=0, timestamp=None, zone='Asia/Bangkok'):
    if provider not in {'openrouter', 'tokenme', 'groq', 'whisper'}:
        raise ValueError('invalid provider')
    initialize(store)
    day = datetime.fromtimestamp(timestamp, ZoneInfo(zone)).date().isoformat() if timestamp is not None else datetime.now(ZoneInfo(zone)).date().isoformat()
    def count(x):
        return x if type(x) is int and x >= 0 else None
    inp, out = count(prompt), count(completion)
    unknown = int(provider != 'whisper' and (inp is None or out is None))
    with store.db:
        store.db.execute('''INSERT INTO usage_daily VALUES (?,?,1,?,?,?,?,?,?)
          ON CONFLICT(day,provider) DO UPDATE SET requests=requests+1,
          successful=successful+excluded.successful,failed=failed+excluded.failed,
          prompt_tokens=prompt_tokens+excluded.prompt_tokens,completion_tokens=completion_tokens+excluded.completion_tokens,
          unknown_usage=unknown_usage+excluded.unknown_usage,audio_seconds=audio_seconds+excluded.audio_seconds''',
          (day,provider,int(success),int(not success),inp or 0,out or 0,unknown,max(0,float(audio_seconds))))


def display(store, *, detail=False, now=None, zone='Asia/Bangkok'):
    initialize(store)
    today = datetime.fromtimestamp(now, ZoneInfo(zone)).date() if now is not None else datetime.now(ZoneInfo(zone)).date()
    monday = today - timedelta(days=today.weekday())

    def totals(start, end):
        rows = store.db.execute("""SELECT provider, sum(requests), sum(successful), sum(failed),
            sum(prompt_tokens), sum(completion_tokens), sum(unknown_usage), sum(audio_seconds)
            FROM usage_daily WHERE day>=? AND day<=? GROUP BY provider""", (start.isoformat(), end.isoformat())).fetchall()
        return {r[0]: r for r in rows}

    def line(label, by):
        a, b, c = by.get('openrouter'), by.get('groq'), by.get('tokenme')
        def tokens(row):
            return row[4] + row[5] if row else 0
        count_a, count_b, count_c = tokens(a), tokens(b), tokens(c)
        suffix = ' *' if any(row and row[6] for row in (a, b, c)) else ''
        if detail:
            def expanded(row):
                return f'{row[4]:,}+{row[5]:,}' if row else '0+0'
            return f'{label}: TokenMe {expanded(c)} | OR {expanded(a)} | Groq {expanded(b)} | รวม {count_a + count_b + count_c:,}{suffix}'
        return f'{label}: TokenMe {count_c:,} | OR {count_a:,} | Groq {count_b:,} | รวม {count_a + count_b + count_c:,}{suffix}'

    lines = ['📊 การใช้ AI · เวลาไทย', 'tokens: OR = OpenRouter' + (' · ขาเข้า+ขาออก' if detail else '')]
    sections = [('รายวัน · 7 วันล่าสุด', [(today - timedelta(days=i), today - timedelta(days=i)) for i in range(7)]),
                ('รายสัปดาห์', [(monday - timedelta(weeks=i), monday - timedelta(weeks=i) + timedelta(days=6)) for i in range(4)])]
    months = []
    for i in range(6):
        n = today.year * 12 + today.month - 1 - i
        first = today.replace(year=n // 12, month=n % 12 + 1, day=1)
        next_n = n + 1
        last = first.replace(year=next_n // 12, month=next_n % 12 + 1) - timedelta(days=1)
        months.append((first, last))
    sections.append(('รายเดือน', months))
    for heading, periods in sections:
        entries = []
        for start, end in periods:
            by = totals(start, end)
            if not by:
                continue
            label = start.strftime('%m/%Y') if heading == 'รายเดือน' else start.strftime('%d/%m')
            if heading == 'รายสัปดาห์':
                label += '–' + end.strftime('%d/%m')
            entries.append(line(label, by))
            if detail:
                for name, provider in [('TokenMe', 'tokenme'), ('OR', 'openrouter'), ('Groq', 'groq')]:
                    row = by.get(provider)
                    if row:
                        entries.append(f'  {name}: สำเร็จ {row[2]} · ล้มเหลว {row[3]} · ไม่ทราบ token {row[6]}')
                voice = by.get('whisper')
                if voice:
                    entries.append(f'  Whisper: {voice[1]} ครั้ง · {voice[7]:,.0f} วินาที')
        lines.extend(['', heading, *(entries or ['ยังไม่มีข้อมูล'])])
    if not detail:
        current = totals(today, today)
        lines.extend(['', 'คำขอวันนี้'])
        for name, provider in [('TokenMe', 'tokenme'), ('OpenRouter', 'openrouter'), ('Groq', 'groq')]:
            row = current.get(provider)
            lines.append(f'{name}: สำเร็จ {row[2] if row else 0} · ล้มเหลว {row[3] if row else 0}')
        voice = current.get('whisper')
        if voice:
            lines.append(f'Whisper: {voice[1]} ครั้ง · {voice[7]:,.0f} วินาที')
        lines.append('\n* มียอด token ที่ผู้ให้บริการไม่ได้แจ้ง')
        lines.append('ใช้ /usage detail เพื่อดูข้อมูลอย่างละเอียด')
    else:
        lines.append('\n* มียอด token ที่ผู้ให้บริการไม่ได้แจ้ง')
    lines.append('ข้อมูลเริ่มนับเมื่อเปิดใช้ระบบนี้')
    return '\n'.join(lines)
