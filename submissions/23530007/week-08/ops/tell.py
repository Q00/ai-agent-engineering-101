"""Pass the user's instruction to a strategist and run it NOW (user 11-11: what I say must apply at once).

    .venv/bin/python ops/tell.py shop  "note text"            # replaces the shop's operator note, runs the shop strategist
    .venv/bin/python ops/tell.py buyer "note text" --append   # appends to the buyer's current note, runs the buyer strategist
    .venv/bin/python ops/tell.py shop  --run-only             # no new note, just run now

The old note goes to operator_notes_archive.jsonl. The run starts in the background at once: it waits for the role's
lock if a run is in progress (up to 15 min), then runs with force (the cost gate treats it as urgent: effort high).
Progress: logs/strategist.log ("model run (effort ...)", "answered the operator").
"""
import fcntl
import json
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
ROLES = {
    "shop": ("operator_to_shop.json", "strategist.lock", "strategist"),
    "buyer": ("operator_to_buyer.json", "buyer_strategist.lock", "buyer_strategist"),
}


def write_note(role, text, append=False):
    path = HERE / ROLES[role][0]
    old = {}
    try:
        old = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    if old.get("note"):
        with open(HERE / "operator_notes_archive.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps({"archived": time.time(), "role": role, "t": float(old.get("t") or 0), "note": old["note"]},
                               ensure_ascii=False) + "\n")
    stamp = time.strftime("%m-%d %H:%M UTC", time.gmtime())
    note = (old.get("note", "") + f" || ADDED {stamp}: " + text) if append and old.get("note") else f"From the user ({stamp}): {text}"
    path.write_text(json.dumps({"t": time.time(), "note": note}, ensure_ascii=False, indent=1), encoding="utf-8")
    return note


def run_now(role):
    """Child process: wait for the role's lock (a run may be in progress), then run the strategist with force."""
    _, lockname, module = ROLES[role]
    lock = open(HERE / ".market" / lockname, "w")
    deadline = time.time() + 900
    while True:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            break
        except OSError:
            if time.time() > deadline:
                return
            time.sleep(5)
    os.chdir(HERE)
    sys.path.insert(0, str(HERE))
    mod = __import__(module)
    mod.run(True)


def main():
    a = sys.argv[1:]
    if len(a) >= 2 and a[0] == "--child":
        run_now(a[1])
        return
    if not a or a[0] not in ROLES:
        raise SystemExit(__doc__)
    role = a[0]
    if "--run-only" not in a:
        text = next((x for x in a[1:] if not x.startswith("--")), None)
        if not text:
            raise SystemExit("no note text")
        write_note(role, text, "--append" in a)
        print(f"note for {role} written")
    log = open(HERE / "logs" / "tell.log", "a")
    subprocess.Popen([sys.executable, __file__, "--child", role], cwd=HERE, stdout=log, stderr=subprocess.STDOUT,
                     stdin=subprocess.DEVNULL, start_new_session=True)
    print(f"{role} strategist starting now in the background (waits for a run in progress); watch logs/strategist.log")


if __name__ == "__main__":
    main()
