# Reasoning-output and live-score question fix

2026-10-28 — Latest rollout evidence: [PROGRESS.md](../PROGRESS.md).

The owner reported a misspelled latest-Manchester-United-score question receiving a `[Thinking Process]` trace about unrelated anime history. This release still has no connected MCP/live-score source.

## Changes

- Request Groq with `reasoning_effort=none` plus `reasoning_format=hidden`. Do not also send `include_reasoning`, which is mutually exclusive with the format parameter in [Groq's documentation](https://console.groq.com/docs/reasoning).
- Validate recognized output envelopes at both provider and renderer. Preserve clearly delimited final text; reasoning-only, incomplete and nested reasoning markup fails closed to a fixed status message, before multipart delivery.
- Never incorporate the separate `message.reasoning` field into the answer. Keep quota accounting for rejected completions, including valid reported usage.
- Preserve readable `finish_reason=length` answer text and multipart delivery, while adding a continuation notice. A reasoning-only completion is not a usable partial answer.
- Prompt for a user-facing final answer, not prompt/reference reconstruction; prioritize the current request over irrelevant historical topics.
- Prohibit degrading direct addresses and blaming the user for capability questions. The output boundary also rejects recognized direct-insult phrases reported by the owner; ordinary non-targeted mild slang remains subject to tone settings.
- Recognize explicit live-football requests, including the reported typo, before history/model calls. State that current scores cannot yet be verified, rather than inventing them or guessing anime subjects. Explicit questions about what Discord members said still use history retrieval.

## Limits

This is a deterministic guard for known formats, not a universal classifier of arbitrary prose. Literal reasoning markup in an otherwise legitimate technical answer can trigger the safe fallback. Normal Thai, emoji, code and complete splitting retain regression coverage.

No MCP installation, model/dependency/schema change is part of this fix. Future MCP work must replace the no-live-score path only when an actual verified lookup capability is available.

Reproduction uses synthetic data, not the reported trace/real user IDs. The original wire response was not captured; exact stochastic generation and its finish reason are not claimed as replayed or known.
