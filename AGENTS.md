# Damask Chief

This repository is the standalone developer product. Read README.md and docs/architecture.md before changing runtime behavior.

- Keep observed repositories external. Do not import their application modules, data, history, or deployment workflows.
- Preserve original human intent separately from generated interpretations. Agent behavior cannot redefine authority.
- Distinguish findings, authorization, queued delivery, emission, subsequent activity, adherence, and human acceptance.
- Default to Assist. Auto is repository-scoped, explicitly enabled, bounded, and deterministically checked again at delivery.
- Preserve other tools' Claude settings. Record exactly owned hook entries, merge carefully, and remove only those entries.
- Keep secrets, transcripts, local databases, model credentials, and user-specific paths out of Git.
- Local before cloud. Providers and agent adapters remain replaceable. No telemetry or unapproved model fallback.
- Use the locked dependencies. Focused checks: `uv run --frozen pytest -q`, `node --input-type=module --check < src/damask_chief_of_staff/web/app.js`, and `uv run --frozen python scripts/smoke_launcher.py` where loopback execution is available.
- Verify the UI through the available supported browser tool. Keep fixture outcomes separate from authenticated Claude/VM outcomes.
- No finance application, autonomous code repair, production deployment, or worker permission changes belong in this MVP.
