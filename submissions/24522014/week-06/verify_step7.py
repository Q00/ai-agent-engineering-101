"""Run the official buyer checks only for one harness; no final submission report."""

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
    spec = importlib.util.spec_from_file_location("week06_buyer_check_kit", kit_path)
    kit = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(kit)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    evidence = root / "logs/step7" / (stamp + "-" + args.mode)
    (evidence / "logs").mkdir(parents=True)
    command = [sys.executable, str(root / "buyer.py"), "--mode", args.mode,
               "--url", "{url}", "--item", "{item}", "--budget", "{budget}"]
    cmd_text = subprocess.list2cmdline(command) if os.name == "nt" else shlex.join(command)

    def start_native(cmd, cwd, out):
        # Only the three controlled placeholders change; keep native process cleanup on Windows.
        parts = cmd.rsplit(" --url ", 1)[1]
        url, rest = parts.split(" --item ", 1)
        item, budget = rest.split(" --budget ", 1)
        actual = command[:5] + [url, "--item", item, "--budget", str(int(budget))]
        return subprocess.Popen(actual, cwd=cwd, stdout=out, stderr=subprocess.STDOUT,
                                start_new_session=(os.name != "nt"))

    kit.start = start_native
    report = {"scope": "official buyer checks only; no seller-under-test checks", "mode": args.mode,
              "kit_sha256": hashlib.sha256(kit_path.read_bytes().replace(b"\r\n", b"\n")).hexdigest(),
              "passed": False, "results": []}
    try:
        kit.buyer_tests(evidence, {"buyer": {"cmd": cmd_text}})
        report["results"] = kit.RESULTS
        report["passed"] = len(kit.RESULTS) == 12 and all(row["pass"] for row in kit.RESULTS)
    except Exception as error:
        report["error"] = str(error)
        raise
    finally:
        (evidence / "protocol.log").write_text("\n".join(kit.LOG) + "\n", encoding="utf-8")
        (evidence / "verification.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"STEP 7 evidence: {evidence}", flush=True)
    if not report["passed"]:
        raise SystemExit("Some buyer checks failed; see verification.json and original logs")


if __name__ == "__main__":
    main()
