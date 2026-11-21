#!/usr/bin/env python3
"""Save unchanged console output for each local validation run."""
import os
from pathlib import Path
import subprocess
import sys

from local_support import ROOT, run_id


def main():
    logs = ROOT / "logs"
    logs.mkdir(exist_ok=True)
    path = logs / (run_id("checks") + ".log")
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONUNBUFFERED"] = "1"
    commands = [[sys.executable, "run_local.py", "check"],
                [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"],
                ["node", "--test", "tests/test_app.cjs"],
                ["node", "--check", "app/app.js"],
                [sys.executable, "run_local.py", "demo"]]
    with path.open("x", encoding="utf-8") as out:
        for command in commands:
            line = "COMMAND: " + repr(command) + "\n"
            print(line, end=""); out.write(line); out.flush()
            process = subprocess.Popen(command, cwd=ROOT, env=env, stdout=subprocess.PIPE,
                                       stderr=subprocess.STDOUT, text=True, encoding="utf-8")
            for line in process.stdout:
                print(line, end=""); out.write(line); out.flush()
            code = process.wait()
            out.write(f"EXIT: {code}\n"); out.flush()
            if code:
                print("Failed run preserved:", path)
                return code
    print("All local checks passed. Log:", path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
