"""Conservative, bounded capture. Raw tool output and hidden reasoning are never stored."""
from __future__ import annotations
import re
from pathlib import Path

SECRET = re.compile(r'(?i)(?:sk-(?:ant-)?[\w-]{12,}|(?:AKIA|ASIA)[A-Z0-9]{16}|gh[pousr]_[\w]{20,}|Bearer\s+[\w.\-/+=]{8,}|(?:password|api[_-]?key|secret|access[_-]?token)\s*[:=]\s*["\']?[^\s"\',;]+)')
PRIVATE = re.compile(r'(?i)(?:-----BEGIN .*PRIVATE KEY-----|DAMASK_PRIVATE_MARKER|DAMASK_SECRET_MARKER)')
DENIED = {".env", ".git", ".aws", ".ssh", "secrets", "credentials", "node_modules", ".venv", "data", "datasets", "outputs", "reports"}
CODE = {".py", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", ".css", ".html", ".rs", ".go", ".java", ".cs", ".cpp", ".c", ".h", ".sh", ".ps1", ".toml", ".yaml", ".yml", ".json", ".md", ".txt", ".sql"}


def text(value, limit=4000):
    value = str(value or "")
    if PRIVATE.search(value):
        return "[content withheld: private marker or key]"
    value = SECRET.sub("[REDACTED]", value)
    value = re.sub(r"https?://[^\s/@:]+:[^\s/@]+@", "https://[REDACTED]@", value)
    return value[:limit] + ("… [capture truncated]" if len(value) > limit else "")


def relative(root, path):
    try:
        p = Path(path)
        p = (Path(root) / p).resolve() if not p.is_absolute() else p.resolve()
        rel = p.relative_to(Path(root).resolve())
        if any(x.lower() in DENIED or x.lower().startswith('.env') for x in rel.parts):
            return None
        if p.suffix.lower() not in CODE and p.name not in {"Dockerfile", "Makefile", ".gitignore"}:
            return None
        return rel.as_posix()
    except (ValueError, OSError):
        return None


def normalize(raw, project):
    kind = str(raw.get("hook_event_name", "Unknown"))[:64]
    data = {"capture": "filtered", "tool": str(raw.get("tool_name", ""))[:80]}
    if project.get("capture_text"):
        for key in ("prompt", "last_assistant_message", "message"):
            if isinstance(raw.get(key), str):
                data[key] = text(raw[key])
    elif any(k in raw for k in ("prompt", "last_assistant_message", "message")):
        data["gap"] = "Text capture disabled for this repository"
    inp = raw.get("tool_input") or {}
    if isinstance(inp, dict):
        path = inp.get("file_path") or inp.get("path")
        if isinstance(path, str):
            rel = relative(project["root"], path)
            if rel:
                data["file"] = rel
            else:
                data["gap"] = "Artifact outside engineering capture allowlist"
        # No arbitrary shell command, source contents or tool stdout in the spool.
        if data["tool"] in ("Bash", "PowerShell"):
            data["command"] = "[command contents withheld]"
    out = raw.get("tool_response")
    if isinstance(out, dict):
        for key in ("exitCode", "exit_code", "interrupted"):
            if isinstance(out.get(key), (int, bool)):
                data[key] = out[key]
    if kind == "PostToolUseFailure":
        data["error"] = "Tool failed; see original Claude session for full error"
    for key in ("source", "reason", "notification_type", "agent_type"):
        if isinstance(raw.get(key), str):
            data[key] = text(raw[key], 160)
    if raw.get("agent_id"):
        data["agent_id"] = text(raw["agent_id"], 100)
    if kind in ("PreCompact", "PostCompact"):
        data["gap"] = "Compaction observed; hidden reasoning and uncaptured transcript content are unavailable"
    return kind, data
