"""Capture complete offline unittest output in a new, immutable run log."""
from datetime import datetime, timezone
from pathlib import Path
import subprocess
import sys
import uuid

root = Path(__file__).resolve().parent
logdir = root / "logs"
logdir.mkdir(exist_ok=True)
path = logdir / ("offline-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
                 + "-" + uuid.uuid4().hex[:8] + ".log")
command = [sys.executable, "-m", "unittest", "discover", "-s", str(root), "-p", "test_*.py", "-v"]
with path.open("x") as out:
    out.write("OFFLINE TESTS — synthetic fixtures, no model or market trades\n")
    out.write("COMMAND: " + repr(command) + "\n")
    out.flush()
    result = subprocess.run(command, cwd=root, stdout=out, stderr=subprocess.STDOUT)
print(path.read_text())
print("Saved original output:", path)
raise SystemExit(result.returncode)
