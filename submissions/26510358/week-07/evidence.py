"""Deterministic transaction guards, evidence checks, and an atomic budget ledger.

These functions never choose an offer or repair a model's price. A guard either
accepts the supplied decision or reports why the harness must reconsider it.
Message warnings indicate untrusted instructions, not a finding of fraud.
"""

from __future__ import annotations

import copy
import json
import math
import os
from pathlib import Path
import re
import tempfile
import threading
from typing import Any, Iterable, Mapping


class EvidenceError(ValueError):
    """A decision or transaction violates a verifiable constraint."""


def numeric_price(value: Any) -> float:
    """Require a finite, positive JSON number; never coerce strings or booleans."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise EvidenceError("price must be a number, not a string or boolean")
    try:
        number = float(value)
    except (OverflowError, ValueError) as exc:
        raise EvidenceError("price must be finite and positive") from exc
    if not math.isfinite(number) or number <= 0:
        raise EvidenceError("price must be finite and positive")
    return number


_UNTRUSTED_PATTERNS = (
    ("review_manipulation", r"\b(?:give|rate|award)\b.{0,40}\b(?:this\s+shop|us|me)\b.{0,30}\b5\s*stars?\b"),
    ("claimed_system_authority", r"(?:^|[\n\r ])(?:system|developer)\s*:|<\|(?:system|developer)\|>|\[\s*system\s*\]"),
    ("ignore_prior_instructions", r"\b(?:ignore|disregard|forget)\b.{0,60}\b(?:instruction|rule|budget|limit)s?\b"),
    ("override_transaction_limits", r"\b(?:override|replace|change|increase|raise|reset)\b.{0,60}\b(?:budget|reserve|limit)s?\b"),
    ("asserted_budget_change", r"\b(?:your|the)\s+budget\s+(?:is\s+now|has\s+changed|must\s+be|is\s+set\s+to)\b"),
    ("korean_limit_instruction", r"(?:예산|하한가|제약|규칙|지침).{0,40}(?:무시|변경|늘려|올려|해제)|(?:무시|변경|해제).{0,40}(?:예산|하한가|제약|규칙|지침)"),
)


def inspect_untrusted(text: str) -> list[str]:
    """Return signals for instruction-like peer content without executing it."""
    if not isinstance(text, str):
        raise EvidenceError("peer message must be text")
    return [name for name, pattern in _UNTRUSTED_PATTERNS
            if re.search(pattern, text, flags=re.IGNORECASE)]


def _decision(decision: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(decision, Mapping):
        raise EvidenceError("harness decision must be an object")
    required = {"action", "price", "item_id", "offer", "reason"}
    missing = required.difference(decision)
    if missing:
        raise EvidenceError("decision is missing fields: " + ", ".join(sorted(missing)))
    if not isinstance(decision["action"], str):
        raise EvidenceError("action must be text")
    if not isinstance(decision["reason"], str):
        raise EvidenceError("reason must be text")
    item_id = decision["item_id"]
    if item_id is not None and (not isinstance(item_id, str) or not item_id.strip()):
        raise EvidenceError("item_id must be nonempty text or null")
    for key in ("price", "offer"):
        if decision[key] is not None:
            numeric_price(decision[key])
    return dict(decision)


def validate_seller_decision(
    decision: Mapping[str, Any], *, reserve: float, offer: float | None = None,
    opening: bool = False, allowed_item_ids: Iterable[str] | None = None,
    max_ask: float | None = None, last_ask: float | None = None,
    accept_floor_bids: bool = False, hold_quote: bool = False, exact_quote: bool = False,
) -> dict[str, Any]:
    """Validate a seller decision, preserving its action and exact supplied price.

    max_ask/last_ask cap quotes and accept_floor_bids requires accepting any
    follow-up bid at or above the floor; none of them ever repair a price.
    """
    result = _decision(decision)
    floor = numeric_price(reserve)
    incoming = numeric_price(offer) if offer is not None else None
    action, price = result["action"], result["price"]
    if action not in {"open", "counter", "accept", "reject"}:
        raise EvidenceError("seller action must be open, counter, accept, or reject")
    if opening and action != "open":
        raise EvidenceError("the first seller response must open a quote")
    if not opening and action == "open":
        raise EvidenceError("an existing task cannot be opened again")
    if opening and result["item_id"] is None:
        raise EvidenceError("opening a quote requires a selected item_id")
    if allowed_item_ids is not None and result["item_id"] not in set(allowed_item_ids):
        raise EvidenceError("selected item_id is not one of the supplied candidates")
    if action == "reject":
        if price is not None:
            raise EvidenceError("reject must have a null price")
    else:
        if price is None:
            raise EvidenceError("quote or acceptance requires a numeric price")
        if numeric_price(price) < floor:
            raise EvidenceError("seller price is below the item's reserve")
    if action in {"open", "counter"}:
        if max_ask is not None and numeric_price(price) > numeric_price(max_ask):
            raise EvidenceError(f"quote must not exceed the configured max_opening_ask {max_ask}")
        if action == "counter" and last_ask is not None and numeric_price(price) > numeric_price(last_ask):
            raise EvidenceError("a counter must not raise the previous ask")
        if opening and accept_floor_bids and incoming is not None and incoming >= floor \
                and numeric_price(price) > incoming:
            raise EvidenceError("open at the buyer's feasible bid (or lower) so it can be accepted next")
    if accept_floor_bids and not opening and incoming is not None:
        if incoming >= floor and action != "accept":
            raise EvidenceError("a follow-up bid at or above the floor must be accepted at exactly that bid")
        if incoming < floor and action == "counter" and numeric_price(price) != floor:
            raise EvidenceError("counter a below-floor bid once, at exactly the reserve")
    if not opening and incoming is not None and last_ask is not None \
            and (incoming == numeric_price(last_ask) or incoming > numeric_price(last_ask) and not exact_quote) \
            and action != "accept":
        raise EvidenceError("a bid meeting the existing quote must be accepted")
    if exact_quote and not opening and last_ask is not None:
        if action == "accept" and numeric_price(price) != numeric_price(last_ask):
            raise EvidenceError("the referral receipt must use the exact discounted quote")
        if incoming is not None and incoming > numeric_price(last_ask) and action != "counter":
            raise EvidenceError("request the exact discounted amount instead of accepting an overbid")
    if hold_quote and not opening and last_ask is not None:
        if action == "counter" and numeric_price(price) != numeric_price(last_ask):
            raise EvidenceError("recovery keeps this task's existing quote")
        if action == "accept" and incoming is not None and incoming < numeric_price(last_ask):
            raise EvidenceError("recovery cannot accept below this task's existing quote")
    if action == "accept":
        if incoming is None:
            raise EvidenceError("acceptance requires evidence of an incoming offer")
        if numeric_price(price) != incoming:
            raise EvidenceError("acceptance price differs from the incoming offer")
        if result["offer"] is not None and numeric_price(result["offer"]) != incoming:
            raise EvidenceError("decision offer differs from the incoming offer")
    return result


def validate_buyer_decision(
    decision: Mapping[str, Any], *, budget: float, ask: float | None = None,
) -> dict[str, Any]:
    """Validate a buyer decision without deriving or clamping any offer."""
    result = _decision(decision)
    if "stance" in result and result["stance"] is not None:
        if not isinstance(result["stance"], str) or result["stance"] not in ("negotiable", "firm"):
            raise EvidenceError("buyer stance must be null, negotiable, or firm")
        if result["action"] != "offer":
            raise EvidenceError("a public stance is only valid for an offer")
    ceiling = numeric_price(budget)
    quoted = numeric_price(ask) if ask is not None else None
    action, price = result["action"], result["price"]
    if action not in {"offer", "accept", "walk_away"}:
        raise EvidenceError("buyer action must be offer, accept, or walk_away")
    if action == "walk_away":
        if price is not None:
            raise EvidenceError("walk_away must have a null price")
    else:
        if price is None:
            raise EvidenceError("offer or acceptance requires a numeric price")
        if numeric_price(price) > ceiling:
            raise EvidenceError("buyer price exceeds the configured budget")
        if action == "accept" and quoted is not None and numeric_price(price) != quoted:
            raise EvidenceError("acceptance price differs from the seller's quote")
    return result


def verify_receipt(
    receipt: Mapping[str, Any], *, item: str, budget: float,
    expected_price: float | None, task_id: str | None = None,
    expected_quote_id: str | None = None,
) -> list[str]:
    """List inconsistencies; missing optional receipt task IDs are not a failure.

    A receipt alone cannot prove delivery or the seller's identity. These checks
    compare it only with the locally recorded agreement and configured budget.
    """
    ceiling = numeric_price(budget)
    expected = numeric_price(expected_price) if expected_price is not None else None
    if not isinstance(receipt, Mapping):
        return ["receipt_not_object"]
    issues: list[str] = []
    item_fields = [receipt[key] for key in ("item", "item_id", "itemId") if key in receipt]
    if not item_fields:
        issues.append("missing_item_evidence")
    elif any(value != item for value in item_fields):
        issues.append("item_mismatch")
    try:
        actual_price = numeric_price(receipt.get("price"))
    except EvidenceError:
        issues.append("invalid_receipt_price")
    else:
        if actual_price > ceiling:
            issues.append("receipt_exceeds_budget")
        if expected is not None and actual_price != expected:
            issues.append("price_mismatch")
    if expected is None:
        issues.append("missing_agreed_price_evidence")
    if task_id is not None:
        for key in ("taskId", "task_id", "task"):
            if key in receipt and receipt[key] != task_id:
                issues.append("task_id_mismatch")
                break
    for label, aliases, expected_id in (
        ("task", ("taskId", "task_id", "task"), task_id),
        ("quote", ("quoteId", "quote_id"), expected_quote_id),
        ("purchase", ("purchaseId", "purchase_id"), None),
    ):
        if expected_id is not None and (not isinstance(expected_id, str) or not expected_id.strip()):
            raise EvidenceError(f"expected {label} ID must be nonempty text")
        identifier, identifier_issues = _receipt_identifier(receipt, aliases, label)
        issues.extend(identifier_issues)
        # The kit does not supply optional IDs. Compare only IDs actually echoed.
        if identifier is not None and expected_id is not None and identifier != expected_id:
            issues.append(f"{label}_id_mismatch")
    return list(dict.fromkeys(issues))


def _receipt_identifier(receipt, aliases, label):
    values = [receipt[key] for key in aliases if key in receipt]
    if not values:
        return None, []
    if any(not isinstance(value, str) or not value.strip() for value in values):
        return None, [f"invalid_{label}_id"]
    if len(set(values)) != 1:
        return None, [f"{label}_id_mismatch"]
    return values[0], []


def _json_copy(value: Any) -> Any:
    try:
        return json.loads(json.dumps(value, allow_nan=False))
    except (TypeError, ValueError, OverflowError) as exc:
        raise EvidenceError("transaction evidence must be finite JSON data") from exc


class Ledger:
    """Thread-safe purchase reservations, with optional atomic JSON persistence.

    Purchase IDs cannot be reused, including after release. An exact replay of a
    successful commit is idempotent. A dispute keeps its funds unavailable until
    a separate reconciliation mechanism exists; release never undoes a purchase.
    The lock coordinates threads in one buyer process, not multiple processes.
    """

    def __init__(self, total_budget: float, path: str | Path | None = None):
        self.total_budget = numeric_price(total_budget)
        self.path = Path(path) if path is not None else None
        self._lock = threading.Lock()
        self._purchases: dict[str, dict[str, Any]] = {}
        if self.path is not None and self.path.exists():
            self._load()

    @staticmethod
    def _identifier(value: str, name: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise EvidenceError(f"{name} must be nonempty text")
        return value

    def _totals(self, purchases: Mapping[str, dict[str, Any]]) -> tuple[float, float]:
        committed = math.fsum(record["price"] for record in purchases.values()
                              if record["state"] != "released" and record["committed"])
        reserved = math.fsum(record["price"] for record in purchases.values()
                             if record["state"] != "released" and not record["committed"])
        return committed, reserved

    def _snapshot(self, purchases: Mapping[str, dict[str, Any]]) -> dict[str, Any]:
        committed, reserved = self._totals(purchases)
        return {"version": 1, "total_budget": self.total_budget,
                "committed_total": committed, "reserved_total": reserved,
                "available_budget": self.total_budget - committed - reserved,
                "purchases": copy.deepcopy(dict(purchases))}

    def _save(self, purchases: dict[str, dict[str, Any]]) -> None:
        if self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary: str | None = None
            try:
                with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8",
                                                 dir=self.path.parent, delete=False,
                                                 prefix=f".{self.path.name}.") as handle:
                    temporary = handle.name
                    json.dump(self._snapshot(purchases), handle, ensure_ascii=False,
                              indent=2, allow_nan=False)
                    handle.write("\n")
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary, self.path)
            finally:
                if temporary is not None and os.path.exists(temporary):
                    os.unlink(temporary)
        self._purchases = purchases

    def _load(self) -> None:
        try:
            saved = json.loads(self.path.read_text(encoding="utf-8"))
            if saved["version"] != 1 or numeric_price(saved["total_budget"]) != self.total_budget:
                raise EvidenceError("persisted ledger version or total budget differs")
            purchases = saved["purchases"]
            if not isinstance(purchases, dict):
                raise EvidenceError("persisted purchases must be an object")
            active_items: set[str] = set()
            for purchase_id, record in purchases.items():
                self._identifier(purchase_id, "purchase_id")
                item_key = self._identifier(record["item_key"], "item_key")
                numeric_price(record["price"])
                if record["purchase_id"] != purchase_id:
                    raise EvidenceError("persisted purchase ID differs from its key")
                if record["state"] not in {"reserved", "committed", "released", "disputed"}:
                    raise EvidenceError("persisted purchase state is invalid")
                if not isinstance(record["committed"], bool):
                    raise EvidenceError("persisted committed flag must be boolean")
                if record["state"] == "committed" and not record["committed"]:
                    raise EvidenceError("persisted committed purchase has no commitment")
                if record["state"] in {"reserved", "released"} and record["committed"]:
                    raise EvidenceError("persisted reservation cannot already be committed")
                if record["state"] != "released":
                    if item_key in active_items:
                        raise EvidenceError("persisted ledger contains a duplicate purchase")
                    active_items.add(item_key)
            committed, reserved = self._totals(purchases)
            if committed + reserved > self.total_budget:
                raise EvidenceError("persisted ledger exceeds its total budget")
            self._purchases = _json_copy(purchases)
        except (OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
            raise EvidenceError("cannot restore persisted ledger") from exc

    def reserve(self, item_key: str, price: float, purchase_id: str, *,
                expected_item: str | None = None, seller_origin: str | None = None,
                expected_quote_id: str | None = None) -> dict[str, Any]:
        """Atomically reserve one item and funds before sending acceptance."""
        item_key = self._identifier(item_key, "item_key")
        purchase_id = self._identifier(purchase_id, "purchase_id")
        amount = numeric_price(price)
        expected_item = self._identifier(expected_item or item_key, "expected_item")
        if seller_origin is not None:
            seller_origin = self._identifier(seller_origin, "seller_origin")
        if expected_quote_id is not None:
            expected_quote_id = self._identifier(expected_quote_id, "expected_quote_id")
        with self._lock:
            if purchase_id in self._purchases:
                raise EvidenceError("purchase_id has already been used")
            if any(record["item_key"] == item_key and record["state"] != "released"
                   for record in self._purchases.values()):
                raise EvidenceError("item is already reserved or purchased")
            committed, reserved = self._totals(self._purchases)
            if math.fsum((committed, reserved, amount)) > self.total_budget:
                raise EvidenceError("reservation exceeds the remaining total budget")
            proposed = copy.deepcopy(self._purchases)
            record = {"purchase_id": purchase_id, "item_key": item_key,
                      "expected_item": expected_item,
                      "seller_origin": seller_origin, "expected_quote_id": expected_quote_id,
                      "price": amount, "state": "reserved", "committed": False,
                      "receipt": None, "disputed_reasons": []}
            proposed[purchase_id] = record
            self._save(proposed)
            return copy.deepcopy(record)

    def commit(self, purchase_id: str, receipt: Mapping[str, Any]) -> dict[str, Any]:
        """Confirm a reservation using a matching receipt; do not pay twice."""
        with self._lock:
            record = self._get(purchase_id)
            received = _json_copy(receipt)
            if record["state"] == "committed" and record["receipt"] == received:
                return copy.deepcopy(record)
            if record["state"] != "reserved":
                raise EvidenceError("only an active reservation can be committed")
            issues = verify_receipt(received, item=record.get("expected_item", record["item_key"]),
                                    budget=record["price"], expected_price=record["price"],
                                    expected_quote_id=record.get("expected_quote_id"))
            if issues:
                raise EvidenceError("receipt verification failed: " + ", ".join(issues))
            remote_id, _ = _receipt_identifier(received, ("purchaseId", "purchase_id"), "purchase")
            if remote_id is not None:
                for previous_id, previous in self._purchases.items():
                    old_receipt = previous.get("receipt")
                    if (previous_id != purchase_id and isinstance(old_receipt, dict)
                            and previous.get("seller_origin") == record.get("seller_origin")):
                        old_remote_id, _ = _receipt_identifier(old_receipt, ("purchaseId", "purchase_id"), "purchase")
                        if old_remote_id == remote_id:
                            raise EvidenceError("receipt verification failed: purchase_id_replay")
            proposed = copy.deepcopy(self._purchases)
            proposed[purchase_id].update(state="committed", committed=True, receipt=received)
            self._save(proposed)
            return copy.deepcopy(proposed[purchase_id])

    def _get(self, purchase_id: str) -> dict[str, Any]:
        self._identifier(purchase_id, "purchase_id")
        if purchase_id not in self._purchases:
            raise EvidenceError("unknown purchase_id")
        return self._purchases[purchase_id]

    def release(self, purchase_id: str) -> dict[str, Any]:
        """Release an uncommitted reservation only after an unambiguous stop."""
        with self._lock:
            record = self._get(purchase_id)
            if record["state"] != "reserved":
                raise EvidenceError("only an undisputed reservation can be released")
            proposed = copy.deepcopy(self._purchases)
            proposed[purchase_id]["state"] = "released"
            self._save(proposed)
            return copy.deepcopy(proposed[purchase_id])

    def mark_disputed(self, purchase_id: str, reason: str) -> dict[str, Any]:
        """Record unresolved evidence while keeping all potentially spent funds."""
        if not isinstance(reason, str) or not reason.strip():
            raise EvidenceError("dispute reason must be nonempty text")
        with self._lock:
            record = self._get(purchase_id)
            if record["state"] == "released":
                raise EvidenceError("a released reservation cannot become disputed")
            proposed = copy.deepcopy(self._purchases)
            updated = proposed[purchase_id]
            updated["state"] = "disputed"
            if reason not in updated["disputed_reasons"]:
                updated["disputed_reasons"].append(reason)
            self._save(proposed)
            return copy.deepcopy(updated)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return self._snapshot(self._purchases)
