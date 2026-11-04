# Companion controls and conversation upgrade

> Quota/conversation behavior is superseded by the [latest follow-up guide](CONVERSATION_FOLLOWUPS.en.md): displayed quotas are provider-reported only, and completed token usage releases reservation overestimates.

2026-10-28. See PROGRESS.md for tested/deployed evidence.

Use `/panel` for an owner-bound, five-minute ephemeral control panel. Personal pages cover tone, auto/short/detailed length, temporary banter reset and privacy. Manage Server is required for room settings, interjections, tool policies, room news, instance-wide usage/budget, health and emergency controls. Every change re-fetches member/channel permissions and rejects stale guild-role snapshots. Existing history ingestion and audience rules remain separate.

Interjections remain off until explicitly enabled per room. The panel offers probability, at least300s cooldown, hourly limit(default3) and quiet hours. A bounded RAM scene(6messages,30s,100rooms) additionally needs3messages from2people without distress markers. Opt-out members are excluded. This is a heuristic, not guaranteed emotional understanding. Dedicated bot rooms retain ordinary untagged conversation.

Direct stop-banter phrases temporarily lower personal tone for30minutes; panel restores the prior preference. Tone0–4 remains. Length adapts through instructions, preserves complete output and still splits long Discord messages. Requested short/detailed overrides the saved default.

`/joke propose` creates a five-minute public consent proposal with no notifications. The subject must consent and a fresh Manage Server member must approve. Self-proposal supplies subject consent. Max20approved jokes/room,240characters,30-day expiry. List/remove commands are available; author,subject or admin may remove. Mention checks are syntactic; admins must review content. Jokes are optional untrusted references only for the consenting subject and nonzero tone, never verified facts. Privacy deletion also removes related jokes and blocks restoration.

Ambiguous lookup requests offer web/history/both. Button results are private to the requester, owner-bound and expire after5minutes. Combined lookup separately confirms the public query; Discord history never becomes an automatic outgoing web query. Fresh historical ACL/source checks remain. More continues history cursors or extends public searches and removes seen URLs in scoped RAM(30minutes,200scopes). Web continuation is best-effort query refinement, not a provider cursor. Focus opens the original public query for refinement; potentially private queries start blank. Sources reuse stored provenance without another model call. Normal answers still hide sources.

Compare at most3HTTPS links with one text-read each, no browser fallback for comparison. All share MCP quotas and the90s job deadline. Failed/missing pages must be disclosed; bounded context may not include complete pages. Room news preferences support10public topics,10excluded terms and one public RSS/Atom URL; empty feed uses existing defaults. No automatic news posting.

AI accounting separates chat/planner and counts every attempt. Tool counts remain separate. Price is unknown until an admin supplies a conservative USD/million-token rate(maximum input/output rate) and optional rolling24hUSD cap. Reservations are durable and serialized before requests; failures/cancellations retain conservative reservations. This is a manually priced estimate, not an invoice or provider-price synchronization. Previously unpriced calls remain unknown; new prices do not retroactively reprice reservations. Image/voice are disabled. Keyless tools still consume host/network resources.

Health shows timestamped actual tool outcomes separately from MCP schema discovery every5minutes or on demand; discovery invokes no paid model or website-reading tool. Reachability does not guarantee a particular page/video works. Provider health uses the last actual request.

`/stop` or panel emergency controls drain matching queued jobs, cancel active processing and prevent subsequent delivery. Room and guild scope are available; resume never replays old jobs. Requests already accepted externally may still incur charges. `/pause` remains supported.

“อย่าจำฉัน” and `/privacy store_data:false` disable new storage and erase current personal history/memories/RAM. “ลบข้อมูลฉัน” directs to DELETE confirmation in the panel; `/memory clear confirm:true` remains. Original Discord messages are not deleted. Opt-out still permits a current question to be sent to the AI for its answer.

The sibling0600`privacy-ledger.sqlite3` stores minimal identifiers,cutoffs,optout and deleted-item IDs, never chat bodies. It is deliberately outside rotating database backups and must be retained securely with recovery assets. Losing/replacing it with an old copy defeats protection against resurrection from older backups. Restore reapplies the retained journal and carries it to a new recovery directory; `--privacy-folder` selects the retained directory. Old erased messages are not reimported. Individual deletion cutoffs avoid deleting newly created memories that reuse an ID after old-snapshot recovery.

Recovery drills run weekly after a snapshot exists, or manually in the health panel. Restore into a temporary directory, apply erasure rules, verify schema/integrity/basic reads and remove the copy. Never replace production; not a semantic audit of all content.

No new services/dependencies, GUI, exposed ports, image understanding or voice. Quoted web/chat/caption/joke data cannot change host policy or directly execute tools. Backup and tag before-controls-019f6c4 before rollout. Schema remains2 with compatible added quota columns. Older code cannot enforce new policies or new deletion requests: retain the journal and apply it with current recovery tooling before rolling back.
