# Deployment and operations

## Stage 1 — Install in the coding runtime

Clone `junaidahmed27/damask-chief`, or extract its ZIP. Native Windows is the default. Install Python 3.12+, Git, and Claude Code 2.1.299+ first. Double-click `Start.cmd`; it calls `Setup.ps1` on the first launch, creates `.venv`, installs the hash-pinned runtime dependencies and this package, then starts a loopback service on an unused port.

If your coding sessions already run in WSL2, explicitly select that existing development distribution with `Setup.ps1 -Runtime WSL -Distro <name>`. Setup checks the distribution and WSL2 kernel, uses `.venv-wsl`, and writes a small ignored runtime selector so subsequent Windows launch controls target the same installation. It does not install WSL, select Docker/system distributions, or request a reboot. Python 3.12+ must also exist in that distribution. Windows-to-WSL localhost forwarding must be enabled by your environment.

Do not move the installed checkout without rerunning setup and reconnecting hooks, which contain the installed Python executable path. Stop the owned service before upgrading the package. No auto-start service or scheduled task is installed. Closing a browser leaves the service running; sign-out/reboot persistence is not promised.

## Stage 2 — Connect selected worktrees

Open Damask with `Start.cmd`. Choose **Connect repository**, enter an absolute Git worktree root in the selected runtime, and approve filtered capture. Separate Git worktrees must be connected separately. Damask backs up existing local settings in its private state directory, preserves other hooks/permission settings, and adds only recorded command-hook entries to `.claude/settings.local.json`. It adds that local settings path to Git's private exclude file.

Reload Claude and inspect `/hooks`. Managed or remote policy may prevent project hooks even when local settings are valid; Damask does not bypass it. Start with a disposable engineering task. A prompt and a tool action should appear as a distinct session without entering the task again. Existing sessions can be imported by selecting one `.jsonl` file belonging to the connected worktree. Import supports visible user/assistant message records with `cwd`, `sessionId`, and message content; unknown schemas fail closed. It skips thinking and tool-output blocks, limits each pass to 10 MB, and resumes from a persisted cursor.

Capture retains selected prompts, visible stop messages, event types, tool names, safe engineering file paths, supported numeric result metadata, and provenance. It excludes arbitrary command contents, source contents, raw tool stdout/stderr, keys matching known patterns, and excluded artifact paths. Redaction cannot classify every private string. Do not connect sessions containing business documents or credentials merely because a path filter exists. Text capture can be disabled through `damask connect <root>` without `--capture-text`; such sessions cannot infer goals from withheld text.

## Stage 3 — Enable the approved model route

Use **Configure model**. No route is enabled by default.

- **Claude CLI** uses the installed CLI's existing authentication and explicitly selected model. Authenticate Claude in this runtime first. Damask disables tools, customizations, hooks, skills, MCP servers, and transcript persistence for the isolated reviewer. Admin-managed authentication/provider policies can still affect the route; confirm they are appropriate for the selected repositories.
- **Anthropic Messages-compatible HTTP** takes an exact HTTPS Messages URL, approved model/deployment ID, and credential environment-variable name. The service reads that variable; keys are not placed in Git or the browser. Set the variable in the environment that launches Damask, then restart Damask. No environment proxy, redirect, or fallback provider is silently used. Other wire protocols are not implemented in this version.

Saving tests the route with synthetic evidence and validates the output. Failed verification leaves the previous configuration intact. The reviewer is single-concurrency, debounced, and limited to 12 reviews/session/hour and 40 installation-wide/hour. HTTP output is capped at 1,500 tokens; CLI output is bounded by size and timeout. Input is a bounded window, not the entire session. Provider token usage is recorded when returned; cost and worker usage remain unknown.

If semantic review is unavailable, hook ingestion, extractive summaries, focused checks, and manual inspection continue. The UI exposes the degraded review state. No successful review or model finding is fabricated.

## Stage 4 — Inspect and steer

**Attention** links findings to retained evidence. **Trajectories** switches between compact provenance-bearing summaries and raw retained events. These are filtered records; an edit marker does not include a source diff. Actor attribution for shared-worktree Git changes is unknown. **Correct understanding** appends a protected human correction to Damask and invalidates old guidance; it does not silently send a replacement task to Claude.

Register a focused check only after inspecting its executable/arguments. It runs with no shell in the selected worktree, with a restricted environment and a timeout. It may still modify files, because an authorized test command is not sandboxed. Output is discarded; the receipt records status and code fingerprints. A passing check supports only its scope, never whole-task acceptance. New file changes make a prior pass stale.

Modes are per repository:

- **Observe:** no delivery.
- **Assist:** exact text/destination preview and individual approval.
- **Auto:** explicitly enabled fixed reminders of a literal human constraint, or a registered check whose receipt is missing/stale. Ambiguity remains human-only. Auto does not run checks itself, grant worker permissions, change scope, modify files, restart workers, or deploy.

Auto permits one reminder per finding/intent and underlying constraint/check, at least two minutes between reminders, at most three per session in fifteen minutes. Pause/revocation/new intent invalidates queued guidance. A previous unknown delivery prevents automatic resending. Guidance is emitted through supported `SessionStart` or `PostToolUse` additional context at the next opportunity. `UserPromptSubmit` invalidates old guidance first; it is never used to force continuation. No Stop-hook loops or terminal injection exist.

Delivery states remain separate: queued, delivery-unknown, emitted, activity-observed, cancelled. `delivery-unknown` is committed before writing hook stdout, so a crash does not cause blind resend. Emission is not consumption; later activity is not adherence. Use **Copy message** if the session has ended or hooks cannot deliver. Repeated unresolved issues require a human decision.

## Files and controls

| Item | Native Windows | WSL/Linux/macOS |
| --- | --- | --- |
| Private home | `%LOCALAPPDATA%\Damask\ChiefOfStaff` | `~/.local/share/damask/chief-of-staff` |
| Database | `data\chief-of-staff.sqlite` below private home | `data/chief-of-staff.sqlite` below private home |
| Configuration | `config.json` in private home | Same |
| Atomic spool | `spool` in private home | Same |
| Backups / receipts | `backups`, `deployment-receipt.json` | Same |
| Start | `Start.cmd` / `start.ps1` | `damask start` |
| Status / stop | `status.ps1` / `stop.ps1` | `damask status` / `damask stop` |
| Disconnect | UI or `disconnect.ps1 -ProjectId <id>` | UI or `damask disconnect <id>` |

Use `DAMASK_HOME` or CLI `--home` only when you intentionally want a separate private installation. Keep live SQLite on a local, non-synchronized filesystem. Windows initialization restricts the new private directory ACL to the current account. POSIX directories are created with private permissions. The per-install token and origin-bound browser session protect the loopback interface, with Host/Origin/CSRF checks. Browser authentication is held in per-origin sessionStorage and sent in a custom header, avoiding cookies shared across localhost ports. It is not a store of work history. This is a single-user tool; it does not isolate malicious processes running as that same OS user.

`damask backup` creates a consistent SQLite backup while running. The UI has the same command. Configuration/credentials and original transcripts are separate. Stop the server before `damask restore <backup.sqlite> --confirm`; restore checks schema/integrity, backs up the current database, cancels restored queued messages, pauses steering, and disconnects restored projects until you explicitly reconnect them. Hooks and model routing belong to the current machine, so verify/reconnect them after restoring onto another host.

`damask delete-session <session-id> --confirm` deletes selected local retained records while the service is stopped. First stop/disconnect the originating session so new events cannot recreate it. Matching pending spool records are removed and SQLite space is reclaimed. Original transcripts and backups require separate explicit disposition. Automatic retention deletion is intentionally absent; quotas pause non-human event ingestion and expose a coverage warning rather than silently dropping intent. Start a fresh isolated installation if necessary while preserving the old store for review.

Stop only targets a process whose PID, creation time, command line, and private home match this installation. Unrelated application processes are left alone. Repeated start reuses an authenticated, ready instance. If a known process is alive but unready, inspect its private `logs/server.log` instead of creating another one.

## VM acceptance

After setup, confirm a real prompt and tool action arrive; verify the model route; inspect one grounded review; approve one benign reminder and see it consumed in the original Claude session; enable Auto on a disposable task; pause it; launch twice; stop/start and inspect preserved intent/receipts. Save `damask receipt`. Record Windows/WSL, actual Claude version, provider route, and any managed-hook limitations. These live target-runtime checks cannot be inferred from local fixture tests.
