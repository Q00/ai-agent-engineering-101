"""Run the full official market test without changing live config or memory."""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import uuid


def main():
    d = Path(__file__).resolve().parent
    name = "round2-check-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    stage = d / ".market" / name
    stage.mkdir(parents=True)
    (stage / "logs").mkdir()
    files = ["seller_agent.py", "receipt_signing.py", "buyer_agent.py", "market_buyer.py", "harness.py"]
    for filename in files:
        shutil.copy2(d / filename, stage / filename)
    shutil.copytree(d / "prompts", stage / "prompts")
    shutil.copy2(d / "agent-round2.json", stage / "agent.json")
    shutil.copy2(d / "shop_key.pem", stage / "shop_key.pem")
    (stage / "shop_key.pem").chmod(0o600)
    (stage / ".venv").symlink_to(d / ".venv", target_is_directory=True)
    test = d.parents[2] / "scripts/market_test_week07.py"
    env = os.environ.copy()
    env["WEEK06_LOG_DIR"] = str(stage / "logs")
    env["PYTHONUNBUFFERED"] = "1"
    print("Isolated Round 2 verification:", stage, flush=True)
    result = None
    try:
        with (stage / "console.log").open("x") as out:
            process = subprocess.Popen(
                [sys.executable, str(test), str(stage)], env=env,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            )
            try:
                for line in process.stdout:
                    print(line, end="", flush=True)
                    out.write(line)
                    out.flush()
                result = process.wait()
            finally:
                if process.poll() is None:
                    process.terminate()
                    process.wait()
    finally:
        # Archive exact outputs, excluding private keys and environment links.
        archive = d / "logs" / name
        shutil.copytree(stage, archive, ignore=shutil.ignore_patterns("*.pem", ".venv", "__pycache__", ".market"))
        (archive / "source-hashes.json").write_text(json.dumps({
            filename: hashlib.sha256((stage / filename).read_bytes()).hexdigest()
            for filename in files + ["agent.json"]
        }, indent=2) + "\n")
        print("Public verification archive:", archive, flush=True)
    return result if result is not None else 1


if __name__ == "__main__":
    sys.exit(main())
