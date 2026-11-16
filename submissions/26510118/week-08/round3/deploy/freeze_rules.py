"""Freeze the rules for the last 12 hours of Round 3 (11/16, the user's call, "둘 다 준비해").

The integrated analysis may change a rule every 3 hours (analysts.py), and a change in the last hours can be
neither judged nor reverted before the board is read at 11/17 23:59 KST. At 11/17 12:00 KST this script sets every
field of analysis/active_rules.json that a person has not already set as a person's value
(analysts.person_set), at its value of that moment: in force as it is, kept from the integrated analysis, which
still runs and reports. A field a person set before keeps its own value and reason. The feedback loops that are
not rules (objective's margin floor, linewatch's caps, the experiments) go on as they are.

A pass of the analysis may be writing the rules: the script waits while analysis/lock is fresh (up to WAIT s).

    cd ~/market/round3 && MARKET_DIR=$PWD/.market ~/market/.venv/bin/python deploy/freeze_rules.py [--write]

Without --write it prints what it would set and changes nothing. A line goes to logs/manual_runs.log on --write.
"""
import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import analysts                                     # noqa: E402
import common                                       # noqa: E402

WAIT, LOCK_FRESH = 600, 900
WHY = "11/17 12:00 KST: rules frozen for the last 12 h of Round 3 (the user's call 11/16, \"둘 다 준비해\")"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    a = ap.parse_args()
    lock = analysts.folder() / "lock"
    t0 = time.time()
    while lock.exists() and time.time() - lock.stat().st_mtime < LOCK_FRESH and time.time() - t0 < WAIT:
        time.sleep(10)
    rules = common.read_json(analysts.folder() / "active_rules.json", {}) or {}
    held = analysts.person_rules()
    todo = [(role, field, rules[role][field]) for role, fields in analysts.FIELDS.items() for field in fields
            if field in (rules.get(role) or {}) and f"{role}.{field}" not in held]
    for role, field, value in todo:
        print(f"{'set' if a.write else 'would set'} {role}.{field} = {value}")
        if a.write:
            analysts.person_set(role, field, value, WHY)
    kept = sorted(held)
    print(f"already a person's: {kept}")
    if a.write:
        line = (f"{time.strftime('%H:%M:%S', time.gmtime())} freeze_rules: {len(todo)} rule fields set as a person's "
                f"at their values (already a person's: {len(kept)}); the integrated analysis can no longer change them\n")
        with open(Path(__file__).resolve().parent.parent / "logs" / "manual_runs.log", "a", encoding="utf-8") as f:
            f.write(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
