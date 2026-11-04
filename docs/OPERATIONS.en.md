# Installation and operations

This is a headless Docker Discord companion using Groq `qwen/qwen3.8-27b` and persistent SQLite storage. There is no management website.

## 1. Discord and Groq setup

1. Create an application in the [Discord Developer Portal](https://discord.com/developers/applications).
2. Generate a bot token and save it only in the local `.env` file.
3. Enable **Message Content Intent**. Server Members Intent is not required by this version.
4. Generate a **Guild Install** link with `bot` and `applications.commands` scopes.
5. Grant **View Channel**, **Read Message History**, and **Send Messages** in approved channels. Administrator permission is unnecessary.
6. Bot administrators need **Manage Server** to configure the application.
7. Generate a fresh [Groq API key](https://console.groq.com/keys), and verify the account's actual plan and limits. Do not reuse a key previously exposed in chat.

## 2. Start Docker

Install Docker Engine and the Compose plugin. The host needs outbound access to Discord and Groq.

```bash
git clone https://github.com/MrLaPlus/discord-ai-companion.git
cd discord-ai-companion
cp .env.example .env
```

Do not overwrite an existing `.env`. Fill in `DISCORD_TOKEN` and `GROQ_API_KEY` with a local editor; retain `GROQ_MODEL=qwen/qwen3.8-27b`. Setting `DISCORD_GUILD_ID` is recommended for a test server so commands register directly there. Enable Discord Developer Mode to copy its Server ID. A blank value registers global commands, which may take time to appear. Changing registration scope can leave old commands visible until separately removed.

```bash
docker compose config --quiet
docker compose build
docker compose run --rm bot python -m bot --check
docker compose up -d
docker compose ps
docker compose logs --tail=100 -f bot
```

The check validates configuration, database initialization, and slash-command serialization without network calls. It prints presence flags only: both should be `True` before live startup. It does not verify credential validity. All channel access starts disabled. Healthy container status reflects Discord readiness and live workers, not repeated AI probes that consume quota.

## 3. Configure channels and tone

Run in a normal channel for mention/reply-only behavior:

```text
/settings channel readable:true respond_here:true bot_room:false
```

For a dedicated bot room:

```text
/settings channel readable:true respond_here:true bot_room:true
```

The optional `channel` parameter targets another channel. A channel can respond without storing history using `readable:false respond_here:true bot_room:false`; the current request still goes to Groq, but stored context/memories from that channel are not used.

```text
/settings tone level:2
/tone level:2
```

The effective tone is the lower of channel and user preference. Both default to 1. Level 0 disables teasing; level 3 permits stronger friendly banter with boundaries.

## 4. Cross-channel context

Assign the same explicit sharing group to approved channels. The channel command writes the complete supplied channel settings, so provide its other flags too:

```text
/settings channel channel:#general readable:true respond_here:true bot_room:false share_group:friends
/settings channel channel:#games readable:true respond_here:true bot_room:false share_group:friends
```

Ordinary replies can share across compatible restricted channels when every potential destination viewer can read source history. Use `/ask question:...` for private answers when audiences differ. Current requester/bot access and an explicit sharing group remain required. NSFW content cannot flow into a general channel. See [automatic retrieval](SMART_HISTORY.en.md).

Permissions are checked before model submission and delivery. Selected source messages are revalidated for edits/deletion. Concurrent permission, privacy, or memory changes may cancel a reply; send a fresh question if needed. Cancellation cannot retract information already transmitted before the change.

## 5. History and catch-up

```text
/history import channel:#games limit:500 days:7
```

Import at most 5,000 messages per command, selecting the period with days/before_message. HISTORY_RETENTION_DAYS controls cache age since retrieval, not the age of the original message. Skip bots, webhooks, and opted-out authors. Progress is private. Import stops on state changes or after ten minutes. Normal questions can now retrieve history automatically without this command.

The result includes a `before_message` cursor for an older batch:

```text
/history import channel:#games limit:500 days:30 before_message:MESSAGE_ID_FROM_PREVIOUS_RESULT
/catchup hours:24
```

Replace the placeholder with a real numeric Discord message ID. Reimport updates existing message IDs. Retrieval uses bounded substring/Thai ngram matching, not semantic embeddings, so synonyms or alternate spellings may be missed. Catch-up returns a private summary of a limited stored sample, not a complete account of all server messages.

## 6. Memory and privacy controls

```text
/remember text:Call me Nagi
/memory list page:1
/memory show memory_id:1
/memory edit memory_id:1 text:Call me Nagi; I like cat games
/forget memory_id:1
/privacy store_data:false
```

Personal memories are keyed to guild, owner, and source channel, subject to disclosure checks. Explicit commands and confirmed first-person preference/name proposals can create memories; no proposal is saved before acceptance. See [new features](COMPANION_FEATURES.en.md).

Opt-out atomically disables future storage and deletes current personal memories/history, including protection against reimport. The user can still invoke the bot; that current message is sent to Groq for the reply. `/memory clear confirm:true` erases current memories and history but does not opt out of future storage. Other people's messages mentioning someone and administrator room notes are not automatically identified as that person's records; administrators should remove such notes where appropriate.

Administrators can use `/room-memory add`, `list page:1`, `show memory_id:...`, and `forget memory_id:...` for room rules/topics. Do not use room notes to store other people's private information. They are administrator notes, not independently verified facts.

Generated assistant replies are never written to SQLite/backups. Bounded RAM context retains up to three recent turns for 30 minutes by default, isolated by person/channel/private mode and cleared on privacy/source/state changes or restart. Prompt budgets can omit long details. Commands do not delete original Discord messages. Old backups expire separately.

## 7. Interjections and pause

```text
/settings interject probability:0.1 cooldown:300 hourly:4 quiet_start:23 quiet_end:7
```

This allows a 10% chance on eligible new human messages, at least 300 seconds apart, at most four times per hour, with quiet hours 23:00–07:00 in `TIMEZONE`. The channel must allow responses and have `bot_room:false`; full bot-room conversation takes precedence. Set probability to 0 to disable. Set both quiet-hour values to -1 to disable quiet hours; equal nonnegative hours mean quiet all day. Quiet hours affect interjections, not direct mentions/replies.

```text
/pause paused:true whole_server:true
/pause paused:false whole_server:true
/status
```

Pause stops replies but does not stop storage in readable channels. Disable readable access to stop ingestion. Status quotas cover the entire instance over rolling windows; the 24-hour cap is not a Thai-midnight reset.

## 8. Quotas and tuning

Example local ceilings do not establish your actual free-plan entitlement. Check [Groq Limits](https://console.groq.com/settings/limits).

- Requests/tokens per minute/day are shared across this instance's guilds.
- `MAX_CONTEXT_BYTES` and `MAX_OUTPUT_TOKENS` bound context and completion size.
- `QUEUE_SIZE`, `WORKERS`, and `USER_COOLDOWN_SECONDS` control load. Start with one worker.
- Default local retention is 30 days for messages and 180 days for explicit memories. Retrieval filters expired data; maintenance purges hourly.
- `MAX_MESSAGES_PER_GUILD` defaults to 100,000 after maintenance.
- Docker uses `DATABASE_FILENAME` under its `/app/data` volume; native Python uses `DATABASE_PATH`.

After environment changes, run `docker compose up -d --force-recreate`. Token reservations conservatively use UTF-8 bytes plus framing and maximum completion size, so local limits may stop work earlier than provider tokenization would. Failed calls count too. HTTP 429 persists provider-directed backoff, without retry storms or automatic paid fallback.

Docker does not automatically restart a container merely because its healthcheck says unhealthy. Inspect logs and restart after addressing the cause. PyNaCl/davey voice-support warnings are expected because this version has no voice features.

## 9. Backup and recovery

The SQLite backup API takes coherent snapshots including committed WAL data while the bot is running:

```bash
docker compose exec bot python -m bot.backup create /app/data/backups/manual-2026-10-27.sqlite3
mkdir -p backups
docker compose cp bot:/app/data/backups/manual-2026-10-27.sqlite3 ./backups/
```

Use a new filename each time; existing files are not overwritten. Copy backups to another disk/host too: a backup in the same volume cannot protect against volume loss. Backups contain private chat data.

Restore only to a new database path, leaving old databases/WAL files intact:

```bash
docker compose stop bot
docker compose run --rm bot python -m bot.backup restore /app/data/backups/manual-2026-10-27.sqlite3 --database /app/data/restored.sqlite3
```

Set `DATABASE_FILENAME=restored.sqlite3` in `.env`, then:

```bash
docker compose up -d --force-recreate
```

Keep the original database until validation succeeds. An old backup may revert opt-outs/deletions; review requests before restarting. Automatic snapshots now default to every 24 hours with seven-day retention, capped at 32 automatic files. Manual backups are preserved. See [backup/alert configuration](COMPANION_FEATURES.en.md).

## 10. Updates and live acceptance

Back up before updates, run `git pull --ff-only`, then `docker compose up -d --build`. Do not use `docker compose down -v` if you want to retain memory.

Once credentials are configured, verify on Discord:

1. Normal channels respond to mentions/replies only.
2. Designated bot rooms respond without mentions.
3. Explicit memories survive `docker compose restart bot`.
4. A private test message never appears in a general-channel prompt/reply or citation.
5. Opt-out deletes current personal records and subsequent imports do not restore them.
6. Interjection settings, pause, status, and backup/restore behave as documented.

See [PROGRESS.md](../PROGRESS.md) for actual checks and untested behavior. Automated tests do not replace live acceptance against Discord.

Developers can run `python -m unittest discover -v` after installing `requirements.txt`, without credentials. Run `python3 scripts/docker_smoke.py` after building the image to verify non-root execution, a read-only root filesystem, persistence across two containers, and backup/restore. The script creates and removes only its own disposable test volume.

Run actual HTTP requests against an in-container mock server without external networking:

```bash
docker run --rm --network none --read-only --tmpfs /tmp:size=32m --mount "type=bind,source=$PWD/tests,target=/app/tests,readonly" discord-ai-companion:local python -m unittest tests.http_integration -v
```
