"""Fail-open Claude command hook. No network, model calls, raw stdout, or arbitrary commands."""
from __future__ import annotations
import argparse
import json
import os
import sys
from pathlib import Path
from .config import Config, atomic_json
from .store import Store, uid, now
from .adapters import session_id
from .privacy import normalize
from .engine import cancel_actions
from .steering import claim, emitted


def handle(config, project_id, raw, output=sys.stdout):
    store = Store(config)
    project = store.get("projects", project_id)
    if not project.get("connected") or os.environ.get("DAMASK_SUPERVISOR") == "1":
        return
    if Path(str(raw.get("cwd", ""))).resolve() != Path(project["root"]).resolve():
        # Separate worktrees must be explicitly connected; never infer authority from parent paths.
        return
    native = str(raw.get("session_id", ""))
    if not native or len(native) > 200:
        return
    sid = session_id(project_id, native)
    kind, data = normalize(raw, project)
    spool = config.home / "spool"
    if sum(p.stat().st_size for p in spool.glob("*.json")) > config.data["spool_max_bytes"]:
        atomic_json(config.home / "collector-error.json", {"message": "Event spool full; capture gap", "ts": now()})
        return
    event = {"id": uid("e_"), "project_id": project_id, "root": project["root"], "session_id": sid,
             "native_id": native, "kind": kind, "ts": now(), "data": data}
    # The original transcript remains with Claude. Keep only a source locator, never read here.
    if raw.get("transcript_path"):
        event["data"]["source"] = "Claude transcript (not copied)"
    filename = str(int(event["ts"] * 1_000_000)) + "-" + event["id"] + ("-human" if kind == "UserPromptSubmit" else "") + ".json"
    atomic_json(spool / filename, event)
    if kind == "UserPromptSubmit":
        with store.transaction() as db:
            cancel_actions(store, db, session_id=sid, reason="New prompt pending ingestion")
        return
    # Pending human instructions must be processed before any old guidance is emitted.
    if any(spool.glob("*-human.json")):
        return
    action = claim(store, sid, kind)
    if action:
        payload = {"hookSpecificOutput": {"hookEventName": kind,
            "additionalContext": "[Damask guidance; existing permissions still apply]\n" + action["message"]}}
        output.write(json.dumps(payload, ensure_ascii=False) + "\n")
        output.flush()
        emitted(store, action)


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser()
    p.add_argument("--home", required=True)
    p.add_argument("--project", required=True)
    args = p.parse_args()
    try:
        config = Config(args.home)
        raw = sys.stdin.buffer.read(1_048_577)
        if len(raw) > 1_048_576:
            raise ValueError("Hook payload exceeded capture bound")
        handle(config, args.project, json.loads(raw))
    except Exception:
        # Never emit exit code 2 or block the worker; no raw error payloads in logs.
        try:
            atomic_json(Path(args.home) / "collector-error.json", {"message": "Hook capture or delivery failed; inspect coverage", "ts": now()})
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
