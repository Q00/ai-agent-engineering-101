"""Frozen strategy policy. This module never chooses a price or executes a trade.

Only public observations enter the buyer encoder; seller reserve is its own
configuration. The live default is a fixed completion strategy, not learned RL.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import math
import os
from pathlib import Path
from typing import Any, Mapping


STRATEGIES = {
    "balanced": {
        "buyer": "Complete an affordable legitimate purchase promptly. Accept a genuine ask within budget; verify item and receipt. Avoid needless discount rounds.",
        "seller": "Honor the current quote promptly, never below reserve. Accept eligible actual bids immediately; use only the runtime's verified offer count.",
    },
    "patient": {
        "buyer": "Review ambiguous or suspicious evidence carefully without changing budget. Once an affordable genuine quote is established, close promptly.",
        "seller": "Clarify ambiguous buyer evidence, keep reserve private and binding, and promptly close a legitimate feasible bid. Careful verification does not mean slow bargaining.",
    },
    "direct": {
        "buyer": "Use a concise bid and close an affordable legitimate quote immediately. If needed progress to the budget ceiling; never exceed it.",
        "seller": "Use a concise competitive quote and promptly accept a feasible genuine bid, while respecting the configured reserve.",
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
