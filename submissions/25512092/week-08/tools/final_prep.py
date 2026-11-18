"""Midterm final step, run once after the Round 3 close (person 11/17): waits until 11/18 00:12 and the day's
automatic logs commit ("logs and reports through 11-17"), at most until 00:45, then
  1. tools/final_numbers.py: saves the paused board to reports/board_final_20261117.json, writes
     reports/baseline_1110.json, rewrites REPORT.md (3) between the FINAL markers
  2. tools/settle_extract.py -> logs/kit-settle-extract.txt
and writes what it did to logs/final_prep.out. It does not touch .gitignore, does not commit, push or open a PR.
    python tools/final_prep.py [--now]"""
import os
import subprocess
import sys
import time
from datetime import datetime

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable
OUT = os.path.join(HERE, "logs", "final_prep.out")
START = datetime(2026, 11, 18, 0, 12).timestamp()
LATEST = datetime(2026, 11, 18, 0, 45).timestamp()


def say(msg):
    with open(OUT, "a", encoding="utf-8") as f:
        f.write(f"{datetime.now():%m-%d %H:%M:%S} {msg}\n")


def auto_done():
    r = subprocess.run(["git", "log", "-1", "--format=%s", "--grep=through 11-17"], cwd=HERE, capture_output=True,
                       text=True, timeout=60)
    return bool(r.stdout.strip())


def main():
    say("started; waiting for 11/18 00:12 and the 11-17 automatic commit")
    if "--now" not in sys.argv:
        while time.time() < START:
            time.sleep(min(300, max(5, START - time.time())))
        while not auto_done() and time.time() < LATEST:
            time.sleep(60)
        say("automatic commit seen" if auto_done() else "no automatic commit by 00:45; going on")
    for args in (["tools/final_numbers.py"], ["tools/settle_extract.py"]):
        r = subprocess.run([PY, "-X", "utf8", *args], cwd=HERE, capture_output=True, text=True, timeout=1800)
        say(f"{' '.join(args)} exit {r.returncode}: {(r.stdout + r.stderr).strip()[-600:]}")
    for p in ("logs/market-kit.log", "logs/market-kit.log.1", "logs/kit-settle-extract.txt",
              "reports/board_final_20261117.json", "reports/baseline_1110.json"):
        fp = os.path.join(HERE, p)
        say(f"{p}: {os.path.getsize(fp):,} bytes" if os.path.exists(fp) else f"{p}: missing")
    say("done (no .gitignore change, no commit)")


if __name__ == "__main__":
    main()
