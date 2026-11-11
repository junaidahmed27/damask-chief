from __future__ import annotations
import os
import subprocess
import tempfile
import psutil


def terminate_tree(pid, expected_created=None):
    try:
        p = psutil.Process(pid)
        if expected_created is not None and abs(p.create_time() - expected_created) > 0.01:
            return False
        children = p.children(recursive=True)
        for child in reversed(children):
            try:
                child.terminate()
            except psutil.NoSuchProcess:
                pass
        p.terminate()
        _, alive = psutil.wait_procs(children + [p], timeout=3)
        for child in alive:
            try:
                child.kill()
            except psutil.NoSuchProcess:
                pass
        return True
    except psutil.NoSuchProcess:
        return False


def bounded_run(argv, cwd, env, timeout, input_text=None, output_limit=100_000, capture=True):
    import time
    with tempfile.TemporaryFile() as inp, tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
        if input_text is not None:
            inp.write(input_text.encode())
            inp.seek(0)
        p = subprocess.Popen(argv, cwd=cwd, env=env, stdin=inp,
            stdout=out if capture else subprocess.DEVNULL, stderr=err if capture else subprocess.DEVNULL, shell=False)
        deadline = time.monotonic() + timeout
        while p.poll() is None:
            oversized = capture and (os.fstat(out.fileno()).st_size > output_limit or os.fstat(err.fileno()).st_size > output_limit)
            if oversized or time.monotonic() >= deadline:
                terminate_tree(p.pid)
                p.wait()
                raise ValueError("Operation output exceeded its bound" if oversized else "Operation timed out; its owned process tree was stopped")
            time.sleep(.05)
        if capture and os.fstat(out.fileno()).st_size > output_limit:
            raise ValueError("Operation output exceeded its bound")
        out.seek(0)
        return {"returncode": p.returncode, "stdout": out.read(output_limit).decode("utf-8", "replace") if capture else ""}
