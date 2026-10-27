"""Model shopping plans: choose shops and spending caps, never fabricate purchases."""
from dataclasses import dataclass
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from negotiation.contracts import DecisionError, _unique_object


class Allocation(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    line_id: str
    shop_id: str
    budget: int


class CartDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    action: Literal["plan", "stop"]
    assignments: list[Allocation]
    reason: str = Field(min_length=1, max_length=240)
    summary: str = Field(min_length=1, max_length=600)


@dataclass(frozen=True)
class CartContext:
    available_budget: int
    lines: list


def cart_schema():
    return CartDecision.model_json_schema()


def parse_cart_decision(raw, context):
    try:
        decision = CartDecision.model_validate(json.loads(raw, object_pairs_hook=_unique_object), strict=True)
    except DecisionError:
        raise
    except (ValueError, TypeError):
        raise DecisionError("invalid_cart_decision_shape") from None
    if not decision.reason.strip() or not decision.summary.strip():
        raise DecisionError("empty_cart_explanation")
    if decision.action == "stop":
        if decision.assignments:
            raise DecisionError("stop_requires_empty_assignments")
        return decision
    expected = {line["line_id"]: {shop["shop_id"] for shop in line["shops"]} for line in context.lines}
    actual = [row.line_id for row in decision.assignments]
    if not expected or len(actual) != len(set(actual)) or set(actual) != set(expected):
        raise DecisionError("plan_must_cover_each_pending_line_once")
    for row in decision.assignments:
        if row.shop_id not in expected[row.line_id]:
            raise DecisionError("shop_outside_line_candidates")
        if type(row.budget) is not int or row.budget <= 0:
            raise DecisionError("positive_integer_allocation_required")
    if sum(row.budget for row in decision.assignments) > context.available_budget:
        raise DecisionError("allocations_exceed_available_budget")
    return decision
