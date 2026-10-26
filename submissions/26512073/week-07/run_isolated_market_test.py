"""Run the unchanged course test while preserving previous evidence and live memory."""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys


BASE = Path(__file__).resolve().parent


def fingerprint(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


def main():
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ")
    run = BASE / "logs" / "test-runs" / stamp
    before = run / "before"
    before.mkdir(parents=True)
    previous = [BASE / "test-report.json", BASE / "logs" / "market-test.log",
                BASE / "logs" / "seller-under-test.log"]
    previous.extend((BASE / "logs").glob("buyer-under-test-*.log"))
    for path in previous:
        if path.exists():
            shutil.copy2(path, before / path.name)

    env = dict(os.environ)
    env.update(SELLER_DATA_DIR=str(run / "seller-data"),
               BUYER_MEMORY_PATH=str(run / "buyer-memory.jsonl"),
               PYTHONUTF8="1", PYTHONUNBUFFERED="1")
    test = BASE / "course_support" / "market_test_week07.py"
    live_paths = [BASE / "pricing_state.json", BASE / "logs" / "seller-history.jsonl",
                  BASE / "buyer_memory.jsonl"]
    live_before = {str(path.relative_to(BASE)): fingerprint(path) for path in live_paths}
    metadata = {"started": stamp, "purpose": "official market test; simulated counterparts",
                "seller_data_dir": env["SELLER_DATA_DIR"],
                "buyer_memory_path": env["BUYER_MEMORY_PATH"],
                "test_sha256": hashlib.sha256(test.read_bytes().replace(b"\r\n", b"\n")).hexdigest(),
                "agent_config": json.loads((BASE / "agent.json").read_text(encoding="utf-8")),
                "live_before": live_before}
    (run / "start.json").write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    print(f"Evidence directory: {run}", flush=True)
    with (run / "console.log").open("x", encoding="utf-8") as capture:
        process = subprocess.Popen([sys.executable, "-u", str(test), str(BASE)], cwd=BASE,
                                   env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   text=True, encoding="utf-8", errors="replace")
        for line in process.stdout:
            capture.write(line)
            capture.flush()
            print(line, end="", flush=True)
        code = process.wait()
    live_after = {str(path.relative_to(BASE)): fingerprint(path) for path in live_paths}
    unchanged = live_before == live_after
    (run / "finish.json").write_text(json.dumps({
        "exit_code": code, "live_after": live_after, "live_memory_unchanged": unchanged,
    }, indent=2) + "\n", encoding="utf-8")
    print(f"Live memory unchanged: {unchanged}", flush=True)
    return code if unchanged else 2


if __name__ == "__main__":
    sys.exit(main())
