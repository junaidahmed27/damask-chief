import io
import json
import subprocess
import sys
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from damask_chief_of_staff import adapters, checks, steering
from damask_chief_of_staff.api import create_app
from damask_chief_of_staff.config import Config, atomic_json
from damask_chief_of_staff.engine import Engine, intent_for, finding
from damask_chief_of_staff.hook import handle
from damask_chief_of_staff.importer import import_transcript
from damask_chief_of_staff.provider import ReviewOutput
from damask_chief_of_staff.store import Store, now, uid
from damask_chief_of_staff.supervisor import Supervisor


def emit(rig, kind="UserPromptSubmit", native="session-one", **extra):
    config,store,p,engine=rig
    out=io.StringIO()
    handle(config,p["id"],{"hook_event_name":kind,"session_id":native,"cwd":p["root"],**extra},out)
    engine.drain()
    return adapters.session_id(p["id"],native),out.getvalue()


def make_finding(rig,sid,key="drift"):
    config,store,p,engine=rig
    s=store.get("sessions",sid)
    intent=intent_for(store,s)
    constraint=intent["constraints"][0]
    with store.transaction() as db:
        return finding(store,db,s,key,"drift","An unrelated edit","The task excludes auth edits",intent["source_ids"],
            "Keep auth unchanged.",constraint_id=constraint["id"],origin="model")


def test_real_hook_subprocess_and_restart_idempotency(rig):
    config,store,p,engine=rig
    raw={"hook_event_name":"UserPromptSubmit","session_id":"native-one","cwd":p["root"],"prompt":"Fix sorting. Do not edit auth."}
    result=subprocess.run([sys.executable,"-m","damask_chief_of_staff.hook","--home",str(config.home),"--project",p["id"]],input=json.dumps(raw),text=True,capture_output=True,check=True)
    assert result.stdout==""
    file=next((config.home/"spool").glob("*.json")); saved=file.read_bytes()
    engine.drain()
    sid=adapters.session_id(p["id"],"native-one")
    engine.summarize(sid)
    file.write_bytes(saved)
    recovered=Store(Config(config.home)); Engine(recovered).drain()
    assert len(recovered.events(sid))==1
    assert recovered.all("summaries")[0]["source_ids"]==[recovered.events(sid)[0]["id"]]
    assert intent_for(recovered,recovered.get("sessions",sid))["constraints"][0]["quote"]=="Do not edit auth."


def test_sessions_intents_late_events_and_compaction_are_separate(rig):
    config,store,p,engine=rig
    sid,_=emit(rig,prompt="Fix sorting. Do not edit auth.")
    sid2,_=emit(rig,native="session-two",prompt="Improve documentation.")
    emit(rig,"PreCompact")
    engine.summarize(sid)
    assert intent_for(store,store.get("sessions",sid))["goal"].startswith("Fix sorting")
    assert intent_for(store,store.get("sessions",sid2))["goal"].startswith("Improve documentation")
    engine.ingest({"id":"late","session_id":sid,"project_id":p["id"],"root":p["root"],"kind":"UserPromptSubmit","ts":1,"data":{"prompt":"Old task"}})
    assert store.get("sessions",sid)["intent_version"]==1
    assert "Late human" in store.get("sessions",sid)["gap"]


def test_summary_failure_does_not_skip_cursor(rig,monkeypatch):
    config,store,p,engine=rig
    sid,_=emit(rig,prompt="Fix sorting")
    original=store.put
    def broken(table,obj,db):
        original(table,obj,db)
        if table=="summaries":raise RuntimeError("injected interruption")
    monkeypatch.setattr(store,"put",broken)
    with pytest.raises(RuntimeError):engine.summarize(sid)
    assert store.get("sessions",sid)["summary_seq"]==0
    assert not store.all("summaries")
    monkeypatch.setattr(store,"put",original)
    engine.summarize(sid)
    assert store.get("sessions",sid)["summary_seq"]>0


def test_capture_excludes_secrets_outputs_source_and_private_artifacts(rig):
    config,store,p,engine=rig
    sid,_=emit(rig,prompt="Fix sorting api_key=SECRET123 password=HIDDEN")
    emit(rig,"PostToolUse",tool_name="Read",tool_input={"file_path":str(Path(p["root"])/"data/private.csv")},tool_response={"stdout":"DAMASK_PRIVATE_MARKER","exit_code":0})
    emit(rig,"Stop",last_assistant_message="DAMASK_SECRET_MARKER")
    saved=json.dumps(store.events(sid))
    assert "SECRET123" not in saved and "HIDDEN" not in saved and "DAMASK_PRIVATE_MARKER" not in saved and "DAMASK_SECRET_MARKER" not in saved
    assert "[REDACTED]" in saved and "outside engineering" in saved


def test_settings_merge_disconnect_preserves_user_edits(rig,monkeypatch):
    config,store,p,engine=rig
    monkeypatch.setattr(adapters,"claude_version",lambda:"2.1.299")
    path=Path(p["root"])/".claude/settings.local.json"
    unrelated={"type":"command","command":"echo keep"}
    atomic_json(path,{"permissions":{"deny":["Bash(rm *)"]},"hooks":{"PostToolUse":[{"hooks":[unrelated]}]}})
    project=adapters.connect(store,p["root"],True)
    adapters.connect(store,p["root"],True)
    data=json.loads(path.read_text()); assert len(data["hooks"]["PostToolUse"])==2
    data["newUserSetting"]="preserve"
    atomic_json(path,data)
    adapters.disconnect(store,project["id"])
    final=json.loads(path.read_text())
    assert final["hooks"]["PostToolUse"]==[{"hooks":[unrelated]}]
    assert final["permissions"]=={"deny":["Bash(rm *)"]} and final["newUserSetting"]=="preserve"
    assert list((config.home/"backups").glob("*settings*"))


def test_assist_delivery_and_subsequent_activity_do_not_claim_adherence(rig):
    config,store,p,engine=rig
    sid,_=emit(rig,prompt="Fix sorting. Do not edit auth.")
    f=make_finding(rig,sid)
    with pytest.raises(ValueError):steering.propose(store,f["id"],auto=True)
    a=steering.propose(store,f["id"],approved_text="Keep auth unchanged.")
    assert a["state"]=="queued"
    _,output=emit(rig,"PostToolUse",tool_name="Edit",tool_input={"file_path":"app.py"})
    assert json.loads(output)["hookSpecificOutput"]["additionalContext"].endswith("Keep auth unchanged.")
    assert store.get("actions",a["id"])["state"]=="emitted"
    emit(rig,"PostToolUse",tool_name="Read",tool_input={"file_path":"app.py"})
    assert store.get("actions",a["id"])["state"]=="activity-observed"
    with pytest.raises(ValueError):steering.propose(store,f["id"])


def test_auto_fixed_authority_pause_new_intent_and_unknown_delivery(rig):
    config,store,p,engine=rig
    sid,_=emit(rig,prompt="Fix sorting. Do not edit auth.")
    f=make_finding(rig,sid)
    steering.set_policy(store,p["id"],"auto",["constraint"])
    a=steering.propose(store,f["id"],auto=True)
    assert '"Do not edit auth."' in a["message"]
    steering.set_policy(store,p["id"],"auto",["constraint"],True)
    assert store.get("actions",a["id"])["state"]=="cancelled"
    steering.set_policy(store,p["id"],"auto",["constraint"])
    a=steering.propose(store,f["id"],auto=True)
    claimed=steering.claim(store,sid,"PostToolUse")
    assert claimed["state"]=="delivery-unknown"
    assert steering.claim(store,sid,"PostToolUse") is None
    f2=make_finding(rig,sid,"another-key")
    with pytest.raises(ValueError):steering.propose(store,f2["id"],auto=True)
    emit(rig,prompt="The task changed. Only edit the docs.")
    assert store.get("sessions",sid)["intent_version"]==2
    assert store.get("findings",f["id"])["status"]=="superseded"


def test_pending_prompt_cancels_guidance_before_ingestion(rig):
    config,store,p,engine=rig
    sid,_=emit(rig,prompt="Do not edit auth.")
    f=make_finding(rig,sid)
    a=steering.propose(store,f["id"])
    handle(config,p["id"],{"hook_event_name":"UserPromptSubmit","session_id":"session-one","cwd":p["root"],"prompt":"Now edit auth."},io.StringIO())
    assert store.get("actions",a["id"])["state"]=="cancelled"
    assert store.get("sessions",sid)["intent_version"]==1  # Still queued in the durable spool.


def test_independent_check_fails_passes_then_becomes_stale(rig):
    config,store,p,engine=rig
    sid,_=emit(rig,prompt="Fix sorting")
    bad=checks.register(store,p["id"],"failure",[sys.executable,"-c","raise SystemExit(1)"])
    result=checks.run(store,sid,bad["id"])
    assert result["data"]["result"]=="failed"
    good=checks.register(store,p["id"],"syntax",[sys.executable,"-c","compile(open('app.py').read(), 'app.py', 'exec')"])
    result=checks.run(store,sid,good["id"])
    assert result["data"]["result"]=="passed"
    Path(p["root"],"app.py").write_text("answer=43\n")
    checks.reconcile(store,sid)
    assert any(f["title"]=="A passing check is now stale" for f in store.all("findings"))


def model_output(payload, drift=True, forged=False):
    human=payload["human_sources"][-1]["id"]
    evidence=payload["events"][-1]["id"]
    return ReviewOutput.model_validate({"goal":"Fix sorting without changing auth","goal_source_ids":[human],
        "summary":"A file edit was observed.","summary_source_ids":[evidence],"ambiguities":[],
        "findings":[{"key":"auth-edit","kind":"drift","title":"Auth is outside the task","explanation":"The observed file is outside the explicit constraint.","evidence_ids":["forged" if forged else evidence],
            "proposed":"Keep auth unchanged.","uncertainty":"The full diff is not captured.","constraint_id":payload["literal_constraints"][0]["id"],"check_id":None}] if drift else []})


def test_supervisor_grounded_review_dedup_and_fabricated_refs(rig):
    config,store,p,engine=rig
    sid,_=emit(rig,prompt="Fix sorting. Do not edit auth.")
    emit(rig,"PostToolUse",tool_name="Edit",tool_input={"file_path":"auth.py"})
    sup=Supervisor(store)
    call=lambda c,payload:(model_output(payload),{"input_tokens":100,"output_tokens":50})
    sup.review(sid,call);sup.review(sid,call)
    assert len(store.all("findings"))==1
    assert store.all("summaries")[-1]["basis"]=="supervisor inference"
    sup.review(sid,lambda c,payload:(model_output(payload,forged=True),{}))
    assert store.get("sessions",sid)["review_status"].startswith("review unavailable")
    assert len(store.all("findings"))==1
    sid2,_=emit(rig,native="supporting-edit",prompt="Fix sorting. Do not edit auth.")
    sup.review(sid2,lambda c,payload:(model_output(payload,drift=False),{}))
    assert not store.all("findings",session_id=sid2)


def test_intent_change_during_model_review_rejects_stale_result(rig):
    config,store,p,engine=rig
    sid,_=emit(rig,prompt="Fix sorting. Do not edit auth.")
    def call(c,payload):
        engine.correct(sid,"Now I authorize editing auth.")
        return model_output(payload),{}
    Supervisor(store).review(sid,call)
    assert not store.all("findings")
    assert store.get("sessions",sid)["intent_version"]==2


def test_api_auth_host_origin_csrf_and_model_disabled(rig):
    config,store,p,engine=rig
    with TestClient(create_app(config,8765,background=False),base_url="http://127.0.0.1:8765") as client:
        assert client.get("/api/state").status_code==401
        assert client.get("/health",headers={"Host":"evil.test"}).status_code==403
        assert client.post("/api/login",json={"token":config.token},headers={"Origin":"https://evil.test"}).status_code==403
        login=client.post("/api/login",json={"token":config.token})
        assert login.status_code==200 and "set-cookie" not in login.headers
        client.headers["x-damask-session"]=login.json()["session"]
        state=client.get("/api/state").json()
        assert state["model"]["provider"]=="disabled"
        assert client.post("/api/backup",json={}).status_code==403
        assert client.post("/api/backup",json={},headers={"X-Damask-CSRF":state["csrf"]}).status_code==200
        assert 'unsafe-inline' not in client.get("/").headers["content-security-policy"]


def test_transcript_import_is_explicit_bounded_and_idempotent(rig,tmp_path):
    config,store,p,engine=rig
    path=tmp_path/"selected.jsonl"
    records=[{"type":"user","sessionId":"past","cwd":p["root"],"timestamp":"2026-11-10T12:00:00Z","message":{"content":"Fix the docs"}},
        {"type":"assistant","sessionId":"past","message":{"content":[{"type":"thinking","thinking":"NEVER_CAPTURE"},{"type":"text","text":"Documentation updated"}]}}]
    path.write_text("\n".join(map(json.dumps,records))+"\n")
    imported=import_transcript(store,p["id"],str(path))
    import_transcript(store,p["id"],str(path))
    events=store.events(imported["session_id"])
    assert len(events)==2 and "NEVER_CAPTURE" not in json.dumps(events)
    assert store.get("sessions",imported["session_id"])["intent_version"]==1


def test_auto_emits_without_per_message_approval_and_observe_refuses(rig):
    config,store,p,engine=rig
    sid,_=emit(rig,prompt="Fix sorting. Do not edit auth.")
    f=make_finding(rig,sid)
    steering.set_policy(store,p["id"],"observe",[])
    with pytest.raises(ValueError):steering.propose(store,f["id"])
    steering.set_policy(store,p["id"],"auto",["constraint"])
    a=steering.propose(store,f["id"],auto=True)
    _,output=emit(rig,"PostToolUse",tool_name="Read",tool_input={"file_path":"app.py"})
    assert "Do not edit auth." in json.loads(output)["hookSpecificOutput"]["additionalContext"]
    assert store.get("actions",a["id"])["state"]=="emitted"
    # Dedupe also applies when a reviewer invents a new issue key for the same constraint.
    f2=make_finding(rig,sid,"different-key")
    with pytest.raises(ValueError):steering.propose(store,f2["id"],auto=True)


def test_auto_cooldown_and_budget_are_rechecked_at_delivery(rig):
    config,store,p,engine=rig
    sid,_=emit(rig,prompt="Do not edit auth. Do not edit billing. Do not edit storage. Do not edit networking.")
    steering.set_policy(store,p["id"],"auto",["constraint"])
    session=store.get("sessions",sid);intent=intent_for(store,session)
    findings=[]
    with store.transaction() as db:
        for n,c in enumerate(intent["constraints"]):
            findings.append(finding(store,db,session,str(n),"drift","Out of scope","A constraint applies",intent["source_ids"],"Reminder",constraint_id=c["id"]))
    first=steering.propose(store,findings[0]["id"],auto=True)
    with pytest.raises(ValueError,match="cooldown"):
        steering.propose(store,findings[1]["id"],auto=True)
    for index in range(1,3):
        with store.transaction() as db:
            for a in store.all("actions",db,session_id=sid):
                a.update(state="emitted",created=now()-300,updated=now()-300)
                store.put("actions",a,db)
        steering.propose(store,findings[index]["id"],auto=True)
    with pytest.raises(ValueError,match="allowance"):
        steering.propose(store,findings[3]["id"],auto=True)


def test_fresh_pass_resolves_stale_check_and_cancels_queued_reminder(rig):
    config,store,p,engine=rig
    sid,_=emit(rig,prompt="Fix sorting")
    c=checks.register(store,p["id"],"syntax",[sys.executable,"-c","compile(open('app.py').read(),'app.py','exec')"])
    checks.run(store,sid,c["id"])
    Path(p["root"],"app.py").write_text("answer=43\n")
    checks.reconcile(store,sid)
    f=next(f for f in store.all("findings") if f["issue_key"].startswith("stale:"))
    steering.set_policy(store,p["id"],"auto",["check"])
    a=steering.propose(store,f["id"],auto=True)
    checks.run(store,sid,c["id"])
    assert store.get("findings",f["id"])["status"]=="resolved"
    assert store.get("actions",a["id"])["state"]=="cancelled"


def test_messages_provider_uses_configured_route_and_validates_structure(rig,monkeypatch):
    import httpx
    from damask_chief_of_staff import provider
    config,store,p,engine=rig
    config.data["model"]={"provider":"anthropic","model":"approved-test-model","endpoint":"https://approved.invalid/v1/messages","approved":True,"key_env":"DAMASK_TEST_KEY"}
    monkeypatch.setenv("DAMASK_TEST_KEY","dummy-test-key")
    expected={"goal":"Fix spacing","goal_source_ids":["human"],"summary":"A supporting CSS edit","summary_source_ids":["edit"],"ambiguities":[],"findings":[]}
    def route(request):
        assert str(request.url)=="https://approved.invalid/v1/messages"
        assert request.headers["x-api-key"]=="dummy-test-key"
        sent=json.loads(request.content)
        assert sent["max_tokens"]==1500 and "tools" not in sent
        return httpx.Response(200,json={"content":[{"type":"text","text":json.dumps(expected)}],"usage":{"input_tokens":10,"output_tokens":20}})
    real=httpx.Client
    monkeypatch.setattr(provider.httpx,"Client",lambda **kwargs:real(transport=httpx.MockTransport(route),**kwargs))
    output,usage=provider.call(config,{"human_sources":[],"events":[]})
    assert output.goal=="Fix spacing" and usage["output_tokens"]==20


def test_model_configuration_must_pass_synthetic_probe_before_enablement(rig,monkeypatch):
    from damask_chief_of_staff import provider
    config,store,p,engine=rig
    def failed(*args):raise ValueError("Synthetic probe failed")
    monkeypatch.setattr(provider,"call",failed)
    with TestClient(create_app(config,8765,background=False),base_url="http://127.0.0.1:8765") as client:
        route={"provider":"anthropic","model":"test","endpoint":"https://approved.invalid/v1/messages","approved":True}
        response=client.post('/api/model',json=route,headers={"x-damask-token":config.token})
        assert response.status_code==400
        assert Config(config.home).data["model"]["provider"]=="disabled"


def test_restore_preserves_history_but_cannot_revive_automatic_authority(rig,tmp_path,capsys):
    from damask_chief_of_staff.cli import main
    config,store,p,engine=rig
    sid,_=emit(rig,prompt="Do not edit auth.")
    f=make_finding(rig,sid)
    steering.set_policy(store,p["id"],"auto",["constraint"])
    a=steering.propose(store,f["id"],auto=True)
    backup=tmp_path/'backup with space ü.sqlite'
    store.backup(backup)
    engine.correct(sid,"New task")
    assert main(['--home',str(config.home),'restore',str(backup),'--confirm'])==0
    assert store.get("sessions",sid)["intent_version"]==1
    assert store.get("actions",a["id"])["state"]=="cancelled"
    project=store.get("projects",p["id"])
    assert not project["connected"]
    assert steering.policy_for(store,project)["paused"]


def test_hook_emits_utf8_guidance_independent_of_console_encoding(rig):
    import os
    config,store,p,engine=rig
    sid,_=emit(rig,prompt="Fix sorting. Do not edit 认证.")
    f=make_finding(rig,sid)
    steering.propose(store,f["id"],approved_text="请保留现有认证逻辑。")
    raw={"hook_event_name":"PostToolUse","session_id":"session-one","cwd":p["root"],"tool_name":"Read","tool_input":{"file_path":"app.py"}}
    result=subprocess.run([sys.executable,"-m","damask_chief_of_staff.hook","--home",str(config.home),"--project",p["id"]],
        input=json.dumps(raw).encode(),capture_output=True,env={**os.environ,"PYTHONIOENCODING":"ascii"},check=True)
    assert "请保留现有认证逻辑。" in json.loads(result.stdout.decode("utf-8"))["hookSpecificOutput"]["additionalContext"]
