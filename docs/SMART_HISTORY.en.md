# Ask and retrieve history automatically

The bot can fetch existing Discord history, including messages posted before the bot joined, with View Channel and Read Message History permissions and an explicitly readable channel.

## Usage

Ask naturally in a configured bot room, or mention/reply to the bot elsewhere. Examples in Thai:

```text
ก่อนบอทเข้าห้องนี้ เคยคุยเรื่องอะไรกัน
เมื่อวานคุยเรื่องเกมอะไร
วันที่2025-05-01มีเรื่องอะไร
ปี 2568 เคยพูดถึงเกมแมวไหม
ข้อความแรกสุดในห้องนี้พูดว่าอะไร
```

No manual `/history import` is required. To target another channel, mention the real channel using Discord's `#channel` selector or its name with an explicit channel prefix.

## Restricted channels

```text
/ask question:ก่อนบอทเข้า ห้อง #พิมพ์คุย เคยคุยอะไรกัน
```

`/ask` replies ephemerally to the requester. Both requester and bot need current source read access, and the source/destination must be readable and in the same configured sharing group. NSFW-to-general sharing stays blocked, including private answers.

Ordinary channel replies can cross channel boundaries only if a permission-state proof establishes that every potential destination viewer can also read source history. It evaluates role combinations and uncached member overwrites, not just the administrator requester's permissions or a partial member cache. If audiences differ, use `/ask` for a private answer.

## Sources on request

Default replies omit provenance, source room/author attribution, citation markers and message links unless the question needs that information. Ask “เอามาจากไหน” or reply to a bot message with “ขอแหล่งอ้างอิงหน่อย”. For an earlier private answer, use `/ask question:เอามาจากไหน`.

Only answer/message identifiers and source relationships are persisted, not generated answer text. Source follow-ups recheck access. Deletion, edits, retention expiry or source-author opt-out invalidate affected references. General knowledge or explicit memories may have no source-message link. References identify context supplied to the model, not an independently verified explanation of its internal reasoning.

## Long answers

Answers are split into consecutive messages of at most 1,900 UTF-16 units. Emoji may consume two units. The application does not truncate the generated answer to fit one Discord message; model output still obeys MAX_OUTPUT_TOKENS and quota settings.

## Bounds and settings

- AUTO_HISTORY_ENABLED=true: fetch on demand.
- AUTO_HISTORY_MESSAGES=300 per channel for history questions; ordinary chat refreshes up to 30 recent messages per channel.
- AUTO_HISTORY_CHANNELS=3 and AUTO_HISTORY_TOTAL=600 bound each question.
- AUTO_HISTORY_TIMEOUT=20 seconds bounds the retrieval stage.
- AUTO_HISTORY_CACHE_SECONDS=60 avoids repeating the same interval fetch.
- HISTORY_RETENTION_DAYS=30 expires cached messages by fetch time, not original message age. Older years can therefore be queried.
- MAX_OUTPUT_TOKENS=768 is the new default, adjustable within quota limits.

Supported Thai time phrases include before the bot joined, oldest messages, yesterday, last night (previous 18:00 to current 06:00), today, last month, last year, YYYY-MM-DD and Gregorian/Buddhist years. The configured timezone applies.

Retrieval is a bounded sample, not an exhaustive server search. Narrow older queries by date/year. Bots, webhooks and opted-out authors are skipped. Messages already deleted from Discord cannot be recovered.

The database migrates to schema 2 for cache timestamps and provenance. Back up before updating. Rolling back to an older image requires restoring a pre-upgrade backup to a new path; do not run an old image against the new schema.
