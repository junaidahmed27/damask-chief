from __future__ import annotations
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from .config import atomic_json
from .engine import cancel_actions
from .store import uid, now

HOOKS = ("SessionStart", "SessionEnd", "UserPromptSubmit", "PostToolUse", "PostToolUseFailure", "Notification", "PreCompact", "PostCompact", "SubagentStart", "SubagentStop", "Stop")
MIN_CLAUDE = (2, 1, 299)  # This exact release was inspected against its command-hook contract.


def claude_version():
    exe = shutil.which("claude")
    if not exe:
        raise ValueError("Claude Code is not on PATH. Install it in this runtime first.")
    r = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=15)
    match = re.search(r"(\d+)\.(\d+)\.(\d+)", r.stdout)
    if r.returncode or not match or tuple(map(int, match.groups())) < MIN_CLAUDE:
        raise ValueError("Claude Code 2.1.299+ is required for this verified exec-form hook integration. Upgrade Claude, then connect again.")
    return match.group(0)


def git(root, *args):
    return subprocess.run(["git", "--no-optional-locks", "-c", "core.fsmonitor=false", "-C", str(root), *args], capture_output=True, timeout=10)


def connect(store, root, capture_text=True, install=True):
    root = Path(root).expanduser().resolve(strict=True)
    r = git(root, "rev-parse", "--show-toplevel")
    if r.returncode:
        raise ValueError("Choose the root of a local Git working tree")
    if Path(r.stdout.decode().strip()).resolve() != root:
        raise ValueError("Select the Git worktree root, not a subdirectory")
    if root == store.config.home or store.config.home.is_relative_to(root):
        raise ValueError("The private Damask state directory must be outside connected repositories")
    version = claude_version() if install else "fixture/no hooks"
    existing = store.all("projects", root=str(root))
    project = existing[0] if existing else {"id": uid("r_"), "root": str(root), "name": root.name, "created": now()}
    project.update(connected=True, capture_text=capture_text, claude_version=version, hook_coverage=list(HOOKS) if install else [],
                   hook_status="Reload Claude and verify /hooks" if install else "No hooks installed")
    if install:
        owned = list(project.get("owned_hooks", []))
        for hook in (project.get("owned_hook"), owned_hook(store, project)):
            if hook and hook not in owned:
                owned.append(hook)
        project["owned_hooks"] = owned
        project["connected"] = False
        project["hook_status"] = "Installation pending; reload Claude after successful connection"
        with store.transaction() as db:
            store.put("projects", project, db)
        install_hooks(store, project)
        project["connected"] = True
        project["hook_status"] = "Reload Claude and verify /hooks"
    with store.transaction() as db:
        store.put("projects", project, db)
        store.audit("project.connected", {"id": project["id"], "capture_text": capture_text, "hooks": install}, db)
    if not project.get("policy_id"):
        from .steering import set_policy
        set_policy(store, project["id"], "assist", [])
        project = store.get("projects", project["id"])
    return project


def owned_hook(store, project):
    return {"type": "command", "command": sys.executable,
        "args": ["-m", "damask_chief_of_staff.hook", "--home", str(store.config.home), "--project", project["id"]], "timeout": 5}


def install_hooks(store, project):
    settings = Path(project["root"]) / ".claude/settings.local.json"
    if settings.is_symlink() or settings.parent.is_symlink():
        raise ValueError("Refusing to write symlinked Claude settings")
    original = settings.read_bytes() if settings.exists() else None
    data = json.loads(original.decode("utf-8-sig")) if original is not None else {}
    if data.get("disableAllHooks") or data.get("allowManagedHooksOnly"):
        raise ValueError("Local settings disable project hooks; resolve the policy in Claude first")
    # Fail closed for readable managed files; inaccessible/remote managed policy is verified in /hooks.
    managed = [Path("/Library/Application Support/ClaudeCode/managed-settings.json"), Path("/etc/claude-code/managed-settings.json")]
    if os.name == "nt":
        managed.append(Path(os.environ.get("PROGRAMFILES", "C:/Program Files")) / "ClaudeCode/managed-settings.json")
    for file in managed:
        if file.exists():
            m = json.loads(file.read_text(encoding="utf-8-sig"))
            if m.get("disableAllHooks") or m.get("allowManagedHooksOnly"):
                raise ValueError("Managed Claude policy prohibits project hooks")
    hook = owned_hook(store, project)
    # Remove only previously recorded exact owned entries, retaining other hooks in mixed groups.
    old = project.get("owned_hooks", [])
    hooks = data.setdefault("hooks", {})
    for event in HOOKS:
        groups = hooks.setdefault(event, [])
        if not isinstance(groups, list):
            raise ValueError("Unexpected hooks schema; settings were not changed")
        for group in groups:
            if old:
                group["hooks"] = [h for h in group.get("hooks", []) if h not in old]
        groups[:] = [g for g in groups if g.get("hooks")]
        groups.append({"hooks": [hook]})
    if settings.exists():
        backup = store.config.home / "backups" / (project["id"] + "-settings-" + uid() + ".json")
        atomic_json(backup, json.loads(settings.read_text(encoding="utf-8-sig")))
    if (settings.read_bytes() if settings.exists() else None) != original:
        raise ValueError("Claude settings changed during connection; retry without overwriting the new edits")
    atomic_json(settings, data)
    project["owned_hook"] = hook
    # Project-local settings must not enter a commit. Use this worktree's private exclude file.
    ex = git(project["root"], "rev-parse", "--git-path", "info/exclude")
    if not ex.returncode:
        path = Path(ex.stdout.decode().strip())
        if not path.is_absolute():
            path = Path(project["root"]) / path
        existing = path.read_text(encoding="utf-8") if path.exists() else ""
        if ".claude/settings.local.json" not in existing:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as f:
                f.write("\n.claude/settings.local.json\n")


def disconnect(store, project_id):
    project = store.get("projects", project_id)
    path = Path(project["root"]) / ".claude/settings.local.json"
    if path.exists() and (project.get("owned_hook") or project.get("owned_hooks")):
        if path.is_symlink() or path.parent.is_symlink():
            raise ValueError("Refusing to edit symlinked settings")
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        for event, groups in list(data.get("hooks", {}).items()):
            for g in groups:
                g["hooks"] = [h for h in g.get("hooks", []) if h not in (project.get("owned_hooks") or [project.get("owned_hook")])]
            data["hooks"][event] = [g for g in groups if g.get("hooks")]
            if not data["hooks"][event]:
                del data["hooks"][event]
        atomic_json(path, data)
    with store.transaction() as db:
        project["connected"] = False
        store.put("projects", project, db)
        cancel_actions(store, db, project_id=project_id, reason="Repository disconnected")
        store.audit("project.disconnected", {"id": project_id}, db)
    return project


def session_id(project_id, native_id):
    return "s_" + hashlib.sha256((project_id + ":" + native_id).encode()).hexdigest()[:24]
