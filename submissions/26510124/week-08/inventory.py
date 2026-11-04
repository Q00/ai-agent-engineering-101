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
    def __init__(self, detail, *, reason_code="stock_unavailable", sku=None, reservation_kind=None):
        super().__init__(detail)
        self.reason_code = reason_code
        self.sku = sku
        self.reservation_kind = reservation_kind


def requested_skus(text):
    """Market G/F + eight hex digits, plus the older numeric classroom SKU."""
    return re.findall(r"(?<!\w)(?:[gf][0-9a-f]{8}|[a-z]+\d{4,})(?!\w)", text.casefold())


def read_json(path, limit=8_000_000):
    with Path(path).open("rb") as stream:
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise StockError("State file exceeds the read limit", reason_code="inventory_unavailable")
    return json.loads(raw)


class Inventory:
    def __init__(self, folder):
        self.folder = Path(folder)
        self.path = self.folder / "inventory.json"
        self.hold_path = self.folder / "receipt-reservations.json"
        try:
            self.holds = read_json(self.hold_path) if self.hold_path.exists() else {}
        except (OSError, ValueError) as exc:
            raise StockError("Receipt reservations could not be read; do not discard them",
                             reason_code="inventory_unavailable") from exc
        if not isinstance(self.holds, dict):
            raise StockError("Invalid receipt reservations; do not discard them", reason_code="inventory_unavailable")
        self.active = {}
        self.fingerprint = None
        self.state = None

    def snapshot(self):
        try:
            stat = self.path.stat()
            if not -5 <= time.time() - stat.st_mtime <= 45:
                raise StockError("Inventory is stale; wait for the kit to refresh it", reason_code="inventory_unavailable")
            fingerprint = (stat.st_ino, stat.st_mtime_ns, stat.st_size)
            if fingerprint != self.fingerprint:
                state = read_json(self.path)
                if not isinstance(state, dict) or type(state.get("round")) is not int:
                    raise StockError("Inventory has no valid market round", reason_code="inventory_unavailable")
                if not isinstance(state.get("units"), list):
                    raise StockError("Inventory has no units list", reason_code="inventory_unavailable")
                self.state, self.fingerprint = state, fingerprint
        except (OSError, ValueError) as exc:
            raise StockError(f"Inventory unavailable: {exc}", reason_code="inventory_unavailable") from exc
        return self.state

    def available(self):
        state = self.snapshot()
        if state["round"] < 3:
            raise StockError("Round 3 inventory is not open", reason_code="inventory_unavailable")
        now = time.time()
        units = {}
        for u in state["units"]:
            if not isinstance(u, dict):
                raise StockError("Invalid inventory unit", reason_code="inventory_unavailable")
            for key in ("unit", "sku", "name", "category"):
                if not isinstance(u.get(key), str) or not u[key]:
                    raise StockError(f"Missing {key} in inventory", reason_code="inventory_unavailable")
            if any(type(u.get(k)) is not int or u[k] <= 0 for k in ("cost", "list_price")):
                raise StockError("Invalid unit cost or list price", reason_code="inventory_unavailable")
            if u["unit"] in units:
                raise StockError("Duplicate unit in inventory", reason_code="inventory_unavailable")
            units[u["unit"]] = u
        kept = {unit: task for unit, task in self.holds.items() if unit in units}
        if kept != self.holds:
            try:
                atomic_json(self.hold_path, kept)
            except OSError as exc:
                raise StockError("Could not persist receipt reservations", reason_code="inventory_unavailable") from exc
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
        requested = requested_skus(text)
        fit, held = [], []
        for key, u in units.items():
            sku = u["sku"].casefold()
            exact = re.search(r"(?<!\w)" + re.escape(sku) + r"(?!\w)", want)
            if requested:
                matches = sku in requested
            else:
                category = u["category"].split(">")[-1].strip().casefold()
                matches = exact or u["name"].casefold() in want or (category and category in want)
            if matches:
                (held if key in blocked else fit).append(u)
        if not fit:
            sku = held[0]["sku"] if held else requested[0].upper() if requested else None
            if held:
                receipt = any(u["unit"] in self.holds for u in held)
                active = any(u["unit"] in self.active.values() for u in held)
                kind = "both" if receipt and active else "receipt" if receipt else "negotiation"
                raise StockError("Matching stock is reserved; try another stocked SKU.",
                                 reason_code="stock_reserved", sku=sku, reservation_kind=kind)
            raise StockError("No matching unreserved stock", sku=sku)
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
        try:
            atomic_json(self.hold_path, holds)
        except OSError as exc:
            raise StockError("Could not persist receipt reservations", reason_code="inventory_unavailable") from exc
        self.holds = holds
        self.release(task)
        return u

    def release(self, task):
        self.active.pop(task, None)
