"""Bounded catalog retrieval: the harness interprets and selects; code filters.

Catalog.from_config(cfg) accepts categories, reserve_offset and an optional
inventory_mod/inventory_remainder partition. search(constraints, limit=8)
returns total, matched_count, and at most eight candidates. select_product
returns action select/clarify/reject, and product+price for a selection.
"""
import json
from pathlib import Path

from harness import HarnessError, decide, parse_object


ROOT = Path(__file__).resolve().parent
CATEGORIES = {"lamp", "chair", "bookshelf", "table"}
MATERIALS = {"wood", "metal", "fabric", "plastic", "glass", "bamboo"}
CONSTRAINT_FIELDS = {"category", "material", "color", "min_capacity_books", "max_height_cm", "min_height_cm", "max_width_cm", "upholstered"}


def public_product(product):
    return {key: value for key, value in product.items() if key != "reserve"}


def validate_constraints(constraints):
    if not isinstance(constraints, dict) or set(constraints) - CONSTRAINT_FIELDS:
        raise HarnessError("Constraints must use only the documented catalog fields")
    if constraints.get("category") not in CATEGORIES:
        raise HarnessError("Search requires a clear category; otherwise ask for clarification")
    if constraints.get("material") is not None and constraints["material"] not in MATERIALS:
        raise HarnessError("Unsupported material must lead to clarification, not a guessed material")
    if constraints.get("color") is not None and constraints["color"] not in {"natural", "white", "black", "blue", "green"}:
        raise HarnessError("Unsupported color must lead to clarification")
    for key in ("min_capacity_books", "max_height_cm", "min_height_cm", "max_width_cm"):
        value = constraints.get(key)
        if value is not None and (type(value) is not int or value < 0):
            raise HarnessError(f"{key} must be a nonnegative integer or null")
    if constraints.get("upholstered") is not None and type(constraints["upholstered"]) is not bool:
        raise HarnessError("upholstered must be boolean or null")
    return constraints


def matches(product, constraints):
    for key in ("category", "material", "color", "upholstered"):
        if constraints.get(key) is not None and product[key] != constraints[key]:
            return False
    for constraint, field in (("min_capacity_books", "capacity_books"), ("min_height_cm", "height_cm")):
        if constraints.get(constraint) is not None and product[field] < constraints[constraint]:
            return False
    for constraint, field in (("max_height_cm", "height_cm"), ("max_width_cm", "width_cm")):
        if constraints.get(constraint) is not None and product[field] > constraints[constraint]:
            return False
    return True


class Catalog:
    def __init__(self, products):
        self.products = products
        self.by_id = {product["id"]: product for product in products}
        if len(self.by_id) != len(products):
            raise ValueError("Catalog product ids must be unique")
        for product in products:
            if not all(product.get(key) for key in ("id", "name", "description")):
                raise ValueError("Catalog products need id, name and description")
            if type(product.get("reserve")) is not int or product["reserve"] <= 0:
                raise ValueError("Catalog reserves must be positive integers")

    @classmethod
    def from_config(cls, cfg):
        path = Path(cfg.get("catalog_path", ROOT / "data" / "catalog.json"))
        if not path.is_absolute():
            path = ROOT / path
        products = json.loads(path.read_text(encoding="utf-8"))
        categories = set(cfg.get("categories", CATEGORIES))
        if not categories or not categories <= CATEGORIES:
            raise ValueError("Shop categories must be known catalog categories")
        mod, remainder = cfg.get("inventory_mod", 1), cfg.get("inventory_remainder", 0)
        offset = cfg.get("reserve_offset", 0)
        if type(mod) is not int or mod < 1 or type(remainder) is not int or not 0 <= remainder < mod:
            raise ValueError("Invalid inventory partition")
        if type(offset) is not int:
            raise ValueError("reserve_offset must be an integer")
        selected = [dict(product, reserve=product["reserve"] + offset)
                    for product in products if product["category"] in categories
                    and (int(product["id"].rsplit("-", 1)[1]) - 1) % mod == remainder]
        return cls(selected)

    def search(self, constraints, limit=8):
        validate_constraints(constraints)
        if type(limit) is not int or not 1 <= limit <= 8:
            raise ValueError("The model sees at most eight catalog candidates")
        found = [product for product in self.products if matches(product, constraints)]
        # Rank by small physical footprint, then stable product id. This is
        # retrieval, not a pricing rule; the model makes the final choice.
        found.sort(key=lambda product: (product["capacity_books"] - constraints.get("min_capacity_books", 0)
                                       if constraints.get("min_capacity_books") is not None else 0,
                                       product["height_cm"], product["width_cm"], product["id"]))
        return {"total": len(self.products), "matched_count": len(found), "candidates": found[:limit]}


def parse_query(output):
    decision = parse_object(output)
    if decision.get("action") == "clarify":
        if not isinstance(decision.get("question"), str) or not decision["question"].strip():
            raise HarnessError("A clarification needs a question")
    elif decision.get("action") == "search":
        validate_constraints(decision.get("constraints"))
    else:
        raise HarnessError("Query action must be search or clarify")
    return decision


def parse_selection(output, candidates, constraints):
    decision = parse_object(output)
    action = decision.get("action")
    if action not in ("select", "clarify", "reject"):
        raise HarnessError("Selection action must be select, clarify or reject")
    if action == "clarify" and (not isinstance(decision.get("question"), str) or not decision["question"].strip()):
        raise HarnessError("A clarification needs a question")
    if action == "select":
        selected = next((product for product in candidates if product["id"] == decision.get("product_id")), None)
        if selected is None or not matches(selected, constraints):
            raise HarnessError("Selected product must be a returned candidate satisfying all constraints")
        price = decision.get("price")
        if type(price) is not int or price <= 0 or price < selected["reserve"]:
            raise HarnessError("Opening price must be an integer >= this product's reserve")
        decision["product"] = selected
    return decision


def select_product(description, catalog, history=None):
    query = decide("catalog_query.txt", {"buyer_description": description, "clarification_history": history or []},
                   parse_query, prefix="catalog-query")
    if query["action"] == "clarify":
        print(json.dumps({"event": "catalog_clarify", "question": query["question"]}), flush=True)
        return query
    retrieval = catalog.search(query["constraints"])
    print(json.dumps({"event": "catalog_search", "constraints": query["constraints"], "total": retrieval["total"],
                      "matched_count": retrieval["matched_count"], "candidate_count": len(retrieval["candidates"]),
                      "candidate_ids": [product["id"] for product in retrieval["candidates"]]}, ensure_ascii=False), flush=True)
    context = {"buyer_description": description, "clarification_history": history or [],
               "constraints": query["constraints"], **retrieval}
    decision = decide("catalog_select.txt", context,
                      lambda output: parse_selection(output, retrieval["candidates"], query["constraints"]), prefix="catalog-select")
    print(json.dumps({"event": "catalog_selection", "action": decision["action"],
                      "product_id": decision.get("product_id"), "price": decision.get("price"),
                      "reason": decision["reason"]}, ensure_ascii=False), flush=True)
    return decision
