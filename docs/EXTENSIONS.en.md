# Selected extensions, level4 and accessible text rooms

2026-10-28. See PROGRESS.md for actual test/deployment evidence.

## Tools

- RSS: ask “RSS ข่าวล่าสุด” or supply a public RSS/Atom URL. Defaults are BBC Technology and Ars Technica. General news first samples these feeds, then uses DuckDuckGo if needed, within two MCP calls. Explicit RSS requests do not silently substitute web search. Entries retain publication dates and links.
- YouTube Transcript: provide a YouTube URL for available Thai/English public captions with timestamp links. No extra API key is required. At most five caption chunks form a bounded sample; the bot has not watched images, listened to audio or necessarily read the entire video. Missing/blocked captions produce an honest failure notice.
- Playwright: explicitly request Playwright reading of a public HTTPS URL, or use the one-shot fallback after ordinary page reading fails. JavaScript-rendered DOM becomes text. No AI-controlled clicks/forms/login/evaluation/downloads are exposed. POST-dependent or bot-blocked sites may remain unavailable.

The browser is a purpose-built read-only MCP service using Microsoft Playwright Python1.64.1, not the full automation toolbox from microsoft/playwright-mcp. Existing SDK2.3.0 remains; RSS parsing uses defusedxml0.7.1 and keyless captions youtube-transcript-api1.2.4. Dependencies are hash-locked. Discord admin/write tools, incoming screenshots and voice remain out of scope. Sources stay opt-in and long replies still split.

## Level4: playful chaos

Use `/tone level:4`, with the room ceiling set by an administrator using `/settings tone level:4`. The effective level remains the minimum of personal and room settings. Level0 stops teasing, levels1–3 retain their existing behavior. Level4 adds absurd comparisons, mock-dramatic overreactions, self-roasts and short punchlines, rather than simply stronger profanity.

Respect welcomed language, boundaries and serious distress. Do not degrade personal worth or invent facts/scores for a joke. Factual answers lead with useful information and at most one playful line. Existing reasoning/direct-abuse guards remain active. Humor quality is subjective, not a deterministic guarantee.

## Accessible rooms

`AUTO_RESPOND_ACCESSIBLE_CHANNELS=true` enables mention/reply conversation in visible/sendable text channels within the configured guild, without configuring each new room. Only designated bot rooms respond without a mention; interjections do not become enabled automatically.

New rooms do not enable persisted chat ingestion or cross-room sharing. Scoped RAM conversation continuity remains available. Their tone ceiling supports4, but the personal default1 still limits effective teasing. Explicit response disable/pause and `/mcp channel mode:off` remain effective. Unconfigured tool modes default to hybrid when automatic room response is enabled.

Fresh Discord permissions are checked before work/delivery. Without ReadMessageHistory, use only fresh Gateway input (30seconds), ordinary sends and no historical REST reads; older-source buttons require revalidation and are omitted, while private `/ask` remains available. History ACLs continue to require history permission.

## Headless Docker boundary

Four services, no published host ports: bot keeps Discord/Groq credentials and the existing database volume; mcp-web handles public search/feed/caption tools and the browser bridge; mcp-browser exposes only read_page; web-proxy provides public HTTPS CONNECT443.

Browser UID10003 has read-only root, no credentials/volumes and only an internal network. Each read creates and closes a fresh browser context. Chromium forces the proxy including loopback, disables QUIC/non-proxied WebRTC, blocks service workers/WebSockets and permits only GET HTTPS page requests. The proxy validates all DNS answers and connects a validated literal IP; Chromium still verifies the original TLS hostname/certificate. No ignore-https-errors or generic internal-network access is enabled.

Chromium runs --no-sandbox inside the restricted container, so container/network/request isolation is essential; this is not a universal security guarantee. Browser limits:768MB/CPU1/pids256/tmp128MB/shm128MB. Proxy:128MB/CPU0.5/pids64. Profiles are ephemeral. RSS uses the existing actual-IP-pinned HTTPS reader. Only optional RSS_FEEDS (JSON array of1–3 public URLs) is forwarded to mcp-web, never the bot's whole .env.

Allowlisted MCP tools expand from2 to6, with deliberate operator schema-pin replacement after review. Existing shared AI quota and MCP6/minute,100/day instance,10/day user ceilings remain. At most two MCP calls/request. Browser client deadline30seconds/server20seconds; whole job90seconds; DNS4seconds/feed8seconds. Failed reads do not invent evidence or strand the queue.

## Validation and recovery

PROGRESS.md distinguishes fixture regressions, actual public RSS/caption/JavaScript probes, model-style checks and user acceptance. A successful probe does not promise every site/video will work. Before rollout retain verified backup and before-extras-ecfa23a images. Schema remains2. To roll back, disable automatic room response, use prior bot/mcp-web images, restore the reviewed old schema pin and cap stored tone4 to3 for the older UI, preserving the data volume.
