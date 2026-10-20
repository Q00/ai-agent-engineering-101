"""Normalize public seller observations without choosing prices or purchases.

Known metadata/DataPart fields are transport adapters, not trusted limits. A
missing numeric quote is interpreted by the actual observation harness; code
only checks grounding. Conflicting structured quotes fail closed. Text numbers
are never ranked, and no last-number or computed-price fallback exists.
"""
from __future__ import annotations

from copy import deepcopy
import json
import re
import time
from typing import Any, Mapping

from evidence import EvidenceError, numeric_price


PRICE_KEYS = ("ask", "asking_price", "askingPrice", "current_ask", "current_price", "price", "unit_price")
ITEM_KEYS = ("itemId", "item_id", "productId", "product_id", "sku")
QUOTE_KEYS = ("quoteId", "quote_id")
CURRENCY_KEYS = ("currency", "currency_code", "currencyCode")
QUANTITY_KEYS = ("quantity", "qty")
# A period followed by a digit continues a decimal; a sentence period does not.
# The final guard still forbids backtracking to an integer prefix of a decimal.
_NUMBERS = re.compile(r"(?<![\d.])[-+]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?:[eE][-+]?\d+)?(?!\d|\.\d)")


def collect_quote_evidence(task: Mapping[str, Any]) -> dict[str, Any]:
    """Collect only the current message's quote fields, never receipt artifacts.

    Supported aliases are intentionally narrow. In particular, dimensions,
    budgets, reserves and product descriptions are not numeric quote fields.
    Raw data remains visible to the harness for unsupported peer formats.
    """
    status = task.get("status")
    message = status.get("message", {}) if isinstance(status, Mapping) else {}
    message = message if isinstance(message, Mapping) else {}
    parts = message.get("parts", [])
    parts = parts if isinstance(parts, list) else []
    metadata = message.get("metadata", {})
    candidates = {field: [] for field in ("ask", "item_id", "quote_id", "currency", "quantity")}
    issues, contract_errors, data_parts, texts = [], [], [], []

    def record(field, value, path):
        try:
            if field in ("ask", "quantity"):
                value = numeric_price(value)
            elif not isinstance(value, str) or not value.strip():
                raise EvidenceError("identifier must be nonempty text")
            elif field == "currency":
                value = value.strip().upper()
        except EvidenceError:
            issues.append("invalid_quote_field:" + path)
            if field in ("currency", "quantity"):
                contract_errors.append(field)
            return
        candidates[field].append({"value": value, "path": path})

    def inspect_contract(payload, path):
        for field, keys in (("currency", CURRENCY_KEYS), ("quantity", QUANTITY_KEYS)):
            for key in keys:
                if key in payload:
                    record(field, payload[key], path + "." + key)

    def inspect(payload, path, *, product=False):
        if not isinstance(payload, Mapping):
            return
        for key in PRICE_KEYS:
            if key in payload:
                value = payload[key]
                if isinstance(value, Mapping):
                    inspect_contract(value, path + "." + key)
                    # An amount nested under a price field is a monetary value.
                    if "amount" in value:
                        record("ask", value["amount"], path + "." + key + ".amount")
                    elif "value" in value:
                        record("ask", value["value"], path + "." + key + ".value")
                    else:
                        issues.append("invalid_quote_field:" + path + "." + key)
                else:
                    record("ask", value, path + "." + key)
        for key in ITEM_KEYS + (("id",) if product else ()):
            if key in payload:
                record("item_id", payload[key], path + "." + key)
        for key in QUOTE_KEYS:
            if key in payload:
                record("quote_id", payload[key], path + "." + key)
        inspect_contract(payload, path)
        for key in ("item", "product"):
            value = payload.get(key)
            if isinstance(value, str):
                record("item_id", value, path + "." + key)
            elif isinstance(value, Mapping):
                inspect(value, path + "." + key, product=True)
        if isinstance(payload.get("quote"), Mapping):
            inspect(payload["quote"], path + ".quote")

    inspect(task.get("metadata"), "task.metadata")
    inspect(metadata, "message.metadata")
    for index, part in enumerate(parts):
        if not isinstance(part, Mapping):
            continue
        if isinstance(part.get("text"), str):
            texts.append(part["text"])
        if isinstance(part.get("data"), Mapping):
            data_parts.append(deepcopy(dict(part["data"])))
            inspect(part["data"], f"message.parts[{index}].data")
    return {"remote_message": " ".join(texts),
            "remote_metadata": deepcopy(metadata),
            "remote_task_metadata": deepcopy(task.get("metadata", {})),
            "remote_data": data_parts, "candidates": candidates, "issues": issues,
            "contract_errors": contract_errors}


def _unique(evidence, field):
    values = {candidate["value"] for candidate in evidence["candidates"][field]}
    if len(values) > 1:
        raise EvidenceError("Conflicting structured quote " + field + " evidence")
    return next(iter(values)) if values else None


def contract_issues(payload, *, currency="CREDITS", quantity=1):
    """Compare only explicit units with the buyer's fixed one-item contract.

    Missing units preserve the course peers' implicit credits/quantity-one
    convention. No exchange rate, unit conversion, or inferred quantity exists.
    This also checks explicit receipt units before committing the ledger.
    """
    if not isinstance(payload, Mapping):
        return ["invalid_contract_evidence"]
    evidence = collect_quote_evidence({"status": {"message": {"metadata": payload}}})
    issues = []
    for field, expected in (("currency", currency), ("quantity", quantity)):
        if field in evidence["contract_errors"]:
            issues.append("invalid_" + field)
            continue
        try:
            actual = _unique(evidence, field)
        except EvidenceError:
            issues.append("conflicting_" + field + "_evidence")
            continue
        if actual is not None and actual != expected:
            issues.append(field + "_mismatch")
    return issues


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, Mapping):
        for child in value.values():
            yield from _strings(child)
    elif isinstance(value, list):
        for child in value:
            yield from _strings(child)


def _grounded_identifier(identifier, raw):
    token = re.compile(r"(?<![\w-])" + re.escape(identifier) + r"(?![\w-])")
    return any(value == identifier or token.search(value) for value in _strings(raw))


def normalize_quote(task, *, observer=None, trace=None, context=None, deadline=None):
    """Return a grounded observation before policy selection and buyer decisions.

    A unique numeric structured ask needs no inference. Otherwise observer must
    be the actual ``Harness('quote_observer', ...)`` in runtime; tests may supply
    an explicitly simulated observer. Unknown fields remain null, never guessed
    from the request, previous quote, budget or a number's position in text.
    """
    evidence = collect_quote_evidence(task)
    if evidence["contract_errors"]:
        raise EvidenceError("Invalid structured quote " + ", ".join(sorted(set(evidence["contract_errors"]))))
    observed = {field: _unique(evidence, field)
                for field in ("ask", "item_id", "quote_id", "currency", "quantity")}
    paths = {field: [candidate["path"] for candidate in evidence["candidates"][field]]
             for field in observed}
    source = "structured"
    if observed["ask"] is None:
        # Exact course wire sentences only. Additional text/numbers/instructions
        # fail the full match and still require the real observation harness.
        text = evidence["remote_message"].strip()
        number = r"(\d+(?:\.\d+)?)"
        opening = re.fullmatch(r"(?:lamp|chair|bookshelf): asking " + number +
                              r"\. Send your offer as a number\.", text, re.I)
        counter = re.fullmatch(number + r" is too low\. Counteroffer: " + number + r"\.", text, re.I)
        if opening or counter:
            observed["ask"] = numeric_price(float(opening.group(1) if opening else counter.group(2)))
            source = "protocol_text"
            paths["ask"] = ["message.text.explicit_protocol_ask"]
    if observed["ask"] is None:
        if observer is None:
            raise EvidenceError("Actual quote observer required for unstructured price evidence")
        source = "harness"
        raw = {key: evidence[key] for key in (
            "remote_message", "remote_metadata", "remote_task_metadata", "remote_data")}
        numbers = set()
        # This set only validates model output. It never selects a number.
        for token in _NUMBERS.findall(json.dumps(raw, ensure_ascii=False)):
            try:
                numbers.add(numeric_price(float(token.replace(",", ""))))
            except (EvidenceError, ValueError, OverflowError):
                pass

        def validate(output):
            required = {"ask", "item_id", "quote_id", "reason"}
            if not isinstance(output, Mapping) or set(output) != required:
                raise EvidenceError("Quote observation needs exactly ask, item_id, quote_id, reason")
            if not isinstance(output["reason"], str):
                raise EvidenceError("Quote observation reason must be text")
            result = dict(output)
            if result["ask"] is not None:
                result["ask"] = numeric_price(result["ask"])
                if result["ask"] not in numbers:
                    raise EvidenceError("Observed asking price must occur in the quoted remote evidence")
            for field in ("item_id", "quote_id"):
                value = result[field]
                if value is not None and (not isinstance(value, str) or not value.strip()
                                          or not _grounded_identifier(value, raw)):
                    raise EvidenceError("Observed " + field + " must occur in the quoted remote evidence")
                if observed[field] is not None and value != observed[field]:
                    raise EvidenceError("Observation must retain the structured " + field)
            return result

        observation_context = {**(context or {}), "stage": "quote_observation",
                               "remote_evidence": raw, "structured_observation": observed,
                               "normalization_issues": evidence["issues"]}
        started = time.monotonic()
        if trace:
            trace.write("quote_observation_started", task_id=task.get("id"),
                        remaining_seconds=None if deadline is None else max(0, deadline - started))
        try:
            decision = observer.decide(observation_context, deadline=deadline, validator=validate)
            observed.update({field: decision[field] for field in ("ask", "item_id", "quote_id")})
        finally:
            if trace:
                trace.write("quote_observation_finished", task_id=task.get("id"),
                            elapsed_including_queue=round(time.monotonic() - started, 3),
                            remaining_seconds=None if deadline is None else max(0, deadline - time.monotonic()))
    result = {**observed, "source": source, "evidence_sources": paths,
              "issues": evidence["issues"], **{key: evidence[key] for key in (
                  "remote_message", "remote_metadata", "remote_task_metadata", "remote_data")}}
    if trace:
        trace.write("quote_normalized", task_id=task.get("id"), observation=result)
    return result
