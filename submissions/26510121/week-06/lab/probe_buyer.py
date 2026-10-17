"""Repeat a saved real buyer observation against the model to inspect policy.

This is a diagnostic experiment, not an A2A trade test. No offers are sent.
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import httpx

from negotiation.config import load_settings
from negotiation.contracts import BuyerContext
from negotiation.harness import decide
from negotiation.runlog import RunLog


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--observation-log", type=Path, required=True)
    args = parser.parse_args()
    source = [json.loads(line) for line in args.observation_log.read_text(encoding="utf-8").splitlines()]
    original = next(e["input"] for e in source if e["event"] == "model_request")
    context = BuyerContext(**original["trusted_context"])
    settings = load_settings()
    directory = ROOT / "logs" / "buyer" / datetime.now(timezone.utc).strftime("probe-%Y%m%dT%H%M%S.%fZ")
    directory.mkdir(parents=True, exist_ok=False)
    results = []
    for run in range(1, 4):
        log = RunLog(directory / f"model-{run}.jsonl", settings.api_key)
        with httpx.Client(timeout=15, trust_env=False) as client:
            decision = decide("buyer", context, original["observation"], settings, log, client)
        passed = decision.action == "offer" and 0 < decision.price <= context.budget
        results.append({"run": run, "opening_offer": passed, "decision": decision.model_dump()})
        print(json.dumps(results[-1], ensure_ascii=False), flush=True)
    with (directory / "probe-summary.json").open("x", encoding="utf-8") as output:
        json.dump({"kind": "model-policy-probe-not-trade-test", "source": str(args.observation_log),
                   "passed": all(r["opening_offer"] for r in results), "results": results}, output, indent=2)
    print(f"Logs: {directory}")
    return 0 if all(r["opening_offer"] for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
