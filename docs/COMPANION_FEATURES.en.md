# Conversation continuity, confirmed memories, search continuation and operations

Requested by the owner on 2026-10-27. See [PROGRESS.md](../PROGRESS.md) for actual verification and rollout evidence.

## Recent assistant answers

The bot uses its own recent questions/answers to resolve follow-ups such as “the second one”, “continue”, or “why did you say that?”. RAM-only sessions retain up to three turns for 30 minutes by default, with a 200-session instance cap. Guild, destination channel, requester, and private/channel mode are separate scopes. Existing prompt budgets still apply, so not every turn or full long answer fits.

Generated answer text never enters SQLite or backups and disappears on restart. Opt-out, data clearing, relevant source edits/deletions and observed permission changes clear temporary context and search cursors. Access and source checks still run before model submission/delivery. Opted-out users can ask questions without retaining their turns.

## Confirmed memory suggestions

Explicit first-person preferences/names such as “I prefer cats” or “ผมชอบเกมแมว” can produce a **Remember / Do not remember** proposal after the answer. Only the original author in the original channel can act. Buttons expire in five minutes; access and the original message are checked again before saving.

No durable memory is created before acceptance. Conservative rules skip detected questions, quoted claims, marked jokes, identifiers and sensitive categories. This is selective rule-based extraction, not an AI summary of every message, and adds no model requests. At most one proposal per person per hour; exact existing memories are not proposed again.

Use `/memory suggestions enabled:false` to disable personal suggestions; `/memory list`, `/memory edit`, and `/forget` manage confirmed facts. Channel proposals stay in the channel; `/ask` proposals remain ephemeral. Restart or invalidation can make old buttons unusable; `/remember` remains the explicit alternative.

## Continue a historical search

Start with a topic/channel/date question, then say `หาเก่ากว่านี้`, `หาเพิ่ม`, `ค้นต่อ`, `search more`, or `search older`. Separate message-ID cursors advance each eligible channel, preserving the original topic and date bounds. Identical timestamps do not cause repeated pages. Oldest-first searches continue forward in time.

`เฉพาะเดือนนั้น` resets the range to the calendar month containing the original range start or last scanned message. Without an anchor, the bot asks for a new topic/date query. After refinement, “หาเพิ่ม” continues that month's pages.

RAM search sessions expire after 30 minutes and are separated by guild/channel/person/private mode. Continue a private `/ask` search with another `/ask`. Cursors commit only after successful answer delivery; provider/quota failure does not skip the page. Existing fetch/channel/total/time bounds and current access checks remain enforced. End-of-search/missing-session responses do not require AI. This is a bounded sample, not a complete-server search. Source links stay opt-in; long answers still split.

## Automatic backups and operator alerts

When ready, create an initial snapshot if due, then every 24 hours by default. SQLite's backup API includes committed WAL data and integrity checks. A 30-second backup budget and a background thread keep the Discord event loop responsive. Only completed snapshots are published as `.sqlite3` files.

Files live under `/app/data/backups` in the persistent volume. Retention defaults to seven days, capped at 32 automated snapshots. Prune only scheduler-owned filenames after a successful new backup; preserve manual files and the newest snapshot. Failure preserves previous snapshots and retries after ten minutes. Inspect `/status`.

This is a same-volume backup; copy snapshots elsewhere to survive disk/volume loss. Old backups contain history/memories and expire independently of current-data erasure. Stop the bot and restore into a new database path as described in the [operations guide](OPERATIONS.en.md).

Default alerts go to the server owner's DM. They cover invalid provider credentials/models, HTTP 429, processing failures, dead workers, backup failures and local quota utilization reaching 80%. Alerts contain no credentials or chat text. Manage Server holders can choose `/settings alerts enabled:true channel:#admin` or omit the channel for owner DM; `enabled:false` disables alerts.

Persisted per-category throttles limit repeat notices to once per six hours. Failed sends retry after ten minutes and appear in `/status`. The bot must be running and able to contact Discord; this cannot report whole-host/network outages. Quota percentages use conservative local accounting, not the provider account's live remaining balance.

## Environment settings

| Variable | Default | Meaning |
|---|---:|---|
| `CONVERSATION_TTL_SECONDS` | 1800 | RAM assistant-turn TTL; 0 disables |
| `CONVERSATION_TURNS` | 3 | Maximum turns per session |
| `MEMORY_SUGGESTIONS` | true | Instance-wide proposals toggle |
| `BACKUP_INTERVAL_HOURS` | 24 | Backup interval; 0 disables |
| `BACKUP_RETENTION_DAYS` | 7 | Automatic-file age; also capped at 32 snapshots |
| `QUOTA_ALERT_PERCENT` | 80 | Local quota alert threshold |

After editing `.env`, run `docker compose up -d --force-recreate bot`. SQLite data/settings survive; RAM context, pending proposals and search cursors reset. No GUI, new ports or dependencies are required.

Image/screenshot understanding and voice-channel support remain outside this delivery.
