from __future__ import annotations
import hashlib
import json
from pathlib import Path
from .adapters import session_id
from .engine import Engine
from .privacy import text
from .store import now


def import_transcript(store, project_id, path):
    project = store.get("projects", project_id)
    if not project["connected"]:
        raise ValueError("Connect the repository first")
    path = Path(path).expanduser().resolve(strict=True)
    if path.suffix != ".jsonl" or not path.is_file():
        raise ValueError("Select a Claude JSONL transcript, not a directory")
    # Version-aware conservative reader: user/assistant text only; thinking/tool output excluded.
    matched = False
    native = None
    with path.open("rb") as f:
        for _ in range(100):
            line = f.readline(1_048_577)
            if not line:
                break
            if len(line) > 1_048_576:
                raise ValueError("Transcript has oversized records; import stopped")
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if obj.get("cwd"):
                if Path(obj["cwd"]).resolve() != Path(project["root"]):
                    raise ValueError("Transcript belongs to a different worktree")
                matched = True
                native = obj.get("sessionId")
                break
    if not matched or not native:
        raise ValueError("Unsupported transcript schema or missing worktree/session identity; no content imported")
    sid = session_id(project_id, native)
    with path.open("rb") as f:
        fingerprint = hashlib.sha256(f.read(256)).hexdigest()
    with store.connect() as db:
        previous = db.execute("SELECT * FROM imports WHERE path=?", (str(path),)).fetchone()
    cursor = previous["cursor"] if previous and previous["fingerprint"] == fingerprint and path.stat().st_size >= previous["cursor"] else 0
    engine = Engine(store)
    consumed = 0
    count = 0
    with path.open("rb") as f:
        f.seek(cursor)
        while consumed < 10_000_000:
            offset = f.tell()
            line = f.readline(1_048_577)
            if not line or not line.endswith(b"\n"):
                break
            if len(line) > 1_048_576:
                raise ValueError("Transcript record exceeds import bound")
            obj = json.loads(line)
            consumed += len(line)
            if obj.get("cwd") and Path(obj["cwd"]).resolve() != Path(project["root"]):
                raise ValueError("Transcript worktree changed; import stopped")
            if obj.get("sessionId", native) != native:
                raise ValueError("Mixed session IDs; import stopped")
            kind = obj.get("type")
            content = obj.get("message", {}).get("content", "") if isinstance(obj.get("message"), dict) else ""
            if isinstance(content, list):
                content = "\n".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")
            if kind in ("user", "assistant") and isinstance(content, str) and content:
                stable = hashlib.sha256((str(path)+str(offset)).encode()+line).hexdigest()
                data = {"gap": "Imported visible messages only; early/tool context may be incomplete", "source": "selected Claude JSONL import"}
                if project.get("capture_text"):
                    data["prompt" if kind == "user" else "last_assistant_message"] = text(content)
                # Source ordering uses recorded timestamps when supported, never import time for old intent.
                from datetime import datetime
                try:
                    timestamp = datetime.fromisoformat(obj["timestamp"].replace("Z", "+00:00")).timestamp()
                except (KeyError, ValueError, TypeError):
                    timestamp = 0.001 + offset / 1_000_000_000
                    data["gap"] += "; source time unavailable"
                engine.ingest({"id": "import_"+stable, "session_id": sid, "native_id": native, "project_id": project_id,
                    "root": project["root"], "kind": "UserPromptSubmit" if kind == "user" else "ImportedMessage", "ts": timestamp, "data": data})
                count += 1
            # Event commit precedes cursor: an interrupted update replays idempotently.
            cursor = f.tell()
            with store.transaction() as db:
                db.execute("INSERT OR REPLACE INTO imports VALUES(?,?,?)", (str(path), cursor, fingerprint))
    return {"session_id": sid, "messages_processed": count, "cursor": cursor, "more": cursor < path.stat().st_size}
