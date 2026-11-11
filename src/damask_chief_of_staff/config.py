from __future__ import annotations
import json
import os
import secrets
import tempfile
from pathlib import Path


def home_path(override=None) -> Path:
    if override or os.environ.get("DAMASK_HOME"):
        return Path(override or os.environ["DAMASK_HOME"]).expanduser().resolve()
    if os.name == "nt":
        return Path(os.environ["LOCALAPPDATA"]) / "Damask" / "ChiefOfStaff"
    return Path.home() / ".local/share/damask/chief-of-staff"


def atomic_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, name = tempfile.mkstemp(prefix=".write-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(value, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


class Config:
    def __init__(self, home=None):
        self.home = home_path(home)
        if os.name == "nt" and str(self.home).startswith("\\\\"):
            raise ValueError("Keep the SQLite state directory on a local drive, not a network share")
        self.home.mkdir(parents=True, exist_ok=True, mode=0o700)
        if os.name == "nt" and not (self.home / "config.json").exists():
            import subprocess
            account = subprocess.run(["whoami"], capture_output=True, text=True, check=True).stdout.strip()
            result = subprocess.run(["icacls", str(self.home), "/inheritance:r", "/grant:r", account + ":(OI)(CI)F"], capture_output=True)
            if result.returncode:
                raise ValueError("Could not restrict the private state directory to this Windows account")
        for part in ("", "data", "spool", "backups", "model-work", "logs"):
            (self.home / part).mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = self.home / "config.json"
        if not self.path.exists():
            atomic_json(self.path, {"schema": 1, "token": secrets.token_urlsafe(40),
                "model": {"provider": "disabled", "model": "", "endpoint": "", "key_env": "ANTHROPIC_API_KEY", "approved": False},
                "review_debounce_seconds": 15, "reviews_per_session_hour": 12,
                "reviews_per_hour": 40, "spool_max_bytes": 50_000_000,
                "db_max_bytes": 500_000_000})
        self.reload()

    def reload(self):
        self.data = json.loads(self.path.read_text(encoding="utf-8"))
        return self.data

    def save(self):
        atomic_json(self.path, self.data)

    @property
    def db(self):
        return self.home / "data/chief-of-staff.sqlite"

    @property
    def token(self):
        return self.data["token"]
