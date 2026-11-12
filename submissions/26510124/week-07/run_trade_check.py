"""Run the unchanged official trade test, preserving prior evidence first.

Usage from this directory: .venv/bin/python run_trade_check.py
The official script still creates test-report.json and its normal logs.
"""
from datetime import datetime, timezone
from pathlib import Path
import os
import shutil
import signal
import subprocess
import sys
import uuid


def main():
    submission = Path(__file__).resolve().parent
    repository = submission.parents[2]
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    previous = [submission / "test-report.json", submission / "logs/trade-test.log",
                submission / "logs/seller-under-test.log"]
    previous.extend((submission / "logs").glob("buyer-under-test*.log"))
    for path in previous:
        if path.is_file():
            target = submission / "logs" / "previous-runs" / stamp / path.relative_to(submission)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
            print("Preserved:", target.relative_to(submission), flush=True)
    console = submission / "logs" / f"official-console-{stamp}.log"
    console.parent.mkdir(exist_ok=True)
    command = [sys.executable, "-u", str(repository / "scripts/trade_test_week06.py"), str(submission)]
    with console.open("x", encoding="utf-8") as log:
        process = subprocess.Popen(command, cwd=repository, stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, text=True, bufsize=1,
                                   start_new_session=(os.name != "nt"))
        try:
            for line in process.stdout:
                print(line, end="", flush=True)
                log.write(line)
                log.flush()
            return process.wait()
        finally:
            if process.poll() is None:
                if os.name == "nt":
                    process.terminate()
                else:
                    os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    if os.name == "nt":
                        process.kill()
                    else:
                        os.killpg(process.pid, signal.SIGKILL)
                    process.wait()


if __name__ == "__main__":
    sys.exit(main())
