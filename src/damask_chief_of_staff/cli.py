from __future__ import annotations
import argparse
import contextlib
import json
import os
import platform
import shutil
import socket
import subprocess
import sys
import time
import webbrowser
from pathlib import Path
import httpx
import psutil
from . import __version__
from .config import Config, atomic_json
from .store import Store


@contextlib.contextmanager
def lock(path):
    file = open(path, "a+b")
    try:
        file.seek(0)
        if os.name == "nt":
            import msvcrt
            if file.read(1) == b"":
                file.write(b"0")
                file.flush()
            file.seek(0)
            msvcrt.locking(file.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        file.close()


def runtime(config):
    path = config.home / "runtime.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def owned(config, r):
    try:
        process = psutil.Process(r["pid"])
        cmd = process.cmdline()
        return abs(process.create_time()-r["created"]) < .01 and "damask_chief_of_staff.cli" in cmd and "serve" in cmd and str(config.home) in cmd
    except (psutil.Error, KeyError):
        return False


def ready(config, r):
    if not r or not owned(config, r):
        return False
    try:
        with httpx.Client(timeout=1, trust_env=False) as client:
            response = client.get(r["url"]+"/api/identity", headers={"x-damask-token": config.token})
        return response.status_code == 200 and response.json().get("home") == str(config.home)
    except Exception:
        return False


def launch(config, browser=True):
    with lock(config.home/"launch.lock"):
        r = runtime(config)
        if r and owned(config, r) and not ready(config, r):
            raise ValueError("This installation has a running but unready server. Run status and inspect its log before restarting.")
        if not ready(config, r):
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", 0))
                port = sock.getsockname()[1]
            kwargs = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS} if os.name == "nt" else {"start_new_session": True}
            with open(config.home/"logs/server.log", "ab") as log:
                p = subprocess.Popen([sys.executable, "-m", "damask_chief_of_staff.cli", "--home", str(config.home), "serve", "--port", str(port)],
                    stdin=subprocess.DEVNULL, stdout=log, stderr=log, cwd=config.home, **kwargs)
            r = {"pid": p.pid, "created": psutil.Process(p.pid).create_time(), "url": f"http://127.0.0.1:{port}", "home": str(config.home), "version": __version__}
            atomic_json(config.home/"runtime.json", r)
            for _ in range(80):
                if ready(config, r):
                    break
                if p.poll() is not None:
                    raise ValueError("Server exited. Inspect the private logs/server.log file.")
                time.sleep(.15)
            else:
                raise ValueError("Server did not become ready; inspect status before retrying.")
    if browser:
        webbrowser.open(r["url"]+"/#token="+config.token)
    return r


def receipt(config):
    import importlib.metadata
    store = Store(config)
    r = runtime(config)
    data = {"version": __version__, "platform": platform.platform(), "python": sys.version.split()[0],
        "dependencies": {name: importlib.metadata.version(name) for name in ("fastapi","uvicorn","httpx","psutil","pydantic")},
        "runtime": {**(r or {}), "ready": ready(config, r)}, "state": str(config.home), "database": str(config.db),
        "projects": [{"id": p["id"], "root": p["root"], "connected": p["connected"], "hook_status": p["hook_status"]} for p in store.all("projects")],
        "sessions": len(store.all("sessions")), "model_provider": config.data["model"]["provider"],
        "model_approved": config.data["model"].get("approved", False),
        "limits": ["No hidden reasoning or raw tool output", "Hook emission is not worker adherence", "VM integration must be verified on the target runtime", "No sign-out/reboot service registration"]}
    atomic_json(config.home/"deployment-receipt.json", data)
    return data


def main(argv=None):
    parser = argparse.ArgumentParser(prog="damask")
    parser.add_argument("--home", help="Private state directory; defaults to the selected runtime's user data directory")
    sub = parser.add_subparsers(dest="command", required=True)
    start = sub.add_parser("start"); start.add_argument("--no-browser", action="store_true")
    serve = sub.add_parser("serve"); serve.add_argument("--port", type=int, required=True)
    for name in ("init", "status", "stop", "receipt", "backup", "browser-url"):
        sub.add_parser(name)
    connect = sub.add_parser("connect"); connect.add_argument("root"); connect.add_argument("--capture-text", action="store_true")
    disconnect = sub.add_parser("disconnect"); disconnect.add_argument("project_id")
    imp = sub.add_parser("import"); imp.add_argument("project_id"); imp.add_argument("path")
    restore = sub.add_parser("restore"); restore.add_argument("path"); restore.add_argument("--confirm", action="store_true")
    delete = sub.add_parser("delete-session"); delete.add_argument("session_id"); delete.add_argument("--confirm", action="store_true")
    args = parser.parse_args(argv)
    try:
        config = Config(args.home)
        store = Store(config)
        result = None
        if args.command == "serve":
            import uvicorn
            from .api import create_app
            with lock(config.home/"server.lock"):
                uvicorn.run(create_app(config, args.port), host="127.0.0.1", port=args.port, access_log=False, log_level="warning")
            return 0
        if args.command == "start":
            r = launch(config, not args.no_browser)
            result = {"url": r["url"], "pid": r["pid"], "status": "ready", "state": str(config.home)}
            receipt(config)
        elif args.command == "init":
            result = {"state": str(config.home), "database": str(config.db), "version": __version__}
        elif args.command == "status":
            r = runtime(config)
            result = {"ready": ready(config,r), "url": r["url"] if r else None, "owned_process": owned(config,r) if r else False}
        elif args.command == "stop":
            from .process import terminate_tree
            r = runtime(config)
            stopped = bool(r and owned(config,r) and terminate_tree(r["pid"],r["created"]))
            result = {"stopped": stopped, "data_preserved": True}
        elif args.command == "browser-url":
            # Only the launcher consumes this; avoid logging it. URL fragment is cleared before API calls.
            r = launch(config, False)
            print(r["url"]+"/#token="+config.token)
            return 0
        elif args.command == "connect":
            from .adapters import connect
            result = connect(store,args.root,args.capture_text)
        elif args.command == "disconnect":
            from .adapters import disconnect
            result = disconnect(store,args.project_id)
        elif args.command == "import":
            from .importer import import_transcript
            result = import_transcript(store,args.project_id,args.path)
        elif args.command == "backup":
            path = config.home / "backups" / ("chief-"+str(int(time.time()))+".sqlite")
            store.backup(path)
            result = {"backup": str(path)}
        elif args.command == "receipt":
            result = receipt(config)
        elif args.command == "restore":
            if not args.confirm:
                raise ValueError("Use --confirm to restore a selected backup after stopping the server")
            with lock(config.home/"server.lock"):
                import sqlite3
                source = Path(args.path).resolve(strict=True)
                with contextlib.closing(sqlite3.connect(source.as_uri()+"?mode=ro",uri=True)) as src:
                    if src.execute("PRAGMA integrity_check").fetchone()[0] != "ok" or src.execute("PRAGMA user_version").fetchone()[0] != 1:
                        raise ValueError("Backup is invalid or has an incompatible schema")
                    store.backup(config.home/"backups"/("before-restore-"+str(int(time.time()))+".sqlite"))
                    with contextlib.closing(store.connect()) as dst:
                        src.backup(dst)
                # Old pending guidance must never become current through restoring a backup.
                from .engine import cancel_actions
                with store.transaction() as db:
                    cancel_actions(store,db,reason="Backup restored; explicit reauthorization required")
                from .steering import set_policy
                for project in store.all("projects"):
                    set_policy(store,project["id"],"assist",[],paused=True)
                    with store.transaction() as db:
                        project = store.get("projects",project["id"],db)
                        project.update(connected=False,hook_status="Restored history; reconnect this worktree and review steering policy")
                        store.put("projects",project,db)
                result = {"restored": str(source), "queued_actions": "cancelled", "connections": "require explicit reconnect", "steering": "paused"}
        elif args.command == "delete-session":
            if not args.confirm:
                raise ValueError("Use --confirm to permanently delete this session's local retained records")
            with lock(config.home/"server.lock"):
                store.get("sessions",args.session_id)
                with store.transaction() as db:
                    for table in ("events","intents","findings","actions","summaries","reviews"):
                        db.execute(f"DELETE FROM {table} WHERE session_id=?",(args.session_id,))
                    db.execute("DELETE FROM sessions WHERE id=?",(args.session_id,))
                    # Audit payloads can contain exact guidance. Clear related action/session records.
                    rows=db.execute("SELECT seq,data FROM audits").fetchall()
                    for row in rows:
                        if args.session_id in row["data"]:
                            db.execute("DELETE FROM audits WHERE seq=?",(row["seq"],))
                for pending in (config.home/"spool").glob("*.json"):
                    try:
                        if json.loads(pending.read_text(encoding="utf-8")).get("session_id") == args.session_id:
                            pending.unlink()
                    except (ValueError,OSError):
                        pass
                with store.connect() as db:
                    db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                    db.execute("VACUUM")
                result = {"deleted": args.session_id, "note": "Backups and original Claude transcripts are separate; dispose of them explicitly if needed"}
        print(json.dumps(result,indent=2))
        return 0
    except (ValueError, OSError, RuntimeError) as exc:
        print("Damask: "+str(exc),file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
