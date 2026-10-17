"""Validate model decisions without choosing or adjusting negotiation prices."""
from dataclasses import dataclass
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, ValidationError


class DecisionError(ValueError):
    """A safe error code that can be sent back to the model for reconsideration."""


class _Decision(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    item_id: str
    price: int | None
    reason: str
    summary: str


class SellerDecision(_Decision):
    action: Literal["ask", "counter", "accept", "reject"]


class BuyerDecision(_Decision):
    action: Literal["offer", "accept", "walk_away"]


def _nonnegative_int(value):
    return type(value) is int and value >= 0


@dataclass(frozen=True)
class SellerContext:
    item_id: str
    reserve: int
    phase: Literal["opening", "negotiating", "terminal"]
    buyer_offer: int | None = None

    def __post_init__(self):
        if not self.item_id or not _nonnegative_int(self.reserve):
            raise ValueError("Invalid trusted seller context")
        if self.phase not in {"opening", "negotiating", "terminal"}:
            raise ValueError("Invalid trusted seller phase")
        if self.buyer_offer is not None and not _nonnegative_int(self.buyer_offer):
            raise ValueError("Invalid observed buyer offer")


@dataclass(frozen=True)
class BuyerContext:
    item_id: str
    budget: int
    seller_ask: int | None
    terminal: bool = False

    def __post_init__(self):
        if not self.item_id or not _nonnegative_int(self.budget):
            raise ValueError("Invalid trusted buyer context")
        if self.seller_ask is not None and not _nonnegative_int(self.seller_ask):
            raise ValueError("Invalid observed seller ask")
        if type(self.terminal) is not bool:
            raise ValueError("Invalid trusted buyer terminal state")


def decision_schema(role):
    """JSON Schema for Responses text.format; business rules are checked below."""
    if role not in {"seller", "buyer"}:
        raise ValueError("Unknown decision role")
    return (SellerDecision if role == "seller" else BuyerDecision).model_json_schema()


def _unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise DecisionError("duplicate_json_key")
        value[key] = item
    return value


def _parse(raw, model):
    try:
        payload = json.loads(raw, object_pairs_hook=_unique_object)
        decision = model.model_validate(payload, strict=True)
    except DecisionError:
        raise
    except (ValueError, TypeError, ValidationError):
        # Do not return raw model content in exception messages.
        raise DecisionError("invalid_decision_shape") from None
    if not decision.item_id.strip():
        raise DecisionError("empty_item_id")
    if not decision.reason.strip() or len(decision.reason) > 240:
        raise DecisionError("invalid_reason_length")
    if not decision.summary.strip() or len(decision.summary) > 600:
        raise DecisionError("invalid_summary_length")
    return decision


def _check_item_and_price(decision, item_id, stop_action):
    if decision.item_id != item_id:
        raise DecisionError("item_changed")
    if decision.action == stop_action:
        if decision.price is not None:
            raise DecisionError("stop_requires_null_price")
    elif type(decision.price) is not int or decision.price <= 0:
        raise DecisionError("positive_integer_price_required")


def parse_seller_decision(raw: str, context: SellerContext) -> SellerDecision:
    if context.phase == "terminal":
        raise DecisionError("task_already_terminal")
    decision = _parse(raw, SellerDecision)
    _check_item_and_price(decision, context.item_id, "reject")
    if context.phase == "opening" and decision.action != "ask":
        raise DecisionError("opening_requires_ask")
    if context.phase == "negotiating" and decision.action == "ask":
        raise DecisionError("followup_requires_counter_accept_or_reject")
    if decision.price is not None and decision.price < context.reserve:
        raise DecisionError("price_below_reserve")
    if decision.action == "accept":
        if context.buyer_offer is None or decision.price != context.buyer_offer:
            raise DecisionError("accept_must_match_observed_offer")
    return decision


def parse_buyer_decision(raw: str, context: BuyerContext) -> BuyerDecision:
    if context.terminal:
        raise DecisionError("task_already_terminal")
    decision = _parse(raw, BuyerDecision)
    _check_item_and_price(decision, context.item_id, "walk_away")
    if decision.price is not None and decision.price > context.budget:
        raise DecisionError("price_above_budget")
    if decision.action == "accept":
        if context.seller_ask is None or decision.price != context.seller_ask:
            raise DecisionError("accept_must_match_observed_ask")
    return decision
