"""Run official buyer checks in a new evidence directory, without changing agent.json."""

from datetime import datetime, timezone
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys


def main():
    root = Path(__file__).resolve().parent
    repo = root.parents[2]
    run = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    directory = root / "logs/lab1-step2/integration" / run
    directory.mkdir(parents=True)
    for name in ("market_buyer.py", "buyer_memory.py", "shop_catalog.py", "buyer.py", "harness.py"):
        shutil.copy2(root / name, directory / name)
    shutil.copytree(root / "prompts", directory / "prompts")
    (directory / "logs").mkdir()
    os.environ["PATH"] = str(Path(sys.executable).parent) + os.pathsep + os.environ.get("PATH", "")
    os.environ["PYTHONUTF8"] = "1"
    spec = importlib.util.spec_from_file_location("official_market_test", repo / "scripts/market_test_week07.py")
    kit = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(kit)
    config = {"market_buyer": {"cmd": f'"{sys.executable}" -X utf8 market_buyer.py --shops {{shops}} --item {{item}} --budget {{budget}}'}}
    print(f"Isolated official buyer checks: {directory}", flush=True)
    try:
        kit.buyer_tests(directory, config)
    finally:
        (directory / "official-buyer-check.log").write_text("\n".join(kit.LOG) + "\n", encoding="utf-8")
    passed = bool(kit.RESULTS) and all(row["pass"] for row in kit.RESULTS)
    report = {"scope": "Official buyer checks only, not the full market test", "passed": passed,
              "results": kit.RESULTS, "command": config["market_buyer"]["cmd"]}
    (directory / "buyer-check-report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"{sum(row['pass'] for row in kit.RESULTS)}/{len(kit.RESULTS)} buyer checks passed", flush=True)
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
