"""Isolated run of a model-written script (human code, not editable by the model).

python3 -I in a fresh temp dir, minimal env, hard timeout, and with isolate=True no network (unshare -rn). The JSON
payload goes in on stdin; the last non-empty stdout line must be a JSON object. Every failure raises SandboxError.
"""

import json
import os
import shutil
import subprocess
import tempfile


class SandboxError(RuntimeError):
    """The script could not be run or gave no usable answer."""


def _inside(path, allowed_dir):
    real, base = os.path.realpath(path), os.path.realpath(allowed_dir)
    return os.path.commonpath([real, base]) == base and real != base


def run_script(path, payload, timeout_s, isolate=True, allowed_dir=None):
    if allowed_dir is None or not _inside(path, allowed_dir):
        raise SandboxError("script is outside the allowed scripts dir")
    real = os.path.realpath(path)
    if not real.endswith(".py") or not os.path.isfile(real):
        raise SandboxError("script missing or not a .py file")
    cmd = ["python3", "-I", real]
    if isolate:
        unshare = shutil.which("unshare", path="/usr/bin:/bin")
        if unshare is None:
            raise SandboxError("unshare not found; refusing to run without isolation")
        cmd = [unshare, "-rn", *cmd]
    env = {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"}
    try:
        with tempfile.TemporaryDirectory(prefix="r3a2-sbx-") as cwd:
            out = subprocess.run(
                cmd,
                input=json.dumps(payload, ensure_ascii=False),
                capture_output=True,
                text=True,
                cwd=cwd,
                env=env,
                timeout=timeout_s,
            )
    except subprocess.TimeoutExpired as e:
        raise SandboxError("timeout") from e
    except OSError as e:
        raise SandboxError(f"could not start: {e}") from e
    if out.returncode != 0:
        raise SandboxError(f"exit {out.returncode}")
    lines = [ln for ln in (out.stdout or "").splitlines() if ln.strip()]
    if not lines:
        raise SandboxError("no output")
    try:
        obj = json.loads(lines[-1])
    except ValueError as e:
        raise SandboxError("last line is not JSON") from e
    if not isinstance(obj, dict):
        raise SandboxError("last line is not a JSON object")
    return obj
