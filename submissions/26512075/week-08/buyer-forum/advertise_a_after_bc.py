"""One-time A-grade manifest, dispatched only after the current B/C run finishes."""
import argparse
import fcntl
import json
from pathlib import Path
import time

from advertise_bc_once import emit, run
import publisher


def ready(state, bc_log):
    with (state / "advertise-bc-once.lock").open("a") as guard:
        try:
            fcntl.flock(guard, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
    rows = publisher.read_jsonl(bc_log)
    if not rows or rows[-1].get("event") != "queueing_finished":
        return False
    return not any(r.get("as") == "shop" and r.get("shop") == "helenjeon101"
                   for r in publisher.read_jsonl(state / publisher.QUEUE))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--bc-log", type=Path, required=True)
    args = parser.parse_args()
    targets = json.loads(args.manifest.read_text())["targets"]
    with (args.state / "advertise-a-after-bc.lock").open("a") as guard:
        fcntl.flock(guard, fcntl.LOCK_EX | fcntl.LOCK_NB)
        emit("waiting_for_bc", targets=targets, manifest=str(args.manifest))
        deadline = time.monotonic() + 4 * 3600
        while not ready(args.state, args.bc_log):
            if time.monotonic() >= deadline:
                emit("stopped_bc_not_complete")
                return
            time.sleep(15)
        emit("bc_finished_starting_a")
        run(args.state, targets=targets, grades=("A",), markup_percent=200, require_json=True)


if __name__ == "__main__":
    main()
