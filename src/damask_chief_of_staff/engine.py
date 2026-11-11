from __future__ import annotations
import hashlib
import json
import re
from pathlib import Path
from .privacy import text
from .store import Store, uid, now, dump


def intent_for(store, session, db=None):
    versions = store.all("intents", db, session_id=session["id"])
    return max(versions, key=lambda x: x["version"]) if versions else None


def cancel_actions(store, db, session_id=None, project_id=None, reason="Authority changed"):
    for a in store.all("actions", db):
        if session_id and a["session_id"] != session_id:
            continue
        if project_id and a["project_id"] != project_id:
            continue
        if a["state"] in ("queued", "proposed", "authorized"):
            a.update(state="cancelled", reason=reason, updated=now())
            store.put("actions", a, db)
            store.audit("action.cancelled", {"id": a["id"], "reason": reason}, db)


def add_intent(store, db, session, event_id, content, ts, confirmed=False):
    old = intent_for(store, session, db)
    sources = list(old["source_ids"]) if old else []
    sources.append(event_id)
    # Each user turn is protected. Inferred summaries cannot erase these sources.
    record = {"id": uid("i_"), "session_id": session["id"], "version": (old["version"] if old else 0) + 1,
        "source_ids": sources, "latest_instruction": content, "goal": content[:500],
        "basis": "human-confirmed" if confirmed else "human-source; interpretation pending", "constraints": [],
        "ambiguities": [], "created": ts}
    # Extract only literal prohibitions; these are not model-generated permissions.
    for n, sentence in enumerate(re.split(r'(?<=[.!?])\s+|\n', content)):
        if re.search(r"\b(do not|don't|never|must not|only change|only edit)\b", sentence, re.I):
            record["constraints"].append({"id": event_id + ":c" + str(n), "quote": sentence[:1000], "source_id": event_id})
    if old:
        record["prior_constraints"] = old.get("constraints", [])
        # A new instruction may supersede old constraints; retain them as unresolved,
        # not as automatic authority until the next grounded review confirms scope.
    store.put("intents", record, db)
    session["intent_version"] = record["version"]
    cancel_actions(store, db, session_id=session["id"], reason="New human instruction")
    for f in store.all("findings", db, session_id=session["id"]):
        if f["status"] in ("open", "snoozed"):
            f.update(status="superseded", updated=now())
            store.put("findings", f, db)
    return record


def add_event(store, db, event):
    cursor = db.execute("INSERT OR IGNORE INTO events(id,session_id,kind,ts,ingested,data) VALUES(?,?,?,?,?,?)",
        (event["id"], event["session_id"], event["kind"], event["ts"], now(), dump(event["data"])))
    return cursor.rowcount == 1


def finding(store, db, session, key, kind, title, explanation, evidence_ids, proposed="", **extra):
    version = session.get("intent_version", 0)
    existing = store.all("findings", db, session_id=session["id"], issue_key=key, intent_version=version)
    if existing:
        f = existing[0]
        # Feedback persists until genuinely new evidence, not a repeated review.
        if set(evidence_ids) <= set(f["evidence_ids"]):
            return f
        if f["status"] in ("dismissed", "expected", "resolved"):
            f["status"] = "open"
        f.update(evidence_ids=evidence_ids, updated=now(), explanation=explanation)
    else:
        f = {"id": uid("f_"), "session_id": session["id"], "issue_key": key, "intent_version": version,
             "kind": kind, "title": title, "explanation": explanation, "evidence_ids": evidence_ids,
             "proposed": proposed, "status": "open", "created": now(), "updated": now(),
             "severity": "medium", "uncertainty": "", **extra}
    store.put("findings", f, db)
    return f


class Engine:
    def __init__(self, store: Store):
        self.store = store
        self.config = store.config

    def ingest(self, event):
        s = self.store
        project = s.get("projects", event["project_id"])
        if not project.get("connected"):
            return False
        if event.get("root") != project["root"]:
            raise ValueError("Event repository mismatch")
        with s.transaction() as db:
            if not add_event(s, db, event):
                return False
            rows = s.all("sessions", db, id=event["session_id"])
            session = rows[0] if rows else {"id": event["session_id"], "project_id": project["id"], "native_id": event.get("native_id", event["session_id"]),
                "root": project["root"], "created": event["ts"], "intent_version": 0,
                "last_review": None, "review_seq": 0, "summary_seq": 0, "coverage": "Hooks only; raw output and hidden reasoning excluded", "last_human_ts": 0}
            latest = event["ts"] >= session.get("last_event", 0)
            session["last_event"] = max(session.get("last_event", 0), event["ts"])
            session["last_ingested"] = now()
            if latest:
                session["last_kind"] = event["kind"]
                session["status"] = "ended" if event["kind"] == "SessionEnd" else "waiting" if event["kind"] in ("Stop", "Notification") else "active"
            if event["data"].get("gap"):
                session["gap"] = event["data"]["gap"]
            if event["kind"] in ("UserPromptSubmit", "HumanCorrection") and event["data"].get("prompt"):
                if event["ts"] >= session.get("last_human_ts", 0):
                    add_intent(s, db, session, event["id"], event["data"]["prompt"], event["ts"], event["kind"] == "HumanCorrection")
                    session["last_human_ts"] = event["ts"]
                else:
                    session["gap"] = "Late human instruction retained; not used to replace newer intent"
            if event["kind"] == "PostToolUseFailure":
                finding(s, db, session, "tool:" + event["data"].get("tool", "unknown"), "blocker", "A tool failed",
                    "Check the original session for the error before choosing a recovery action.", [event["id"]],
                    "Review the failed tool result and continue within the existing task.", origin="rule")
            if event["kind"] == "SessionEnd":
                cancel_actions(s, db, session_id=session["id"], reason="Session ended; use copy/paste to resume")
            # Subsequent activity is observable, not proof a reminder was followed.
            for a in s.all("actions", db, session_id=session["id"]):
                if a["state"] == "emitted" and event["kind"] in ("PostToolUse", "Stop") and event["ts"] > a["updated"]:
                    a.update(state="activity-observed", observed_event_id=event["id"], updated=now())
                    s.put("actions", a, db)
                    s.audit("action.activity-observed", {"id": a["id"], "event_id": event["id"], "adherence": "unknown"}, db)
            s.put("sessions", session, db)
        return True

    def drain(self):
        count = 0
        for file in sorted((self.config.home / "spool").glob("*.json"))[:200]:
            try:
                event = json.loads(file.read_text(encoding="utf-8"))
                if sum(p.stat().st_size for p in self.config.db.parent.glob("chief-of-staff.sqlite*")) > self.config.data["db_max_bytes"] and event.get("kind") not in ("UserPromptSubmit", "HumanCorrection"):
                    self.health("Storage quota reached; tool capture paused. Export or delete sessions to free space.")
                    break
                self.ingest(event)
                file.unlink()
                count += 1
            except (ValueError, KeyError, json.JSONDecodeError):
                file.rename(file.with_suffix(".rejected"))
                self.health("A malformed or disconnected event was quarantined; coverage is incomplete")
        return count

    def health(self, message):
        with self.store.transaction() as db:
            db.execute("INSERT OR REPLACE INTO meta VALUES('collector_error',?)", (dump({"message": message, "ts": now()}),))

    def correct(self, sid, prompt):
        session = self.store.get("sessions", sid)
        event = {"id": uid("e_"), "session_id": sid, "project_id": session["project_id"], "root": session["root"],
            "kind": "HumanCorrection", "ts": now(), "data": {"prompt": text(prompt)}}
        self.ingest(event)

    def summarize(self, sid):
        s = self.store
        with s.transaction() as db:
            session = s.get("sessions", sid, db)
            events = s.events(sid, session.get("summary_seq", 0), 100, db)
            if not events:
                return
            intent = intent_for(s, session, db)
            bullets = []
            for e in events:
                d = e["data"]
                detail = d.get("file") or d.get("prompt") or d.get("message") or d.get("tool") or d.get("result") or ""
                bullets.append({"event_id": e["id"], "kind": e["kind"], "text": str(detail)[:250]})
            summary = {"id": uid("sum_"), "session_id": sid, "end_seq": events[-1]["seq"], "start_seq": events[0]["seq"],
                "source_ids": [e["id"] for e in events], "intent_version": intent["version"] if intent else 0,
                "bullets": bullets, "gaps": list({e["data"]["gap"] for e in events if e["data"].get("gap")}),
                "version": "extractive-v1", "created": now()}
            s.put("summaries", summary, db)
            session["summary_seq"] = events[-1]["seq"]
            s.put("sessions", session, db)

    def continuation(self, sid):
        s = self.store
        session = s.get("sessions", sid)
        intent = intent_for(s, session)
        findings = [f for f in s.all("findings", session_id=sid) if f["status"] in ("open", "snoozed")]
        checks = [e for e in self.recent(sid, 100) if e["kind"] == "IndependentCheck"]
        return {"session_id": sid, "native_session_id": session["native_id"], "root": session["root"], "intent": intent,
            "open_decisions": findings, "checks": checks, "coverage": session["coverage"], "gap": session.get("gap"),
            "next_step": findings[0]["proposed"] if findings else "Continue the existing task; review current evidence before claiming completion.",
            "completion": "Not human accepted; checks establish only their registered scope"}

    def recent(self, sid, limit=100):
        with self.store.connect() as db:
            rows = db.execute("SELECT * FROM events WHERE session_id=? ORDER BY seq DESC LIMIT ?", (sid, limit)).fetchall()
            return [{**dict(r), "data": json.loads(r["data"])} for r in reversed(rows)]
