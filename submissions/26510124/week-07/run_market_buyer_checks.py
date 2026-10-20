"""Run the official BUYER checks in a fresh snapshot, with real Claude calls.

This is a partial test report, not the full week-07 test-report.json.
Live market memory and existing logs are never overwritten.
"""
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shlex
import shutil
import sys
import uuid


def main():
    submission = Path(__file__).resolve().parent
    kit_path = submission.parents[2] / "scripts" / "market_test_week07.py"
    run = submission / "logs" / (
        "market-buyer-check-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        + "-" + uuid.uuid4().hex[:8])
    run.mkdir()
    (run / "logs").mkdir()
    source_hashes = {}
    for name in ("market_buyer.py", "harness.py"):
        shutil.copy2(submission / name, run / name)
        source_hashes[name] = hashlib.sha256((run / name).read_bytes()).hexdigest()
    shutil.copytree(submission / "prompts", run / "prompts")
    config = json.loads((submission / "agent.json").read_text())
    config["market_buyer"]["cmd"] = (
        f"{shlex.quote(sys.executable)} market_buyer.py --shops {{shops}} --item {{item}} --budget {{budget}}")
    (run / "agent.json").write_text(json.dumps(config, indent=2) + "\n")
    # The snapshot's harness writes its own raw attempts, not the live agent's.
    os.environ["WEEK06_LOG_DIR"] = str(run / "logs")
    spec = importlib.util.spec_from_file_location("official_market_test", kit_path)
    kit = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(kit)

    class Tee:
        def __init__(self, stream, log):
            self.stream, self.log = stream, log

        def write(self, text):
            self.stream.write(text)
            self.log.write(text)
            self.flush()

        def flush(self):
            self.stream.flush()
            self.log.flush()

    print("Buyer-only snapshot:", run, flush=True)
    original = sys.stdout
    with (run / "console.log").open("x", encoding="utf-8") as out:
        sys.stdout = Tee(original, out)
        try:
            kit.buyer_tests(run, config)
        finally:
            sys.stdout = original
            passed = bool(kit.RESULTS) and all(r["pass"] for r in kit.RESULTS)
            report = {
                "scope": "official buyer_tests only; seller tests not run",
                "kit_sha256": hashlib.sha256(kit_path.read_bytes()).hexdigest(),
                "source_sha256": source_hashes,
                "results": kit.RESULTS,
                "passed": passed,
            }
            (run / "buyer-report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(f"Buyer checks: {sum(r['pass'] for r in kit.RESULTS)}/{len(kit.RESULTS)} passed", flush=True)
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
