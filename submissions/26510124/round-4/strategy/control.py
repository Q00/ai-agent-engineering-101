"""Bounded, persistent operations experiments. No market or model calls here."""
from contextlib import contextmanager
from copy import deepcopy
import fcntl
import hashlib
import json
import math
from pathlib import Path
import tempfile
import time


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


class PolicyController:
    def __init__(self, root, baseline):
        self.root, self.baseline = Path(root), baseline
        self.cfg = baseline["operations"]
        self.directory = self.root / ".local"
        self.path = self.directory / "operations-policy.json"

    @contextmanager
    def locked(self):
        self.directory.mkdir(parents=True, exist_ok=True)
        with (self.directory / "operations-policy.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    def read(self):
        try:
            state = json.loads(self.path.read_text())
        except FileNotFoundError:
            return {"version": 0, "active": None, "next_model_at": 0, "last_evidence": None}
        if (not isinstance(state, dict) or type(state.get("version")) is not int
                or state["version"] < 0 or "active" not in state):
            raise ValueError("Invalid operations policy state")
        return state

    def audit(self, event, **fields):
        logs = self.root / "logs"
        logs.mkdir(exist_ok=True)
        with (logs / "operations.jsonl").open("a") as stream:
            stream.write(json.dumps({"ts": time.time(), "event": event, **fields}, ensure_ascii=False) + "\n")
            stream.flush()

    def save(self, state):
        with tempfile.NamedTemporaryFile(mode="w", dir=self.directory, delete=False) as stream:
            json.dump(state, stream, allow_nan=False)
            tmp = Path(stream.name)
        tmp.replace(self.path)

    def metrics(self, data, version, now):
        """Reader-provided settled cohort; never infer missing counters as zero."""
        if not isinstance(data, dict) or data.get("round") != 4:
            raise ValueError("Fresh Round 4 evidence required")
        observed = data.get("observed_at")
        if (type(observed) not in (int, float) or not math.isfinite(observed)
                or not 0 <= now - observed <= 900 or not str(data.get("source", "")).startswith("https://")):
            raise ValueError("Fresh sourced evidence required")
        m = data.get("operations_metrics")
        if not isinstance(m, dict) or type(m.get("policy_version")) is not int or m["policy_version"] != version:
            raise ValueError("Metrics must belong to the current policy version")
        for key in ("completed_tasks", "settlements", "refunds", "net_profit"):
            if type(m.get(key)) is not int or (key != "net_profit" and m[key] < 0):
                raise ValueError("Missing/invalid settled cohort metrics")
        if not 0 <= m["refunds"] <= m["settlements"] <= m["completed_tasks"]:
            raise ValueError("Inconsistent settled cohort counters")
        start, end = m.get("window_start"), m.get("window_end")
        if (any(type(v) not in (int, float) or not math.isfinite(v) for v in (start, end))
                or not self.cfg["evaluation_seconds"] <= end - start <= self.cfg["evaluation_seconds"] + 60
                or not 0 <= now - end <= 900):
            raise ValueError("Need a recent complete evaluation window")
        if m["completed_tasks"] < self.cfg["min_tasks"] or m["settlements"] < self.cfg["min_settlements"]:
            raise ValueError("Not enough completed tasks/settlements")
        return m

    def evidence_id(self, data):
        # Fetch timestamps alone do not justify another model call.
        return digest({k: v for k, v in data.items() if k != "observed_at"})

    def advance(self, state, data, now):
        active = state["active"]
        if not active:
            return
        reason = None
        if active["baseline_hash"] != digest(self.baseline):
            reason = "baseline_changed"
        elif now >= active["expires_at"]:
            reason = "experiment_expired"
        elif now >= active["evaluate_at"]:
            try:
                current = self.metrics(data, state["version"], now)
                if current["window_start"] < active["started_at"]:
                    raise ValueError("Evaluation includes pre-change observations")
            except ValueError:
                current = None
            if current and active.get("last_evaluation") != digest(current):
                before = active["metrics"]
                # Compare profit per completed task; larger refund share also rolls back.
                worse = (current["net_profit"] * before["completed_tasks"]
                         < before["net_profit"] * current["completed_tasks"]
                         or current["refunds"] * before["settlements"]
                         > before["refunds"] * current["settlements"])
                self.audit("evaluation", version=state["version"], metrics=current, worse=worse)
                active["last_evaluation"] = digest(current)
                self.save(state)
                if worse:
                    reason = "performance_regressed"
        if reason:
            prior = state["version"]
            state.update(version=prior + 1, active=None)
            self.save(state)
            self.audit("rollback", previous_version=prior, version=state["version"], reason=reason)

    def current(self, data=None, now=None):
        now = time.time() if now is None else now
        with self.locked():
            state = self.read()
            self.advance(state, data, now)
            return deepcopy(state)

    def effective(self, data=None, now=None):
        state = self.current(data, now)
        cfg = deepcopy(self.baseline)
        active = state["active"]
        if active:
            changes = active["changes"]
            cfg["shop"]["opening"]["adjust_bps"] = changes.get("opening_adjust_bps", {})
            if "closing_start_offer" in changes:
                cfg["shop"]["closing"]["start_offer"] = changes["closing_start_offer"]
            cfg["shop"]["restock"]["priority_skus"] = changes.get("restock_priority", [])
        return cfg, {"version": state["version"], "active": active}

    def prepare(self, data, now=None):
        """Reserve one model call only when it can produce a usable change."""
        now = time.time() if now is None else now
        with self.locked():
            state = self.read()
            self.advance(state, data, now)
            try:
                if not self.cfg.get("enabled") or self.cfg.get("mode") != "auto":
                    raise ValueError("Automatic operations disabled")
                if state["active"]:
                    raise ValueError("Current experiment is still running; evaluation uses no model")
                metrics = self.metrics(data, state["version"], now)
                evidence_id = self.evidence_id(data)
                if now < state["next_model_at"] or evidence_id == state["last_evidence"]:
                    raise ValueError("No new eligible evidence or model cooldown")
                units = data.get("units")
                if not isinstance(units, list) or not units:
                    raise ValueError("No inventory targets")
                targets = sorted({u["sku"] for u in units if isinstance(u, dict)
                                  and isinstance(u.get("sku"), str) and u.get("available") is True})
                if not targets:
                    raise ValueError("No available inventory targets")
            except (ValueError, TypeError, KeyError) as error:
                return {"ready": False, "reason": str(error), "version": state["version"]}
            state.update(next_model_at=now + self.cfg["model_interval_seconds"], last_evidence=evidence_id)
            self.save(state)
            self.audit("model_reserved", version=state["version"], evidence_id=evidence_id)
            return {"ready": True, "version": state["version"], "evidence_id": evidence_id,
                    "metrics": metrics, "targets": targets[:64],
                    "units": [{k: u.get(k) for k in ("id", "sku", "grade", "cost", "base_opening_price")}
                              for u in units[:64] if u.get("available") is True],
                    "allowed": {"opening_adjust_bps": "max 2 observed SKUs, each -200..200 bps from baseline",
                                "closing_start_offer": "integer 1..3 (baseline 2)",
                                "restock_priority": "max 2 observed SKUs with settled sales"},
                    "expires_after_seconds": self.cfg["experiment_seconds"]}

    def apply(self, proposal, data, now=None):
        now = time.time() if now is None else now
        with self.locked():
            state = self.read()
            self.advance(state, data, now)
            try:
                if not self.cfg.get("enabled") or self.cfg.get("mode") != "auto":
                    raise ValueError("Automatic operations disabled")
                if state["active"]:
                    raise ValueError("An experiment is already active")
                if (not isinstance(proposal, dict) or set(proposal) != {"base_version", "evidence_id", "changes"}
                        or type(proposal["base_version"]) is not int or proposal["base_version"] != state["version"]):
                    raise ValueError("Stale/invalid policy proposal")
                metrics = self.metrics(data, state["version"], now)
                evidence_id = self.evidence_id(data)
                if proposal["evidence_id"] != evidence_id or evidence_id != state["last_evidence"]:
                    raise ValueError("Evidence changed or was not reserved")
                reserved_at = state["next_model_at"] - self.cfg["model_interval_seconds"]
                if not 0 <= now - reserved_at <= self.cfg["proposal_ttl_seconds"]:
                    raise ValueError("Proposal expired")
                changes = proposal["changes"]
                if (not isinstance(changes, dict) or not changes or set(changes) - {
                        "opening_adjust_bps", "closing_start_offer", "restock_priority"}):
                    raise ValueError("Unapproved policy fields")
                units = {u["sku"]: u for u in data["units"] if u.get("available") is True}
                adjustments = changes.get("opening_adjust_bps", {})
                if not isinstance(adjustments, dict) or len(adjustments) > 2:
                    raise ValueError("At most two opening price targets")
                for sku, bps in adjustments.items():
                    unit = units.get(sku, {})
                    if (type(bps) is not int or not -200 <= bps <= 200
                            or type(unit.get("base_opening_price")) is not int or unit["base_opening_price"] <= 0):
                        raise ValueError("Opening price target needs verified baseline price and bounded adjustment")
                start = changes.get("closing_start_offer", 2)
                if type(start) is not int or not 1 <= start <= 3:
                    raise ValueError("Closing start must be 1..3")
                priorities = changes.get("restock_priority", [])
                if (not isinstance(priorities, list) or len(priorities) > 2
                        or any(not isinstance(s, str) for s in priorities) or len(set(priorities)) != len(priorities)):
                    raise ValueError("At most two unique restock targets")
                proven = {s["sku"] for s in data.get("sales", []) if s.get("status") == "settled"}
                if any(s not in units or s not in proven for s in priorities):
                    raise ValueError("Restock priority requires inventory and confirmed sales")
                if not any(adjustments.values()) and start == 2 and not priorities:
                    raise ValueError("Proposal makes no change")
                state["version"] += 1
                state["active"] = {"changes": deepcopy(changes), "metrics": metrics,
                                   "baseline_hash": digest(self.baseline), "evidence_id": evidence_id,
                                   "started_at": now, "evaluate_at": now + self.cfg["evaluation_seconds"],
                                   "expires_at": now + self.cfg["experiment_seconds"]}
                self.save(state)
                self.audit("applied", version=state["version"], active=state["active"])
                return {"status": "applied", "version": state["version"], "changes": changes}
            except (KeyError, TypeError, ValueError) as error:
                self.audit("rejected", version=state["version"], reason=str(error))
                return {"status": "rejected", "reason": str(error), "version": state["version"]}
