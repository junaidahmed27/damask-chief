# Damask Chief

A local chief of staff for developers running long Claude Code sessions. Connect a worktree, keep using Claude normally, and open Damask to inspect intent, compressed trajectories, evidence, and steering. Goals come from existing human requests.

This is a runnable Python service and browser interface, with its own SQLite store. It is independent of any observed application or finance repository.

## Deploy on your Windows VM

Prerequisites: **Python 3.12+**, **Git**, and **Claude Code 2.1.299+** in the runtime you use for coding.

```powershell
git clone https://github.com/junaidahmed27/damask-chief.git
cd damask-chief
.\Start.cmd
```

Or download this repository's ZIP, extract it, and double-click **Start.cmd**. First launch installs locked dependencies into an isolated environment; later launches reuse the same local service. No administrator privileges, Docker, WSL installation, or service registration are required.

In the browser:

1. **Connect repository**: select your coding worktree and approve bounded text capture. Damask merges project-local Claude hooks. Reload Claude and verify `/hooks`.
2. **Configure model**: choose your approved Claude CLI account/model, or an Anthropic Messages-compatible endpoint. Saving runs a synthetic route verification before enabling semantic reviews.
3. Continue your Claude sessions. Use **Attention**, **Trajectories**, and **Steering history**. Start in **Assist**; enable **Auto** separately for each repository when ready.

The VM does not need Claude to build this product. It needs Claude only for the sessions you want to supervise and, optionally, the supervisor's model route.

Existing WSL2 users can instead run:

```powershell
.\Setup.ps1 -Runtime WSL -Distro Ubuntu
```

Use the distribution where Claude and the selected repositories already run. This selects one store/runtime; it does not bridge native Windows sessions into WSL. See [deployment and operations](docs/operations.md).

## What is implemented

| Capability | Behavior |
| --- | --- |
| Session collection | Project-local Claude hooks, durable atomic spool, isolated session IDs, selected JSONL transcript import |
| Persistent intent | Original requests/corrections retained separately from model interpretations; version changes invalidate old guidance |
| Trace visualization | Compact summaries and event timelines with source IDs, check receipts, compaction markers, and delivery events |
| Chief-of-staff worker | Bounded background model review; grounded/schema-validated findings; no tools or command execution by the reviewer |
| Completion evidence | Developer-registered checks, actual exit status, before/after code fingerprints, stale-pass detection |
| Assisted steering | Preview exact text and destination, approve, queue, emit at a supported hook, retain the delivery receipt |
| Bounded Auto | Explicit repository policy; fixed reminders of literal constraints or registered missing/stale checks; dedupe, cooldown, caps, pause |
| Continuation | Copy protected intent, source references, unresolved decisions, and check receipts |
| Operations | Start/reuse, status, verified stop, disconnect, consistent backup/restore, session deletion |

Normal installation starts empty. `scripts/seed_fixture.py` is an explicitly separate QA fixture, never a source of live product claims.

## Verification and limits

The local runtime, hook executable, API boundary, state transitions, launcher reuse, and browser workflow have been exercised on macOS with isolated fixtures. See [validation](docs/validation.md) for exact evidence and remaining gates. Windows/Linux checks are configured in [CI](https://github.com/junaidahmed27/damask-chief/actions).

**Authenticated live Claude supervision and Windows/WSL integration still require verification on the target VM.** The development host had no Claude authentication. A working provider adapter and fixture checks do not establish model quality or worker adherence.

Raw tool output, hidden reasoning, source diffs, arbitrary business documents, cloud sync, and autonomous code changes are outside this version. Known-secret redaction is best effort; choose engineering sessions that are appropriate for the configured data boundary. No model is enabled by default and no telemetry is sent.

## Development

```sh
uv sync --frozen --extra dev
uv run --frozen pytest -q
uv run --frozen python scripts/smoke_launcher.py
uv run --frozen damask start
```

Architecture and editable supervisor prompt: [architecture](docs/architecture.md), [chief-v1.txt](src/damask_chief_of_staff/prompts/chief-v1.txt). Runtime dependencies are locked in `uv.lock` and the hash-pinned `requirements.lock`. The wheel contains the backend, prompt, and web assets. No open-source license has been selected yet.
