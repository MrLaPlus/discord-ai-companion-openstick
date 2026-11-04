# MCP public web search — runtime guide

> Original two-tool runtime below; see [EXTENSIONS.en.md](EXTENSIONS.en.md) for RSS/captions/Playwright, level4 and accessible-room behavior.

2026-10-28. See PROGRESS.md for the latest deployment evidence.

The bot uses `mcp==2.3.0` and the reviewed `duckduckgo-mcp-server==0.7.0` backend in an isolated `mcp-web` Docker service. No additional search API key is required. The existing chat model remains unchanged. The awesome-mcp-servers catalog is not installed wholesale.

## Operation

Set `MCP_ENABLED=true` locally and run `docker compose up -d --build`. Both services are headless, with no published host ports. A Manage Server administrator enables an already configured response room using `/mcp channel mode:hybrid`; all new rooms start `off`.

Modes are `off`, `button` (review the query before searching), `auto`, and `hybrid` (automatic lookup plus relevant search buttons). Context-derived/personal questions require query confirmation even in auto mode. Examples: mention the bot with a latest-score/news question, provide a public HTTPS URL, or use the existing private `/ask` command.

“ค้นเว็บเพิ่ม 🌐” always previews the exact outgoing query in an editable modal. Only the original requester in the original channel may submit, within five minutes, once. Ambiguous/private follow-ups start with a blank query. The bot never builds a public query from stored Discord history, memories or its previous answer. Do not enter personal data or credentials; known credential patterns are also rejected, but pattern checks are not a complete privacy classifier.

Sources stay hidden unless requested. Ask “เอามาจากไหน” within thirty minutes or reply to the original answer to retrieve the retained source URLs without another search/model call. Long answers continue to split. `/mcp status` reports the last operation, not continuous uptime for every website. Capability replies use actual runtime settings.

## Orchestration and storage

Clear public search/URL requests take a direct route. Other lookup candidates may use a compact validated JSON planner (`search`, `fetch`, `none`), seeing only the current utterance and date. This implementation deliberately does not use Groq native `tool_calls`: the host controls MCP execution, while final synthesis has no callable tools. Public lookup synthesis excludes unrelated Discord history. Ordinary chat/history retrieval retains its previous path; interjections never invoke web tools.

Website text is untrusted reference data. It cannot enable tools, alter permissions, run shell commands or request further tool execution. Web `W1` provenance remains separate from Discord `S1` references and integer-ID parsers. Web references and answer continuity are scoped in RAM by guild/channel/requester/private mode, capped at thirty minutes/200 scopes, and cleared by invalidation/restart. Opt-out prevents retaining additional web evidence/assistant context.

SQLite remains schema2. It stores only a reviewed-schema fingerprint and short-lived usage counters for MCP, without raw query text, URLs or raw user IDs. Per-user counters use HMAC identities, count a rolling24h window and prune records older than48h on the next request. Privacy opt-out does not reset abuse-prevention quotas. Raw tool results are not placed in the database or backups.

## Limits and failure behavior

Defaults: six MCP calls/minute,100/rolling24h per instance,no per-user daily cap by default. These count tool calls, not questions. One request uses at most two MCP calls (search and optional first-result read) and two AI calls sharing the existing AI quota. Started work has a90-second deadline; queued messages expire after five minutes. Three tool failures open a60-second circuit and register a content-free fault with existing operator alerts.

Failed search differs from a successful empty search. A failed page read may leave usable, explicitly supplied search snippets; the bot must not claim it read that page. DuckDuckGo is not a live sports feed: results may contain only overview pages, fixtures or old matches. The bot must distinguish retrieval date, match date and completed results, and admit when the latest score cannot be verified. JavaScript/login/bot-blocked pages may be unavailable. No CAPTCHA solving or automatic paid fallback is provided.

## Service boundary

The sidecar receives no Discord/Groq credentials, database volume, Docker socket, host network or published ports. It uses a private control network plus web egress, UID10002, read-only root, dropped capabilities,384MB/CPU0.75/pids96. These boundaries do not make arbitrary downloaded MCP servers safe.

Only `search` and `fetch_content` are exposed. Tool names/schema are checked and fingerprinted; drift requires operator review. No resources/prompts/sampling/elicitation/write integrations are enabled. Upstream fetch is replaced by a public HTTPS reader that validates each redirect/DNS result and connects to the validated literal IP while retaining the original Host and TLS SNI/certificate hostname. Private/metadata/IPv6 transition addresses, credentials and Discord history URLs are rejected.

Text pages are capped at2MiB and extracted text4096bytes/page; MCP responses128KiB. The reader requests identity encoding and rejects compressed responses before decoding. Upstream search HTML is checked after receipt, with deadlines/container resource caps; this is not a claim of byte-by-byte streaming isolation throughout the upstream search stack.

## Validation and rollback

`tests/test_web.py` exercises routing, privacy, quotas, controls, schema drift and response limits alongside existing regression tests. `mcp_web/test_safe.py` tests synthetic DNS/TLS pinning, redirects, private addresses, oversized/compressed pages and the actual SDK envelope.

`scripts/mcp_probe.py` performs public search and HTTPS reading through actual Streamable HTTP without Discord credentials/login. `scripts/live_web_probe.py` additionally uses the configured Groq key and durable production quota for planner→MCP→final synthesis; it neither reads Discord chat nor creates Discord messages. Run live probes intentionally, not as frequent health checks.

Pre-rollout recovery: verified `pre-mcp-2026-10-28.sqlite3` in the persistent volume and `discord-ai-companion:before-mcp-aecce68`, reconstructed from the prior hotfix source without copying `.env`. Disable MCP and use that image to roll back; preserve the volume, schema remains2.

MVP supports one reviewed service and `/mcp channel` + `/mcp status`. Arbitrary server installation, service-specific role allowlists, private integrations and write tools remain future separately scoped work.

2026-10-28: local per-user tool cap increased from10 to30 calls/rolling24h after a real question was rejected at admission. Tool calls are not questions: search+read uses2. Instance100/day and6/min, real provider limits and room pauses unchanged. / เพิ่มเพดานเครื่องมือรายคนจาก10เป็น30ครั้งย้อนหลัง24ชั่วโมง ไม่ใช่โควตา AI และไม่ได้เปิดห้องที่พักไว้


2026-10-28 OWNER OVERRIDE: per-person daily tool cap REMOVED, superseding10→30 entries above. MCP_USER_REQUESTS_PER_DAY=0 disables this optional cap (positive values remain supported for existing operator configs). Current env/default/example use0. Global100/day and6/min and real provider limits remain. Owner explicitly approved resuming คุยเกียว-project-arcania; resume response only, no history/interjection expansion or old-question replay. / เอาเพดานค้นเว็บรายคนออกตามคำสั่งล่าสุด และอนุมัติเปิดห้องกลับแล้ว
