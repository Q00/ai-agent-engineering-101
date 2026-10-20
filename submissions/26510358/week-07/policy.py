"""Frozen strategy policy. This module never chooses a price or executes a trade.

Only public observations enter the buyer's encoder; seller reserve is its own
configuration. Candidate policies are not installed by the trainer.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
import hashlib
import json
import math
import os
from pathlib import Path
import tempfile
from typing import Any, Mapping


STRATEGIES = {
    "balanced": {
        "buyer": "Seek a good feasible deal with measured concessions. Treat the observed ask as a negotiation anchor, test a plausible discount and use the response to judge further room. Keep negotiating when a deal is plausible, then close an affordable agreement as time runs short.",
        "seller": "Seek a good feasible deal with measured, diminishing concessions. Compare a firmer counteroffer against the buyer's actual bid trend before lowering the ask. Preserve value while keeping a feasible agreement available; your item's reserve remains binding.",
    },
    "patient": {
        "buyer": "Use a patient strategy: make an ambitious plausible opening bid and very gradual concessions, testing the seller's response rather than treating its anchor as fair value. Clarify ambiguous product or price evidence. Spend available rounds seeking savings, but close a valid affordable agreement near the deadline; verification never changes the fixed budget.",
        "seller": "Use a patient strategy: preserve value with firmer counters and diminishing concessions, especially while actual buyer bids keep improving. Seek evidence of resistance before making a large cut. Clarify ambiguous requests, and close a valid feasible firm bid near the deadline; your configured reserve remains binding.",
    },
    "direct": {
        "buyer": "Use a concise direct strategy: make larger meaningful progress toward a good feasible deal with a clear bid and an appropriate discount. Your budget is an authorization ceiling, never a spending target. Promptly settle when evidence supports it; do not abandon a plausible deal merely to save a call.",
        "seller": "Use a concise direct strategy: make larger meaningful progress with a clear commercially reasonable counteroffer and promptly settle when evidence supports it. Preserve value and respect your reserve; do not reject a plausible deal merely to save a call.",
    },
}
ACTIONS = tuple(STRATEGIES)
BASELINE = {"schema_version": 1, "version": "baseline-v1", "algorithm": "fixed_baseline",
            "q_values": {}, "metadata": {"synthetic": False, "description": "Untrained balanced strategy"}}


def strategy_instruction(role: str, strategy: str) -> str:
    if role not in ("buyer", "seller") or strategy not in STRATEGIES:
        raise ValueError("Unknown role or strategy")
    return STRATEGIES[strategy][role]


def _number(value: Any) -> float:
    if isinstance(value, bool):
        return 0.0
    try:
        result = float(value)
    except (ValueError, TypeError):
        return 0.0
    return result if math.isfinite(result) else 0.0


def context_key(role: str, context: Mapping[str, Any]) -> str:
    """Coarse, reproducible state; counterpart private limits are never features."""
    if role not in ("buyer", "seller"):
        raise ValueError("role must be buyer or seller")
    turn = _number(context.get("round", 0))
    phase = "early" if turn < 2 else "middle" if turn < 5 else "late"
    if role == "buyer":
        # Intentionally does NOT inspect context['reserve'] or seller internals.
        own_limit, observed = _number(context.get("budget")), _number(context.get("ask"))
    else:
        # Seller sees its own reserve, but does NOT inspect buyer's private budget.
        own_limit, observed = _number(context.get("reserve")), _number(context.get("offer"))
    ratio = observed / own_limit if own_limit > 0 and observed > 0 else None
    band = "unknown" if ratio is None else "low" if ratio < 0.85 else "near" if ratio <= 1.05 else "high"
    flags = context.get("flags") or []
    if isinstance(flags, Mapping):
        suspect = any(bool(value) for value in flags.values())
    else:
        suspect = bool(flags)
    return f"{role}|{phase}|{band}|{'suspect' if suspect else 'clear'}"


def _validate_payload(payload: Mapping[str, Any]) -> dict[str, dict[str, float]]:
    if payload.get("schema_version") != 1 or not isinstance(payload.get("version"), str) or not payload["version"]:
        raise ValueError("Policy needs schema_version=1 and a nonempty version")
    values = payload.get("q_values")
    if not isinstance(values, dict):
        raise ValueError("q_values must be an object")
    cleaned = {}
    for key, row in values.items():
        if not isinstance(key, str) or len(key.split("|")) != 4 or key.split("|")[0] not in ("buyer", "seller"):
            raise ValueError("Invalid policy context")
        if not isinstance(row, dict) or set(row) != set(ACTIONS):
            raise ValueError("Every policy row must include all three strategies")
        if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in row.values()):
            raise ValueError("Policy values must be finite numbers")
        cleaned[key] = {action: float(row[action]) for action in ACTIONS}
    return cleaned


class StrategyPolicy:
    """Read once per process/run; edits or promotion never mutate this snapshot."""

    def __init__(self, path: str | Path | None = None):
        if path is None:
            configured = os.environ.get("A2A_POLICY_PATH")
            active = Path(__file__).resolve().parent / "policies" / "active.json"
            path = configured if configured else active if active.is_file() else None
        raw = Path(path).read_bytes() if path else json.dumps(BASELINE, sort_keys=True).encode()
        payload = json.loads(raw)
        self._q_values = _validate_payload(payload)
        self.version = payload["version"]
        self.sha256 = hashlib.sha256(raw).hexdigest()
        self._metadata = deepcopy(payload.get("metadata", {}))

    @property
    def metadata(self) -> dict[str, Any]:
        return deepcopy(self._metadata)

    def choose(self, role: str, context: Mapping[str, Any]) -> str:
        values = self._q_values.get(context_key(role, context))
        return max(ACTIONS, key=lambda action: values[action]) if values else "balanced"


def _digest(path: Path, normalize: bool = False) -> str:
    content = path.read_bytes()
    return hashlib.sha256(content.replace(b"\r\n", b"\n") if normalize else content).hexdigest()


def promote_policy(candidate_path: str | Path, target_path: str | Path,
                   validation_report_path: str | Path, trade_report_path: str | Path) -> None:
    """Fail closed until candidate-specific actual execution evidence is recorded.

    This verifies report completeness/provenance; it cannot replace honest
    execution of the referenced tests. The trainer never calls this function.
    Validation schema is documented by the CLI --help in train_policy.py.
    """
    candidate, target = Path(candidate_path).resolve(), Path(target_path).resolve()
    policy = StrategyPolicy(candidate)
    evidence = json.loads(Path(validation_report_path).read_text())
    trade_path = Path(trade_report_path).resolve()
    trade = json.loads(trade_path.read_text())
    if evidence.get("policy_sha256") != policy.sha256 or evidence.get("policy_version") != policy.version:
        raise ValueError("Validation evidence does not identify this candidate")
    holdout = evidence.get("actual_harness_holdout", {})
    if holdout.get("independent") is not True or holdout.get("mode") != "actual_harness":
        raise ValueError("Independent actual-harness holdout required; synthetic metrics cannot promote")
    for name in ("actual_harness_holdout", "guard_tests", "adversarial_tests"):
        group = evidence.get(name, {})
        cases = group.get("cases")
        if group.get("passed") is not True or not isinstance(cases, list) or not cases or any(case.get("passed") is not True for case in cases):
            raise ValueError(f"Passing {name} cases required")
    if evidence.get("official_policy_sha256") != policy.sha256 or evidence.get("official_report_sha256") != _digest(trade_path):
        raise ValueError("Official run must attest this candidate and the exact trade report")
    # Locate the real repository kit, not a caller-provided replacement validator.
    repo = next((p for p in target.parents if (p / "scripts/trade_test_week06.py").is_file()), None)
    if repo is None:
        raise ValueError("Cannot locate the current official trade test")
    cfg = json.loads((trade_path.parent / "agent.json").read_text())
    if trade.get("kit_sha256") != _digest(repo / "scripts/trade_test_week06.py", normalize=True):
        raise ValueError("Official trade test SHA does not match the current kit")
    if trade.get("seller_cmd") != cfg["seller"]["cmd"] or trade.get("buyer_cmd") != cfg["buyer"]["cmd"]:
        raise ValueError("Official trade report commands differ from agent.json")
    required = {"seller_card": 1, "seller_opens": 1, "seller_deal": 3, "seller_terminal": 1,
                "seller_holds_reserve": 3, "buyer_deal": 3, "buyer_protocol_deal": 3,
                "buyer_holds_budget": 3, "buyer_protocol_holds_budget": 3}
    results = trade.get("results", [])
    counts = Counter(result.get("test") for result in results)
    if trade.get("passed") is not True or any(result.get("pass") is not True for result in results) or any(counts[name] < count for name, count in required.items()):
        raise ValueError("All current official 21 results must pass")
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as handle:
        temporary = Path(handle.name)
        handle.write(candidate.read_bytes())
    try:
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
