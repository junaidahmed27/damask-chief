# Architecture

One Python service, one SQLite database, one static browser client. There is no dependency on a monitored repository's application runtime, database, or deployment system.

```text
Selected Claude worktree
  command hooks → filtered atomic spool → idempotent event ingestion
                  ↑                                ↓
  additionalContext ← gated delivery queue    protected human intent
                                               events + check receipts
                                                      ↓
                                 extractive summaries / model reviewer
                                                      ↓
                                   grounded findings + exact suggestions
                                                      ↓
                        local authenticated UI → approval / repository policy
```

- `adapters.py`, `hook.py`, `importer.py`: selected-repository integration and conservative Claude capture. Hooks never wait for inference. Import is explicit and idempotent. Other harnesses can add adapters without changing the work model.
- `store.py`, `engine.py`: transactional durable records, append-only human sources and audits, intent versions, summary provenance/cursors, replay, findings, continuation. Generated interpretations are separate summary records, not edits to original human instructions.
- `provider.py`, `supervisor.py`, `prompts/chief-v1.txt`: tool-disabled model adapter, schema/reference validation, bounded context, single reviewer, budgets, background collection during inference, uncertainty, dedupe and feedback. The model proposes; the application validates and authorizes.
- `checks.py`: explicit registered argv, per-run before/after engineering fingerprints, discarded output, timeout, freshness. No model-authored shell execution. A receipt can be partial or stale.
- `steering.py`: exact messages, policy versions, current-intent checks, explicit literal/check authority, dedupe/cooldown/caps, cancellation, crash-safe uncertainty, observed activity separated from compliance.
- `api.py`, `web/`: token sign-in, origin-bound browser session header, CSRF and Host/Origin checks, escaped evidence, attention/session/trace/steering/settings views. Browser storage is not an authoritative record store.
- `cli.py`, launch scripts: owned-process lifecycle, one selected runtime, loopback readiness, backup/restore, operations receipt.

Initial database schema version is 1. Future migrations must preserve source/authority references and cancel pending actions whose authorization can no longer be established. Summary cursor and extractive summary publish commit together. Hooks write one uniquely named file per event, then atomically rename. Crashes after ingestion but before file removal replay without duplicate event IDs.

The model's context is intentionally smaller than the retained store: recent human sources, recent normalized events, literal constraints, check registrations, and code fingerprint metadata. Older intent remains queryable but is not all sent every review. Every summary is versioned with sources and coverage; omitted content must remain unknown. Original requests are never replaced by lossy summaries.

This MVP detects drift primarily from human text, file/tool metadata, stop messages and independent check evidence. It does not read full code diffs or arbitrary tool stdout. Supporting edits may be legitimate; drift is a hypothesis until its evidence and uncertainty are reviewed. Missing full context is a limitation to measure with real development sessions.

Cloud synchronization, team identity, multi-user authorization, cross-runtime Windows/WSL collection, more harnesses, native agent-control APIs, autonomous code repair, and organization-wide memory are future boundaries. No proprietary/cloud dependencies are needed to run this local core.

Implementation references: [Claude hooks](https://code.claude.com/docs/en/hooks), [settings precedence](https://code.claude.com/docs/en/settings), [CLI reference](https://code.claude.com/docs/en/cli-reference), [Messages API](https://platform.claude.com/docs/en/api/messages/create). The installed Claude 2.1.299 CLI help and current documented exec-form hook/context contract were inspected during this build. Runtime verification still belongs to the actual target host.
