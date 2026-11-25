"""Run the unchanged official seller checks only, with isolated STEP 5 evidence."""

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=["claude", "codex"], required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    kit_path = root.parents[2] / "scripts/trade_test_week06.py"
    spec = importlib.util.spec_from_file_location("week06_seller_check_kit", kit_path)
    kit = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(kit)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    evidence = root / "logs/step5" / (stamp + "-" + args.mode)
    (evidence / "logs").mkdir(parents=True)
    command = [sys.executable, str(root / "seller.py"), "--mode", args.mode, "--port", "{port}"]
    cmd_text = subprocess.list2cmdline(command) if os.name == "nt" else shlex.join(command)
    # The official helper uses a shell. Launch our known command natively so its stop()
    # terminates the actual server on Windows, rather than only the cmd.exe parent.
    def start_native(cmd, cwd, out):
        port = int(cmd.rsplit(" ", 1)[1])
        actual = command[:-1] + [str(port)]
        return subprocess.Popen(actual, cwd=cwd, stdout=out, stderr=subprocess.STDOUT,
                                start_new_session=(os.name != "nt"))

    kit.start = start_native
    report = {"scope": "official seller checks only; no buyer checks", "mode": args.mode,
              "kit_sha256": hashlib.sha256(kit_path.read_bytes().replace(b"\r\n", b"\n")).hexdigest(),
              "passed": False, "results": []}
    try:
        kit.seller_tests(evidence, {"seller": {"cmd": cmd_text}})
        report["results"] = kit.RESULTS
        report["passed"] = len(kit.RESULTS) == 9 and all(row["pass"] for row in kit.RESULTS)
    except Exception as error:
        report["error"] = str(error)
        raise
    finally:
        (evidence / "protocol.log").write_text("\n".join(kit.LOG) + "\n", encoding="utf-8")
        (evidence / "verification.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"STEP 5 evidence: {evidence}", flush=True)
    if not report["passed"]:
        raise SystemExit("Some seller checks failed; see verification.json and original logs")


if __name__ == "__main__":
    main()
