"""Repeatable local verification with immutable console logs."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parent


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--live-models", action="store_true", help="Run bounded, paid model probes after local tests")
    args = p.parse_args()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    log = ROOT / "logs" / ("verification-" + stamp + ".txt")
    log.parent.mkdir(exist_ok=True)
    command = [sys.executable, "-m", "pytest", "tests", "-q"]
    result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace")
    with log.open("x", encoding="utf-8") as f:
        f.write("Local contract and integration tests; no public market actions.\n")
        f.write(result.stdout)
        f.write(result.stderr)
    print(result.stdout, end="")
    print("Console log:", log)
    if result.returncode:
        print(result.stderr)
        return result.returncode
    if args.live_models:
        return subprocess.call([sys.executable, "model_probe.py"], cwd=ROOT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
