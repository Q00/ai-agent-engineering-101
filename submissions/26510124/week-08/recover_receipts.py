"""One-time legacy hold recovery after a controlled guarded service restart.

This invalidates old quotes; it does NOT assert those quotes never settled.
The market still checks unit ownership at checkout and settlement. A late
pre-restart settlement can invalidate a newly observed inventory snapshot.
Known settling/uncertain leases are never part of this recovery.
"""
import argparse
import json
from pathlib import Path
import time

from inventory import Inventory, StockError, read_json
from receipt_leases import LeaseError, ReceiptLeaseStore, number


class SnapshotNotReady(ValueError):
    pass


def recover(root, card, stopped_at, *, apply=False, now=None):
    now = time.time() if now is None else now
    root = Path(root)
    guard = card.get("x_receipt_guard") if isinstance(card, dict) else None
    if (not isinstance(guard, dict) or guard.get("version") != 1
            or not isinstance(guard.get("runtime_id"), str) or len(guard["runtime_id"]) != 32
            or not number(guard.get("started_at")) or not number(stopped_at)
            or not 0 < stopped_at <= guard["started_at"] <= now):
        raise ValueError("Recovery requires a verified guard started after the old service stopped")
    folder = root / ".market"
    stock = Inventory(folder)
    try:
        state = stock.snapshot()
    except StockError as exc:
        raise SnapshotNotReady(str(exc)) from exc
    snapshot_at = stock.path.stat().st_mtime
    if state["round"] < 3 or not guard["started_at"] <= snapshot_at <= now + 5:
        raise SnapshotNotReady("Wait for a fresh Round 3 inventory snapshot from the new runtime")
    units = state["units"]
    if any(not isinstance(u, dict) or not isinstance(u.get("unit"), str) or not u["unit"] for u in units):
        raise ValueError("Inventory contains an invalid unit")
    owned = {u["unit"] for u in units}
    if len(owned) != len(units):
        raise ValueError("Inventory contains duplicate units")
    evidence = {"runtime_id": guard["runtime_id"], "guard_started_at": guard["started_at"],
                "old_service_stopped_at": stopped_at, "inventory_at": snapshot_at,
                "round": state["round"], "observed_at": now,
                "meaning": "legacy quotes revoked; fresh inventory ownership, not proof of non-settlement"}
    store = ReceiptLeaseStore(folder)
    # Preview is read-only; the actual candidate set is checked again under
    # the store's cross-process lock when applying the recovery.
    rows = read_json(store.path)["leases"]
    holds = read_json(store.hold_path) if store.hold_path.exists() else {}
    candidates = sum(unit in owned and task not in rows for unit, task in holds.items())
    if not apply:
        return {"applied": False, "legacy_candidates": candidates, "evidence": evidence}
    result = store.recover_legacy(owned, evidence)
    return {"applied": True, "legacy_candidates": candidates, **result, "evidence": evidence}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", type=Path, default=Path(__file__).resolve().parent)
    ap.add_argument("--card", type=Path, required=True, help="ACP response verified by the deployment installer")
    ap.add_argument("--stopped-at", type=float, required=True, help="Installer timestamp after stopping the old service")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    try:
        card = json.loads(args.card.read_text())
        deadline = time.monotonic() + 30
        while True:
            try:
                result = recover(args.root, card, args.stopped_at, apply=args.apply)
                break
            except SnapshotNotReady:
                if not args.apply or time.monotonic() >= deadline:
                    raise
                time.sleep(2)
    except (OSError, ValueError, StockError, LeaseError) as exc:
        print("Receipt recovery not confirmed: " + str(exc))
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    print("RECEIPT RECOVERY: " + str(result.get("count", 0)) + " legacy reservations invalidated and released.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
