"""Week 06 extra credit — deploy_check.py: the kit's own seller tests, run against the deployed seller.

    python deploy_check.py https://harness-shop-25620024.<account>.workers.dev

scripts/trade_test_week06.py starts the seller from agent.json on a local port; it has no option
for a URL. This script imports the kit unchanged and replaces only how the seller is started and
found: no process is started, and the Agent Card is read from the deployed URL. The kit's
seller_tests then run as they are (kit buyer, ladders, reserve checks, the terminal-state check),
using the interface URL from the deployed card.
Writes logs/deployed/deployed-seller-test.log and logs/deployed/deployed-seller-test.json.
"""
import importlib.util
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx

HERE = Path(__file__).resolve().parent
KIT = HERE.parents[2] / "scripts" / "trade_test_week06.py"


def main():
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    deployed = sys.argv[1].rstrip("/")
    spec = importlib.util.spec_from_file_location("kit", KIT)
    kit = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(kit)

    kit.wait_card = lambda base, seconds=60: httpx.get(deployed + "/.well-known/agent-card.json", timeout=30).json()
    kit.start = lambda cmd, cwd, out: None
    kit.stop = lambda proc: None

    out = HERE / "logs" / "deployed"
    (out / "logs").mkdir(parents=True, exist_ok=True)
    started = datetime.now(timezone.utc).isoformat(timespec="seconds")
    kit.log(f"# kit seller tests against the deployed seller {deployed}, {started}")
    card = kit.seller_tests(out, {"seller": {"cmd": f"(deployed at {deployed}) {{port}}"}})
    passed = sum(r["pass"] for r in kit.RESULTS)
    kit.log(f"\n{passed}/{len(kit.RESULTS)} passed")
    (out / "deployed-seller-test.log").write_text("\n".join(kit.LOG) + "\n", encoding="utf-8")
    (out / "deployed-seller-test.json").write_text(json.dumps(
        {"deployed": deployed, "started": started, "card": card, "results": kit.RESULTS,
         "passed": passed == len(kit.RESULTS)}, indent=2, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
