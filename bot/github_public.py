"""Read a public repository release using a fixed GitHub endpoint."""
from __future__ import annotations

import asyncio
import json
import re
from urllib.parse import quote, urlsplit

import aiohttp

REPO = re.compile(r'^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})/[A-Za-z0-9_.-]{1,100}$')


def clean_repo(value):
    repo = value.strip().removeprefix('https://github.com/').strip('/')
    if not REPO.fullmatch(repo) or '..' in repo:
        raise ValueError('ระบุ repository สาธารณะรูปแบบ owner/repo ครับ')
    return repo


async def latest_release(session, value):
    repo = clean_repo(value)
    owner, name = repo.split('/')
    url = f'https://api.github.com/repos/{quote(owner, safe="")}/{quote(name, safe="")}/releases/latest'
    try:
        async with session.get(url, headers={'Accept': 'application/vnd.github+json', 'User-Agent': 'OpenStick-Companion'},
                timeout=aiohttp.ClientTimeout(total=10), allow_redirects=False) as response:
            if response.status == 404:
                return 'ไม่พบ release สาธารณะล่าสุดของ repository นี้ครับ'
            if response.status != 200:
                return 'ตรวจ GitHub ไม่สำเร็จหรือถูกจำกัดจำนวนคำขอครับ'
            body = bytearray()
            async for chunk in response.content.iter_chunked(8192):
                body.extend(chunk)
                if len(body) > 131072:
                    raise ValueError('response too large')
            data = json.loads(body)
        link = data.get('html_url')
        parsed = urlsplit(link) if isinstance(link, str) else None
        expected = f'/{owner}/{name}/releases/'
        if not parsed or parsed.scheme != 'https' or parsed.hostname != 'github.com' or parsed.username or parsed.password or parsed.port not in (None, 443) or not parsed.path.lower().startswith(expected.lower()) or parsed.query or parsed.fragment:
            raise ValueError('invalid result')
        tag, title, published = (str(data.get(key) or '')[:160].replace('\n', ' ') for key in ('tag_name', 'name', 'published_at'))
        if not tag:
            raise ValueError('missing release')
        return f"GitHub release ล่าสุดของ {repo}\n{title or tag} ({tag})\nเผยแพร่: {published or 'ไม่ระบุ'}\n{link}"
    except asyncio.CancelledError:
        raise
    except (aiohttp.ClientError, asyncio.TimeoutError, ValueError, TypeError, json.JSONDecodeError):
        return 'ตรวจ GitHub ไม่สำเร็จครับ ลองใหม่ภายหลัง'
