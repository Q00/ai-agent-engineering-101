"""Local inventory and reservations. No market requests or model calls.

An issued receipt has no expiry in the kit contract. Its unit stays reserved
across seller restarts until it disappears from inventory; it is not resold
merely because a buyer is slow to pay. Unfinished negotiations expire instead.
"""
import json
import math
import re
import time
from pathlib import Path

from feedback import atomic_json


class StockError(ValueError):
    pass


def read_json(path, limit=8_000_000):
    with Path(path).open("rb") as stream:
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise StockError("State file exceeds the read limit")
    return json.loads(raw)


class Inventory:
    def __init__(self, folder):
        self.folder = Path(folder)
        self.path = self.folder / "inventory.json"
        self.hold_path = self.folder / "receipt-reservations.json"
        self.holds = read_json(self.hold_path) if self.hold_path.exists() else {}
        if not isinstance(self.holds, dict):
            raise StockError("Invalid receipt reservations; do not discard them")
        self.active = {}
        self.fingerprint = None
        self.state = None

    def snapshot(self):
        try:
            stat = self.path.stat()
            if not -5 <= time.time() - stat.st_mtime <= 45:
                raise StockError("Inventory is stale; wait for the kit to refresh it")
            fingerprint = (stat.st_ino, stat.st_mtime_ns, stat.st_size)
            if fingerprint != self.fingerprint:
                state = read_json(self.path)
                if not isinstance(state, dict) or type(state.get("round")) is not int:
                    raise StockError("Inventory has no valid market round")
                if not isinstance(state.get("units"), list):
                    raise StockError("Inventory has no units list")
                self.state, self.fingerprint = state, fingerprint
        except (OSError, ValueError) as exc:
            raise StockError(f"Inventory unavailable: {exc}") from exc
        return self.state

    def available(self):
        state = self.snapshot()
        if state["round"] < 3:
            raise StockError("Round 3 inventory is not open")
        now = time.time()
        units = {}
        for u in state["units"]:
            if not isinstance(u, dict):
                raise StockError("Invalid inventory unit")
            for key in ("unit", "sku", "name", "category"):
                if not isinstance(u.get(key), str) or not u[key]:
                    raise StockError(f"Missing {key} in inventory")
            if any(type(u.get(k)) is not int or u[k] <= 0 for k in ("cost", "list_price")):
                raise StockError("Invalid unit cost or list price")
            if u["unit"] in units:
                raise StockError("Duplicate unit in inventory")
            units[u["unit"]] = u
        kept = {unit: task for unit, task in self.holds.items() if unit in units}
        if kept != self.holds:
            atomic_json(self.hold_path, kept)
            self.holds = kept
        return {key: u for key, u in units.items() if u.get("kind") in ("food", "goods")
                and u.get("grade") in ("A", "B", "C") and
                (u["kind"] != "food" or
                 type(u.get("expires_at")) in (int, float)
                 and math.isfinite(u["expires_at"]) and u["expires_at"] > now)}

    def reserve(self, task, text):
        units = self.available()
        blocked = set(self.holds) | set(self.active.values())
        want = text.casefold()
        # An explicit SKU must never silently fall back to another category item.
        requested = re.findall(r"(?<!\w)[a-z]+\d{4,}(?!\w)", want)
        fit = []
        for key, u in units.items():
            if key in blocked:
                continue
            sku = u["sku"].casefold()
            exact = re.search(r"(?<!\w)" + re.escape(sku) + r"(?!\w)", want)
            if requested:
                matches = sku in requested
            else:
                category = u["category"].split(">")[-1].strip().casefold()
                matches = exact or u["name"].casefold() in want or (category and category in want)
            if matches:
                fit.append(u)
        if not fit:
            raise StockError("No matching unreserved stock")
        u = min(fit, key=lambda x: (x.get("expires_at") or float("inf"), x.get("supplied_at", 0), x["unit"]))
        self.active[task] = u["unit"]
        return u

    def current(self, task):
        u = self.available().get(self.active.get(task))
        if u is None:
            raise StockError("Reserved unit expired or left inventory")
        return u

    def issue(self, task):
        u = self.current(task)
        holds = {**self.holds, u["unit"]: task}
        # Persist BEFORE sending a receipt, so a restart cannot double-promise it.
        atomic_json(self.hold_path, holds)
        self.holds = holds
        self.release(task)
        return u

    def release(self, task):
        self.active.pop(task, None)
