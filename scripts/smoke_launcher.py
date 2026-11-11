"""Disposable loopback lifecycle check; no Claude/model access or external repo hooks."""
import tempfile
from pathlib import Path
from damask_chief_of_staff.cli import launch, ready, owned
from damask_chief_of_staff.config import Config
from damask_chief_of_staff.process import terminate_tree

with tempfile.TemporaryDirectory(prefix="damask-launch-") as temp:
    config=Config(Path(temp)/"state space 工程")
    running=None
    try:
        first=launch(config,False);running=first
        second=launch(config,False)
        assert first["pid"]==second["pid"] and first["url"]==second["url"]
        assert ready(config,first) and owned(config,first)
        assert terminate_tree(first["pid"],first["created"])
        assert not ready(config,first)
        running=None
        print("PASS: authenticated readiness, launch twice, verified owned-process stop")
    finally:
        if running and owned(config,running):
            terminate_tree(running["pid"],running["created"])
