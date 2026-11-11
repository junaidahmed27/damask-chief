from __future__ import annotations
from .engine import intent_for, cancel_actions, add_event
from .store import uid, now


def policy_for(store, project, db=None):
    return store.get("policies", project["policy_id"], db) if project.get("policy_id") else {"mode": "assist", "paused": False, "types": []}


def set_policy(store, project_id, mode, types, paused=False):
    if mode not in ("observe", "assist", "auto") or not set(types) <= {"constraint", "check"}:
        raise ValueError("Invalid steering policy")
    with store.transaction() as db:
        p = store.get("projects", project_id, db)
        policy = {"id": uid("p_"), "project_id": project_id, "mode": mode, "types": types,
            "paused": paused, "authorizer": "local developer", "effective": now(), "cooldown_seconds": 120, "max_per_15m": 3}
        store.put("policies", policy, db)
        p["policy_id"] = policy["id"]
        store.put("projects", p, db)
        cancel_actions(store, db, project_id=project_id, reason="Repository steering policy changed")
        store.audit("policy.enabled", policy, db)
    return policy


def eligible(store, db, action, at=None):
    at = at or now()
    s = store.get("sessions", action["session_id"], db)
    p = store.get("projects", s["project_id"], db)
    policy = policy_for(store, p, db)
    if not p.get("connected") or s["status"] == "ended":
        return "Session or repository is disconnected"
    if s.get("intent_version", 0) != action["intent_version"]:
        return "Intent changed"
    if policy["mode"] == "observe" or policy.get("paused"):
        return "Steering is paused or Observe mode is active"
    if action.get("policy_id") != p.get("policy_id"):
        return "Policy changed"
    if action.get("auto"):
        if policy["mode"] != "auto" or action["category"] not in policy["types"]:
            return "Correction type is not enabled"
        if not action.get("authority_ref") or not action.get("evidence_ids"):
            return "Missing explicit authority or evidence"
        f = store.get("findings", action["finding_id"], db)
        if f["status"] != "open" or f["intent_version"] != action["intent_version"]:
            return "Finding is no longer current"
        summaries = store.all("summaries", db, session_id=s["id"])
        current_summaries = [x for x in summaries if x.get("basis") == "supervisor inference" and x["intent_version"] == s["intent_version"]]
        if current_summaries and max(current_summaries, key=lambda x:x["created"]).get("ambiguities"):
            return "Current intent has unresolved ambiguity"
        if any(a["state"] == "delivery-unknown" for a in store.all("actions", db, session_id=s["id"]) if a["id"] != action["id"]):
            return "A previous delivery needs human review"
        for eid in action["evidence_ids"]:
            if not db.execute("SELECT 1 FROM events WHERE id=? AND session_id=?", (eid,s["id"])).fetchone():
                return "Referenced evidence is unavailable"
        others = [a for a in store.all("actions", db, session_id=s["id"]) if a["id"] != action["id"] and a.get("auto") and a["state"] not in ("cancelled", "failed")]
        if any(a.get("finding_id") == action["finding_id"] or (a.get("authority_ref") == action["authority_ref"] and a["intent_version"] == action["intent_version"]) for a in others):
            return "This issue has already received a reminder"
        if any(a["state"] == "delivery-unknown" for a in others):
            return "A previous delivery needs human review"
        recent = [a for a in others if at - a["created"] < 900]
        if len(recent) >= policy["max_per_15m"]:
            return "Automatic correction allowance exhausted"
        if any(at - a["updated"] < policy["cooldown_seconds"] for a in recent):
            return "Automatic correction cooldown"
    return None


def propose(store, finding_id, auto=False, approved_text=None):
    with store.transaction() as db:
        f = store.get("findings", finding_id, db)
        s = store.get("sessions", f["session_id"], db)
        p = store.get("projects", s["project_id"], db)
        if f["intent_version"] != s.get("intent_version") or f["status"] != "open":
            raise ValueError("Finding is no longer current")
        if any(a.get("finding_id") == finding_id and a["state"] not in ("cancelled", "failed") for a in store.all("actions", db, session_id=s["id"])):
            raise ValueError("A reminder already exists for this finding; inspect its receipt")
        intent = intent_for(store, s, db)
        message = approved_text if approved_text is not None else f.get("proposed", "")
        category, authority = "assist", "developer"
        if auto:
            constraint = next((c for c in (intent or {}).get("constraints", []) if c["id"] == f.get("constraint_id")), None)
            check = next((c for c in store.all("checks", db, project_id=p["id"]) if c["id"] == f.get("check_id") and c.get("enabled")), None)
            if constraint and f["kind"] == "drift":
                category, authority = "constraint", constraint["id"]
                message = 'Reminder of your existing instruction: "' + constraint["quote"] + '". Keep the work within that scope. If it conflicts with a newer requirement, ask the developer.'
            elif check and f["kind"] == "evidence" and f["issue_key"].startswith(("stale:", "missing:")):
                category, authority = "check", check["id"]
                message = "Completion evidence is missing or stale. The developer registered the focused check '" + check["name"] + "'. Request or run it only within your existing permissions before claiming that scope is verified."
            else:
                raise ValueError("Finding has no deterministic automatic authority")
        if not message or len(message) > 4000:
            raise ValueError("Steering text must contain 1–4000 characters")
        action = {"id": uid("a_"), "session_id": s["id"], "project_id": p["id"], "finding_id": f["id"],
            "intent_version": s["intent_version"], "message": message, "category": category, "authority_ref": authority,
            "policy_id": p.get("policy_id"), "evidence_ids": f["evidence_ids"], "auto": auto,
            "state": "queued", "created": now(), "updated": now(), "authorizer": p.get("policy_id") if auto else "local developer"}
        reason = eligible(store, db, action)
        if reason:
            raise ValueError(reason)
        store.put("actions", action, db)
        store.audit("action.authorized-and-queued", action, db)
        return action


def claim(store, sid, hook_kind):
    # Prompt submission only invalidates old guidance; deliver after the new intent is ingested.
    if hook_kind not in ("PostToolUse", "SessionStart"):
        return None
    with store.transaction() as db:
        for action in sorted(store.all("actions", db, session_id=sid), key=lambda a: a["created"]):
            if action["state"] != "queued":
                continue
            reason = eligible(store, db, action)
            if reason:
                action.update(state="cancelled", reason=reason, updated=now())
                store.put("actions", action, db)
                continue
            # Persist uncertainty BEFORE stdout. Never blindly retry after a crash.
            action.update(state="delivery-unknown", updated=now())
            store.put("actions", action, db)
            store.audit("action.delivery-attempt", {"id": action["id"], "hook": hook_kind}, db)
            return action
    return None


def emitted(store, action):
    with store.transaction() as db:
        a = store.get("actions", action["id"], db)
        a.update(state="emitted", updated=now())
        store.put("actions", a, db)
        add_event(store, db, {"id": uid("e_"), "session_id": a["session_id"], "kind": "SteeringEmitted", "ts": now(),
            "data": {"action_id": a["id"], "message": a["message"], "authority": a["authorizer"], "adherence": "unknown"}})
        store.audit("action.emitted", {"id": a["id"]}, db)
