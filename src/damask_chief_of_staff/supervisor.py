from __future__ import annotations
import json
import threading
from . import provider
from .engine import Engine, intent_for, finding
from .store import uid, now, dump
from .privacy import text
from .steering import propose
from . import checks


class Supervisor:
    def __init__(self, store):
        self.store = store
        self.engine = Engine(store)
        self.stop_event = threading.Event()
        self.thread = None
        self.last_reconcile = {}
        self.reviewer = None

    def start(self):
        self.thread = threading.Thread(target=self.loop, daemon=True, name="damask-chief")
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=2)

    def loop(self):
        while not self.stop_event.is_set():
            try:
                self.tick()
            except Exception as exc:
                self.engine.health("Supervisor cycle failed (" + type(exc).__name__ + "); observation will retry")
            self.stop_event.wait(1)

    def request(self, sid):
        with self.store.transaction() as db:
            s = self.store.get("sessions", sid, db)
            s["review_requested"] = True
            s["review_status"] = "queued"
            self.store.put("sessions", s, db)

    def tick(self):
        self.store.config.reload()
        self.engine.drain()
        for s in sorted(self.store.all("sessions"), key=lambda s: s.get("last_ingested", 0), reverse=True):
            p = self.store.get("projects", s["project_id"])
            if not p.get("connected"):
                continue
            self.engine.summarize(s["id"])
            if now() - self.last_reconcile.get(s["id"], 0) > 30:
                checks.reconcile(self.store, s["id"])
                self.last_reconcile[s["id"]] = now()
            events = self.engine.recent(s["id"], 1)
            last_seq = events[-1]["seq"] if events else 0
            if not s.get("intent_version"):
                continue
            if last_seq <= s.get("review_seq", 0) and not s.get("review_requested"):
                continue
            if now() - s.get("last_ingested", 0) < self.store.config.data["review_debounce_seconds"] and not s.get("review_requested"):
                continue
            if s.get("review_retry_after", 0) > now():
                continue
            if self.reviewer is None or not self.reviewer.is_alive():
                self.reviewer = threading.Thread(target=self.review, args=(s["id"],), daemon=True, name="damask-reviewer")
                self.reviewer.start()
        with self.store.transaction() as db:
            for f in self.store.all("findings", db):
                if f["status"] == "snoozed" and f.get("snoozed_until", 0) < now():
                    f["status"] = "open"
                    self.store.put("findings", f, db)
        # Auto is deterministically gated separately from model inference.
        for f in self.store.all("findings"):
            if f["status"] == "open" and (f.get("constraint_id") or (f.get("check_id") and f["kind"] == "evidence")):
                try:
                    propose(self.store, f["id"], auto=True)
                except ValueError:
                    pass

    def review(self, sid, model_call=None):
        s = self.store
        session = s.get("sessions", sid)
        intent = intent_for(s, session)
        model_config = dump(s.config.data["model"])
        if not intent:
            return
        with s.connect() as db:
            total = db.execute("SELECT COUNT(*) FROM reviews WHERE ts>?", (now()-3600,)).fetchone()[0]
            own = db.execute("SELECT COUNT(*) FROM reviews WHERE session_id=? AND ts>?", (sid, now()-3600)).fetchone()[0]
        if total >= s.config.data["reviews_per_hour"] or own >= s.config.data["reviews_per_session_hour"]:
            self.status(sid, "review budget exhausted", retry=300)
            return
        if (s.config.data["model"]["provider"] == "disabled" or not s.config.data["model"].get("verified")) and model_call is None:
            self.status(sid, "semantic review unavailable: configure an approved model route", retry=30)
            return
        # Protected instructions are fetched independently of compressed trace cursors.
        with s.connect() as db:
            human = [{"id": r["id"], "data": json.loads(r["data"]), "kind": r["kind"]} for r in db.execute(
                "SELECT * FROM events WHERE session_id=? AND kind IN ('UserPromptSubmit','HumanCorrection') ORDER BY seq DESC LIMIT 20", (sid,))]
        human.reverse()
        events = self.engine.recent(sid, 40)
        payload = {"intent_version": intent["version"], "human_sources": human,
            "literal_constraints": intent["constraints"], "events": events,
            "registered_checks": s.all("checks", project_id=session["project_id"]),
            "prior_goal_inference": intent.get("goal"), "coverage": session["coverage"],
            "gap": "Only the most recent 20 human sources and a bounded event window are included; older sources remain protected in storage",
            "snapshot": session.get("snapshot")}
        # Reduce event count first, then human-source excerpts; disclose all truncation.
        while len(dump(payload)) > 24000 and len(payload["events"]) > 3:
            payload["events"].pop(0)
        if len(dump(payload)) > 24000:
            for e in payload["human_sources"]:
                e["data"] = {"prompt": str(e["data"].get("prompt", ""))[:400] + " [bounded review excerpt]"}
        known = {e["id"] for e in payload["events"] + payload["human_sources"]}
        human_ids = {e["id"] for e in human}
        record = {"id": uid("review_"), "session_id": sid, "ts": now(), "status": "running", "prompt_version": provider.PROMPT_VERSION, "usage": {}, "cost": "unknown"}
        with s.transaction() as db:
            s.put("reviews", record, db)
        self.status(sid, "reviewing")
        try:
            output, usage = (model_call or provider.call)(s.config, payload)
            if not set(output.goal_source_ids) <= human_ids or not set(output.summary_source_ids) <= known:
                raise ValueError("Model cited unavailable goal/summary evidence")
            for f in output.findings:
                if not set(f.evidence_ids) <= known:
                    raise ValueError("Model cited unavailable finding evidence")
                if f.constraint_id and f.constraint_id not in {c["id"] for c in intent["constraints"]}:
                    raise ValueError("Model cited unavailable literal authority")
                if f.check_id and f.check_id not in {c["id"] for c in payload["registered_checks"]}:
                    raise ValueError("Model cited unavailable check")
            with s.transaction() as db:
                current = s.get("sessions", sid, db)
                s.config.reload()
                if current.get("intent_version") != intent["version"] or dump(s.config.data["model"]) != model_config:
                    raise ValueError("Intent or model authorization changed during review")
                # Store model interpretation separately; do not mutate original human source records.
                interpretation = {"id": uid("sum_"), "session_id": sid, "end_seq": events[-1]["seq"],
                    "start_seq": payload["events"][0]["seq"], "source_ids": output.summary_source_ids,
                    "intent_version": intent["version"], "version": provider.PROMPT_VERSION,
                    "summary": text(output.summary, 2500), "goal": text(output.goal, 600), "goal_source_ids": output.goal_source_ids,
                    "ambiguities": [text(x, 500) for x in output.ambiguities], "created": now(), "basis": "supervisor inference"}
                s.put("summaries", interpretation, db)
                for f in output.findings:
                    finding(s, db, current, "model:" + f.key, f.kind, text(f.title, 160), text(f.explanation, 1600), f.evidence_ids,
                        text(f.proposed, 2000), uncertainty=text(f.uncertainty, 500), constraint_id=f.constraint_id,
                        check_id=f.check_id, origin="model", review_id=record["id"])
                current.update(last_review=now(), review_seq=events[-1]["seq"], review_status="reviewed", review_requested=False, review_retry_after=0)
                s.put("sessions", current, db)
                record.update(status="complete", usage=usage)
                s.put("reviews", record, db)
        except Exception as exc:
            # Do not echo provider response bodies, keys, or private model text in error messages.
            message = str(exc) if type(exc) is ValueError and len(str(exc)) < 220 else type(exc).__name__
            self.status(sid, "review unavailable: " + text(message, 220), retry=60)
            with s.transaction() as db:
                record.update(status="failed", error=type(exc).__name__)
                s.put("reviews", record, db)

    def status(self, sid, status, retry=0):
        with self.store.transaction() as db:
            s = self.store.get("sessions", sid, db)
            s.update(review_status=status, review_requested=False, review_retry_after=now()+retry)
            self.store.put("sessions", s, db)
