"""Durable, bounded receipt authorization shared by seller and checkout.

The checkout guard must be running before using leases. Expiry first revokes
the receipt, then frees its unit. A payment with an unknown result stays held.
Legacy holds are retained; absence of a lifecycle row never authorizes payment.
"""
from contextlib import contextmanager
from copy import deepcopy
import fcntl
import json
import math
import os
from pathlib import Path
import tempfile
import time
import uuid


class LeaseError(ValueError):
    pass


SIGNED_FIELDS = ("shop", "item", "price", "task", "sig_seller")
STATUSES = {"issued", "checkout", "settling", "uncertain", "completed", "revoked"}
EXPIRABLE = {"issued", "checkout"}


def number(value):
    return type(value) in (int, float) and math.isfinite(value)


def identifier(value):
    return isinstance(value, str) and 0 < len(value) <= 256


def durable_json(path, value):
    """Unique temporary file plus file/directory fsync; caller holds flock."""
    path = Path(path)
    temporary = None
    try:
        if path.is_symlink():
            raise LeaseError("Receipt state must not be a symbolic link")
        raw = (json.dumps(value, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=path.name + ".", suffix=".tmp", delete=False) as stream:
            temporary = stream.name
            os.chmod(temporary, 0o600)
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if temporary:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass


class ReceiptLeaseStore:
    def __init__(self, folder):
        self.folder = Path(folder)
        self.path = self.folder / "receipt-leases.json"
        self.hold_path = self.folder / "receipt-reservations.json"
        self.lock_path = self.folder / "receipt-leases.lock"

    @contextmanager
    def _lock(self):
        try:
            self.folder.mkdir(parents=True, exist_ok=True)
            flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(self.lock_path, flags, 0o600)
            with os.fdopen(descriptor, "a+") as stream:
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise LeaseError("Receipt lifecycle state could not be read or persisted") from exc

    def _json(self, path, *, missing=None):
        if path.is_symlink():
            raise LeaseError("Receipt state must not be a symbolic link")
        if not path.exists():
            if missing is not None:
                return deepcopy(missing)
            raise LeaseError("Receipt checkout guard has not initialized its state")
        with path.open("rb") as stream:
            raw = stream.read(8_000_001)
        if len(raw) > 8_000_000:
            raise LeaseError("Receipt lifecycle state exceeds its read limit")
        return json.loads(raw)

    @staticmethod
    def _receipt(task, unit, receipt):
        if (not identifier(task) or not identifier(unit) or not isinstance(receipt, dict)
                or any(not identifier(receipt.get(k)) for k in ("shop", "item", "task"))
                or not isinstance(receipt.get("sig_seller"), str) or not 0 < len(receipt["sig_seller"]) <= 512
                or type(receipt.get("price")) is not int or receipt["price"] < 0
                or receipt["task"] != task or receipt.get("unit") != unit):
            raise LeaseError("Receipt does not match its task and reserved unit")
        try:
            if len(json.dumps(receipt, allow_nan=False).encode("utf-8")) > 4096:
                raise LeaseError("Receipt is too large")
        except (TypeError, ValueError) as exc:
            raise LeaseError("Receipt is not valid bounded JSON") from exc

    def _read(self):
        state = self._json(self.path)
        holds = self._json(self.hold_path, missing={})
        if (not isinstance(state, dict) or set(state) != {"version", "leases"}
                or type(state["version"]) is not int or state["version"] != 1
                or not isinstance(state["leases"], dict)):
            raise LeaseError("Invalid receipt lifecycle state; reservations retained")
        if (not isinstance(holds, dict)
                or any(not identifier(unit) or not identifier(task) for unit, task in holds.items())):
            raise LeaseError("Invalid receipt reservations; reservations retained")
        rows = state["leases"]
        live_units = set()
        for task, row in rows.items():
            legacy = isinstance(row, dict) and row.get("reason") == "legacy_restart_recovery"
            if legacy:
                if (set(row) != {"receipt", "unit", "issued_at", "deadline", "status", "session_id", "reason"}
                        or not identifier(task) or not identifier(row.get("unit"))
                        or row.get("receipt") is not None or row.get("status") != "revoked"
                        or row.get("session_id") is not None or not number(row.get("issued_at"))
                        or row.get("deadline") != row["issued_at"]):
                    raise LeaseError("Invalid legacy receipt revocation")
                continue
            if (not isinstance(row, dict)
                    or set(row) != {"receipt", "unit", "issued_at", "deadline", "status", "session_id"}
                    or not isinstance(row.get("status"), str) or row.get("status") not in STATUSES
                    or not number(row.get("issued_at")) or not number(row.get("deadline"))
                    or row["deadline"] <= row["issued_at"]
                    or row["session_id"] is not None and not identifier(row["session_id"])):
                raise LeaseError("Invalid receipt lifecycle entry; reservations retained")
            self._receipt(task, row["unit"], row["receipt"])
            if row["status"] in {"checkout", "settling", "uncertain", "completed"} and row["session_id"] is None:
                raise LeaseError("Receipt checkout is missing its session")
            if row["status"] not in {"revoked", "completed"}:
                if row["unit"] in live_units:
                    raise LeaseError("Several receipts hold the same unit")
                live_units.add(row["unit"])
        # A crash between lifecycle and legacy-map writes is repaired under the
        # same lock. A revoked task can never regain authorization from its hold.
        repaired = dict(holds)
        for unit, task in list(repaired.items()):
            if rows.get(task, {}).get("status") == "revoked":
                repaired.pop(unit)
        for task, row in rows.items():
            if row["status"] not in {"revoked", "completed"}:
                other = repaired.get(row["unit"])
                if other is not None and other != task:
                    raise LeaseError("Receipt lifecycle conflicts with an existing reservation")
                repaired[row["unit"]] = task
        if repaired != holds:
            durable_json(self.hold_path, repaired)
        return state, repaired

    def _save(self, state, holds):
        # Authorization/revocation is the commit point. A partial write can
        # only conservatively retain a hold; _read repairs its projection.
        # Old closed receipts remain unauthorized when omitted from this
        # allowlist. Pending/unknown payments and any held unit are never pruned.
        retained = set(holds.values()) | set(self._json(self.hold_path, missing={}).values())
        terminal = [(row["issued_at"], task) for task, row in state["leases"].items()
                    if row["status"] in {"revoked", "completed"} and task not in retained]
        for _, task in sorted(terminal, reverse=True)[512:]:
            del state["leases"][task]
        durable_json(self.path, state)
        durable_json(self.hold_path, holds)

    def initialize(self):
        with self._lock():
            if not self.path.exists():
                holds = self._json(self.hold_path, missing={})
                if (not isinstance(holds, dict)
                        or any(not identifier(u) or not identifier(t) for u, t in holds.items())):
                    raise LeaseError("Invalid legacy reservations; no migration performed")
                durable_json(self.path, {"version": 1, "leases": {}})
            self._read()

    def _expire(self, state, holds):
        changed = False
        now = time.time()
        for task, row in state["leases"].items():
            if row["status"] in EXPIRABLE and now >= row["deadline"]:
                row["status"] = "revoked"
                if holds.get(row["unit"]) == task:
                    holds.pop(row["unit"])
                changed = True
        if changed:
            self._save(state, holds)

    def _authorized(self, state, holds, task, receipt=None, session_id=None):
        if not identifier(task):
            raise LeaseError("Invalid receipt task identifier")
        row = state["leases"].get(task)
        if row is None or row["status"] not in EXPIRABLE:
            raise LeaseError("Receipt is unknown, closed, or payment is already in progress")
        if time.time() >= row["deadline"]:
            raise LeaseError("Receipt checkout deadline has passed; start a new negotiation")
        if holds.get(row["unit"]) != task:
            raise LeaseError("Receipt no longer owns its inventory reservation")
        if receipt is not None and (not isinstance(receipt, dict)
                or type(receipt.get("price")) is not int
                or any(receipt.get(key) != row["receipt"].get(key) for key in SIGNED_FIELDS)):
            raise LeaseError("Receipt differs from the seller's recorded signed terms")
        if session_id is not None and row["session_id"] != session_id:
            raise LeaseError("Checkout session does not match this receipt")
        return row

    def issue(self, task, unit, receipt, ttl=300):
        self._receipt(task, unit, receipt)
        if not number(ttl) or not 1 <= ttl <= 3600:
            raise LeaseError("Receipt lifetime must be between 1 and 3600 seconds")
        with self._lock():
            state, holds = self._read()
            self._expire(state, holds)
            if task in state["leases"] or unit in holds:
                raise LeaseError("Task or unit already has a receipt reservation")
            now = time.time()
            row = {"receipt": deepcopy(receipt), "unit": unit, "issued_at": now,
                   "deadline": now + ttl, "status": "issued", "session_id": None}
            state["leases"][task] = row
            holds[unit] = task
            self._save(state, holds)
            return deepcopy(row)

    def get(self, task):
        if not identifier(task):
            raise LeaseError("Invalid receipt task identifier")
        with self._lock():
            state, holds = self._read()
            self._expire(state, holds)
            return deepcopy(state["leases"].get(task))

    def authorize(self, task, receipt=None, session_id=None):
        with self._lock():
            state, holds = self._read()
            self._expire(state, holds)
            return deepcopy(self._authorized(state, holds, task, receipt, session_id))

    def checkout(self, task, session_id):
        if not identifier(session_id):
            raise LeaseError("Invalid checkout session identifier")
        with self._lock():
            state, holds = self._read()
            self._expire(state, holds)
            row = self._authorized(state, holds, task)
            if row["status"] == "checkout" and row["session_id"] != session_id:
                raise LeaseError("Receipt already has a checkout session")
            row.update(status="checkout", session_id=session_id)
            self._save(state, holds)
            return deepcopy(row)

    def begin_settle(self, task, session_id):
        if not identifier(session_id):
            raise LeaseError("Invalid checkout session identifier")
        with self._lock():
            state, holds = self._read()
            self._expire(state, holds)
            row = self._authorized(state, holds, task, session_id=session_id)
            if row["status"] != "checkout":
                raise LeaseError("Receipt has no checkout ready for payment")
            row["status"] = "settling"
            self._save(state, holds)
            return deepcopy(row)

    def finish(self, task, status):
        permitted = {"completed": {"settling", "uncertain", "completed"},
                     "uncertain": {"settling", "uncertain"},
                     "revoked": {"issued", "checkout", "revoked"}}
        if not identifier(task) or not isinstance(status, str) or status not in permitted:
            raise LeaseError("Invalid payment lifecycle transition")
        with self._lock():
            state, holds = self._read()
            row = state["leases"].get(task)
            if row is None or row["status"] not in permitted[status]:
                raise LeaseError("Cannot release or change this payment state")
            row["status"] = status
            if status == "revoked" and holds.get(row["unit"]) == task:
                holds.pop(row["unit"])
            self._save(state, holds)
            return deepcopy(row)

    def cancel(self, task, session_id):
        if not identifier(task):
            raise LeaseError("Invalid receipt task identifier")
        with self._lock():
            state, holds = self._read()
            self._expire(state, holds)
            row = state["leases"].get(task)
            if row is not None and row["status"] == "revoked" and row["session_id"] == session_id:
                return deepcopy(row)
            row = self._authorized(state, holds, task, session_id=session_id)
            row["status"] = "revoked"
            if holds.get(row["unit"]) == task:
                holds.pop(row["unit"])
            self._save(state, holds)
            return deepcopy(row)

    def refuse(self, task, session_id):
        """Record a proven validation refusal of this one payment attempt.

        Only the checkout guard may call this after validating the market's
        structured refusal. It is never a timeout or uncertain-payment reset.
        """
        if not identifier(task) or not identifier(session_id):
            raise LeaseError("Invalid refused-payment identifiers")
        with self._lock():
            state, holds = self._read()
            row = state["leases"].get(task)
            if (row is None or row["status"] != "settling"
                    or row["session_id"] != session_id):
                raise LeaseError("Refusal does not match a currently settling payment")
            row["status"] = "revoked"
            if holds.get(row["unit"]) == task:
                holds.pop(row["unit"])
            self._save(state, holds)
            return deepcopy(row)

    def holds(self):
        with self._lock():
            state, holds = self._read()
            self._expire(state, holds)
            return dict(holds)

    def sweep(self):
        return self.holds()

    def reconcile(self, present_units):
        present = set(present_units)
        if any(not identifier(unit) for unit in present):
            raise LeaseError("Invalid authoritative inventory identifiers")
        with self._lock():
            state, holds = self._read()
            self._expire(state, holds)
            changed = False
            for unit, task in list(holds.items()):
                if unit in present:
                    continue
                row = state["leases"].get(task)
                # Missing stock is not proof of successful/failed payment.
                if row and row["status"] in {"settling", "uncertain"}:
                    continue
                if row and row["status"] in EXPIRABLE:
                    row["status"] = "revoked"
                holds.pop(unit)
                changed = True
            if changed:
                self._save(state, holds)
            return dict(holds)

    def recover_legacy(self, owned_units, evidence):
        """Explicit recovery after the caller fences the old service and verifies
        the new checkout guard and authoritative ownership. Never infers that a
        payment failed, and never changes a known lease, including uncertainty.
        """
        owned = set(owned_units)
        if any(not identifier(unit) for unit in owned):
            raise LeaseError("Invalid recovery inventory identifiers")
        if not isinstance(evidence, dict) or not evidence:
            raise LeaseError("Legacy recovery requires its external verification evidence")
        try:
            if len(json.dumps(evidence, allow_nan=False).encode("utf-8")) > 16384:
                raise LeaseError("Recovery evidence is too large")
        except (TypeError, ValueError) as exc:
            raise LeaseError("Invalid recovery evidence") from exc
        with self._lock():
            state, holds = self._read()
            candidates = {unit: task for unit, task in holds.items()
                          if unit in owned and task not in state["leases"]}
            if not candidates:
                return {"count": 0, "backup": None}
            now = time.time()
            backup = self.folder / ("legacy-recovery-" + uuid.uuid4().hex + ".json")
            document = {"created_at": now, "evidence": deepcopy(evidence), "holds": candidates}
            descriptor = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                stream.write(json.dumps(document, ensure_ascii=False, allow_nan=False) + "\n")
                stream.flush()
                os.fsync(stream.fileno())
            directory = os.open(self.folder, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
            for unit, task in candidates.items():
                state["leases"][task] = {"receipt": None, "unit": unit, "issued_at": now,
                                          "deadline": now, "status": "revoked", "session_id": None,
                                          "reason": "legacy_restart_recovery"}
                holds.pop(unit)
            # Save revocations while their holds still exist, so pruning can
            # never discard the migration fence before the hold map is durable.
            pending_holds = {**holds, **candidates}
            self._save(state, pending_holds)
            durable_json(self.hold_path, holds)
            return {"count": len(candidates), "backup": str(backup)}
