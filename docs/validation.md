# MVP validation — 11 November 2026

## Verified locally

Environment: macOS arm64, Python 3.12.14, Node 26.7.0, uv 0.12.6. Claude Code 2.1.299 is installed; `claude auth status --json` reported no authentication.

- **20 focused runtime tests pass.** Real hook subprocess/spool/ingestion, replay after restart, session isolation, late intent handling, compaction evidence, transactional summary rollback, filtering, settings merge/uninstall, Assist and Auto state transitions, cooldown/caps, unknown delivery, pending-prompt cancellation, independent check failure/pass/staleness, grounded model output validation with controlled provider responses, stale-intent rejection, API Host/Origin/auth/CSRF, selected transcript import, provider request construction, failed model-route verification, backup restore that cannot reactivate old Auto authority, and UTF-8 hook delivery even when the console defaults to ASCII.
- **JavaScript syntax and Python compilation pass.** The actual web client runs without browser console errors in the inspected workflow.
- **Wheel packaging passes.** Backend modules, HTML/CSS/JS, and the versioned supervisor prompt are included. An isolated wheel installation passed authenticated readiness, repeated launch reusing one process, and verified owned-process shutdown.
- **Live local browser walkthrough passes on isolated fixtures.** Sign-in; Attention; independent check receipt; exact assisted-message preview/approval; persisted queued action; real hook executable emitting its context output; reloaded delivery history; compact and retained trace views; explicit repository Auto enablement; pause while sessions remain active.
- **No real external repository was connected during verification.** The QA store clearly identifies simulated Claude events. Check commands actually ran against a disposable local code fixture. A failed ascending-order assertion produced a real independent failure receipt.

The test client currently emits a Starlette deprecation warning about its HTTPX transport. This is a test-harness warning; the production service uses Uvicorn and the configured provider client. It is recorded rather than hidden.

## Target-runtime gates

- The initial implementation passed Windows and Linux CI (runtime suite, authenticated loopback launch/reuse/stop, wheel build, and Windows PowerShell parsing). Final changes rerun that matrix; read the exact commit's workflow result. Native Claude hook execution and real Windows/WSL browser setup still require target-VM verification.
- WSL2 distribution selection and Windows-to-WSL browser forwarding were not exercised on this Mac.
- Authenticated Claude model output, actual Claude consumption/adherence after context injection, and live session resume/compaction were not tested because this host lacks Claude authentication. Controlled provider tests establish plumbing and grounding validation, not model judgment quality.
- No target VM deployment, customer session capture, production application changes, reboot/sign-out durability, or cloud integration is claimed.

## First VM run

Use Start.cmd, connect a disposable worktree, reload Claude, verify /hooks, then submit a small task. Configure the approved model route and pass its synthetic probe. Confirm one real review and one benign reminder consumed by the original session. Check Auto/pause on the fixture before enabling it for normal work. Run the receipt command to record that host's actual runtime, model-route selection, paths, and connected projects.
