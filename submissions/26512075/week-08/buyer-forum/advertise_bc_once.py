"""Advertise a fixed snapshot of B/C units once through the existing kit queue.

Leaves existing ads and queue entries intact. Generates each draft just before
queueing it, so the five-minute posting cooldown does not expire later drafts.
"""
import argparse
import fcntl
import json
import math
from pathlib import Path
import time

import publisher


def emit(event, **fields):
    print(json.dumps({"at": time.time(), "event": event, **fields}, ensure_ascii=False), flush=True)


def run(state):
    market, shop = "https://market.ourolabs.ai", "helenjeon101"
    deadline = time.monotonic() + 3 * 3600
    units = publisher.read_json(state / "inventory.json")["units"]
    targets = [u for u in units if u["grade"] in ("B", "C") and u["kind"] == "goods"]
    emit("started", targets=[{"unit": u["unit"], "sku": u["sku"], "grade": u["grade"]} for u in targets])
    for original in targets:
        while time.monotonic() < deadline:
            queue = publisher.read_jsonl(state / publisher.QUEUE)
            history = publisher.forum_delivery.resolved(state, publisher.read_jsonl(state / publisher.JOURNAL))
            own = [r for r in history if r.get("as") == "shop" and r.get("shop") == shop and r.get("market") == market]
            if any(r.get("state") in ("unknown", "inflight") for r in own):
                emit("stopped_delivery_needs_review")
                return
            active = [r for r in own if r.get("state") == "sent" and r.get("expires_at", 0) > time.time()]
            if any(r.get("unit") == original["unit"] for r in queue + active):
                emit("skipped_already_queued_or_attempted", sku=original["sku"])
                break
            # Respect every existing entry, including pending A-grade ads.
            if any(r.get("as") == "shop" and r.get("shop") == shop for r in queue):
                time.sleep(15)
                continue
            now = time.time()
            if any(r.get("next_at", 0) > now for r in own):
                time.sleep(15)
                continue
            inventory = publisher.read_json(state / "inventory.json")["units"]
            unit = next((u for u in inventory if u["unit"] == original["unit"]), None)
            if not unit or unit["grade"] not in ("B", "C"):
                emit("skipped_stock_changed", sku=original["sku"])
                break
            try:
                entry = publisher.shop_preview(state, market, shop, unit["unit"], ttl=1800)
                expected = math.ceil(unit["cost"] * 103 / 100)
                if entry["grade"] not in ("B", "C") or entry["price"] != expected or entry["warranty_hours"] != 0:
                    emit("skipped_unexpected_terms", sku=unit["sku"], grade=entry["grade"], price=entry["price"])
                    break
                emit("reviewed", id=entry["id"], sku=entry["sku"], post=entry["post"])
                queued = publisher.shop_enqueue(state, market, shop, entry["id"])
                emit("queued", id=entry["id"], sku=entry["sku"], grade=entry["grade"], price=entry["price"], added=queued)
            except (OSError, ValueError, RuntimeError) as exc:
                emit("skipped_validation", sku=unit["sku"], reason=str(exc))
            break
        else:
            emit("stopped_deadline")
            return
    emit("queueing_finished")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, required=True)
    args = parser.parse_args()
    with (args.state / "advertise-bc-once.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        run(args.state.resolve())
