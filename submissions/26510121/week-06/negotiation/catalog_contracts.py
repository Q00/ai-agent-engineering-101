"""Strict contracts for model intent extraction and shortlist selection."""
from dataclasses import dataclass
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict

from negotiation.catalog import matches
from negotiation.contracts import DecisionError, _unique_object


class NeedDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    action: Literal["search", "clarify", "unsupported"]
    category: Literal["bookshelf", "chair", "lamp", "desk", "cabinet"] | None
    material: Literal["wood", "metal", "plastic"] | None
    color: Literal["black", "white", "brown", "blue"] | None
    min_books: int | None
    max_height_cm: int | None
    max_width_cm: int | None
    reason: str


class SelectionDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    action: Literal["select", "clarify"]
    item_id: str | None
    reason: str


@dataclass(frozen=True)
class NeedContext:
    inventory_version: str = "synthetic-v1"


@dataclass(frozen=True)
class SelectionContext:
    need: dict
    candidates: list


def catalog_schema(role):
    return (NeedDecision if role == "need" else SelectionDecision).model_json_schema()


def _parse(raw, model):
    try:
        decision = model.model_validate(json.loads(raw, object_pairs_hook=_unique_object), strict=True)
    except DecisionError:
        raise
    except (ValueError, TypeError):
        raise DecisionError("invalid_catalog_decision_shape") from None
    if not decision.reason.strip() or len(decision.reason) > 240:
        raise DecisionError("invalid_reason_length")
    return decision


def parse_need_decision(raw, context):
    decision = _parse(raw, NeedDecision)
    if decision.action == "search":
        if decision.category is None:
            raise DecisionError("search_requires_category")
        for value in (decision.min_books, decision.max_height_cm, decision.max_width_cm):
            if value is not None and (type(value) is not int or value <= 0):
                raise DecisionError("positive_integer_constraint_required")
        if decision.min_books is not None and decision.category not in {"bookshelf", "cabinet"}:
            raise DecisionError("book_capacity_requires_storage")
    elif any(value is not None for key, value in decision.model_dump().items() if key not in {"action", "reason"}):
        raise DecisionError("non_search_requires_null_constraints")
    return decision


def parse_selection_decision(raw, context):
    decision = _parse(raw, SelectionDecision)
    if decision.action == "clarify":
        if decision.item_id is not None:
            raise DecisionError("clarify_requires_null_item")
    else:
        row = next((row for row in context.candidates if row["item_id"] == decision.item_id), None)
        if row is None:
            raise DecisionError("selection_outside_candidates")
        if not matches(row, context.need):
            raise DecisionError("selected_product_violates_constraints")
    return decision
