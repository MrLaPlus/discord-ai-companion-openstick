"""Public football query hints, not a score feed or a guarantee of freshness."""

from __future__ import annotations

import re
from urllib.parse import urlsplit

CLUBS = (
    (r"แมนยู|\b(?:manchester united|man utd|man united)\b", "Manchester United", "manutd.com"),
    (r"แมนซิตี้|แมนซิตี|แมนฯ.?ซิตี้|\b(?:manchester city|man city)\b", "Manchester City", "mancity.com"),
    (r"อาร์เซนอล|อาร์เซน่อล|อาเซนอล|น่อล|\barsenal\b", "Arsenal", "arsenal.com"),
    (r"ฟลีตวูด|ฟลีทวูด|\bfleetwood(?: town)?\b", "Fleetwood Town", "fleetwoodtownfc.com"),
)
SCORE_HOSTS = ("livescore.com", "flashscore.com", "sofascore.com")
LEAGUE_HOST = "premierleague.com"
FOOTBALL = re.compile(r"ฟุตบอล|ผลบอล|บอล|พรีเมียร์ลีก|พรีเมียร์ลี[กค]|\b(?:football|soccer|premier league)\b", re.I)
MATCH = re.compile(
    r"สกอ|ผล(?:การ)?แข่ง|ผลบอล|ตาราง(?:การ)?แข่ง|โปรแกรม|เตะ|แข่ง|ตารางคะแนน|อันดับ|"
    r"\b(?:scores?|results?|fixtures?|schedule|standings|kick.?off|play|playing)\b",
    re.I,
)


def football_request(question):
    subject = bool(FOOTBALL.search(question)) or any(re.search(p, question, re.I) for p, _, _ in CLUBS)
    # A casual "United played terribly" is banter, not an implicit tool request.
    request = re.search(
        r"สกอ|ผลบอล|ผล(?:การ)?แข่ง|ตาราง|โปรแกรม|อันดับ|ไหน|ใคร|เมื่อไ[หร]|กี่โมง|"
        r"ยัง|วันนี้|คืนนี้|พรุ่งนี้|ล่าสุด|\?|\b(?:scores?|results?|fixtures?|schedule|standings|when|who|latest|today)\b",
        question,
        re.I,
    )
    return bool(subject and MATCH.search(question) and request)


def preferred_hosts(question):
    clubs = tuple(host for pattern, _, host in CLUBS if re.search(pattern, question, re.I))
    return clubs + (LEAGUE_HOST,) + SCORE_HOSTS


def football_query(question, query):
    """Preserve the user's date/opponents; add source hints only if they fit."""
    if not football_request(question):
        return query
    original = query
    for pattern, name, _ in CLUBS:
        query = re.sub(pattern, name, query, flags=re.I)
    # Leave room for the public-only MCP's existing 400-character input bound.
    hosts = preferred_hosts(question)
    selected = tuple(dict.fromkeys(hosts))
    hint = " (" + " OR ".join("site:" + h for h in selected) + ")"
    if not re.search(r"\bfootball\b", query, re.I):
        query += " football"
    if re.search(r"ตาราง(?:การ)?แข่ง|โปรแกรม|เตะ|\bfixtures?|schedule|kick.?off\b", question, re.I):
        query += " fixtures schedule"
    elif re.search(r"ตารางคะแนน|อันดับ|\bstandings\b", question, re.I):
        query += " standings"
    else:
        query += " results score"
    if len(query + hint) <= 400:
        return query + hint
    return query if len(query) <= 400 else original


def host_rank(url, question):
    host = (urlsplit(url).hostname or "").lower()
    for index, preferred in enumerate(preferred_hosts(question)):
        if host in {preferred, "www." + preferred}:
            return index
    return 100


def browser_score_page(url):
    """Known public match sites often require JavaScript; no hidden endpoints."""
    host = (urlsplit(url).hostname or "").lower().removeprefix("www.")
    # City fixtures have readable HTML; Chromium was blocked in the actual probe.
    return host in {LEAGUE_HOST, *SCORE_HOSTS, "manutd.com"}
