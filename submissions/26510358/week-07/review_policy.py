"""Factual pre-settlement reviews and conservative error attribution.

Public text comes from fixed templates, never exception messages, peer review
instructions or a buyer budget. A matching receipt is not proof of settlement,
delivery or quality; this policy never awards five stars. The kit owns final
settlement validation and review publication.
"""
from collections.abc import Mapping
import math
import re

import httpx

from harness import HarnessConfigurationError, HarnessError, HarnessThrottleError
from inference_queue import QueueStateError, QueueTimeout


_ITEM = re.compile(r"(?:lamp|chair|bookshelf|desk|table|cabinet|sofa|bed|stool|bench)(?:-\d{1,6})?\Z")


def _item_label(item):
    """Echo canonical catalog identifiers only, not arbitrary request text."""
    if isinstance(item, str):
        normalized = item.strip().lower()
        if _ITEM.fullmatch(normalized):
            return normalized
    return "requested item"


def _decision(*, stars=None, review="", memory_status="unresolved",
              penalize_shop=False, reason_code="outcome_unconfirmed", attribution="unknown"):
    return {"stars": stars, "review": review, "memory_status": memory_status,
            "penalize_shop": penalize_shop, "reason_code": reason_code,
            "attribution": attribution}


def _whole_price(receipt):
    if not isinstance(receipt, Mapping):
        return None
    price = receipt.get("price")
    if type(price) not in (int, float):
        return None
    try:
        if not math.isfinite(price) or price <= 0 or int(price) != price:
            return None
    except (OverflowError, ValueError):
        return None
    return int(price)


def review_for_result(item, *, status, receipt=None, issues=(), state=None):
    """Describe the runtime's local result without asserting payment success.

    ``status`` and ``issues`` must be validated runtime outputs, not seller claims.
    A contract match with a signature *present* receives four stars provisionally.
    Neither a signature-looking string nor peer ``verified`` fields prove crypto
    verification. Missing signatures remain unresolved. Budgets are not inputs.
    ``penalize_shop`` means a concrete contract/availability observation may affect
    selection; it is never a conclusion about intent or a public fraud label.
    """
    label = _item_label(item)
    if status == "disputed" or (status in {"completed", "receipt_matched"} and issues):
        return _decision(stars=1, review=f"The {label} receipt did not match our agreed purchase.",
                         memory_status="disputed", penalize_shop=True,
                         reason_code="receipt_contract_mismatch", attribution="contract")
    if status in {"completed", "receipt_matched"}:
        price = _whole_price(receipt)
        if price is None:
            return _decision(reason_code="receipt_evidence_missing")
        signature = receipt.get("sig_seller")
        if not isinstance(signature, str) or not signature.strip():
            return _decision(stars=3,
                review=f"The {label} receipt lacks a seller signature; completion unresolved.",
                reason_code="seller_signature_missing", attribution="contract")
        return _decision(stars=4,
            review=f"Agreed {label} x 1 for {price} credits; receipt matches the agreement; market validation pending.",
            memory_status="receipt_matched", reason_code="receipt_contract_matched", attribution="none")
    if status == "no_deal":
        if state == "TASK_STATE_FAILED":
            return _decision(memory_status="unavailable", penalize_shop=True,
                             reason_code="counterparty_task_failed", attribution="counterparty")
        return _decision(stars=3, review=f"No agreement was reached for the {label} purchase.",
                         memory_status="no_deal", reason_code="no_agreement", attribution="none")
    return _decision(reason_code="outcome_unconfirmed")


def _causes(exc):
    """Inspect typed causes, not messages that may contain untrusted text."""
    seen = set()
    while isinstance(exc, BaseException) and id(exc) not in seen and len(seen) < 8:
        seen.add(id(exc))
        yield exc
        exc = exc.__cause__ or exc.__context__


def review_for_error(item, exc, *, uncertain=None):
    """Withhold ratings when no trustworthy shop assessment can be made.

    Local model/authentication/queue failures never harm shop reputation. Typed
    HTTP server/capacity and transport failures are availability observations,
    not proof of fraud. Unclassified exceptions remain unattributed. An unknown
    purchase outcome takes precedence over error attribution: do not buy again
    on the assumption that nothing was paid. No raw exception is made public.
    """
    if uncertain is None:
        uncertain = bool(getattr(exc, "purchase_uncertain", False))
    if uncertain:
        return _decision(reason_code="purchase_outcome_uncertain")
    causes = tuple(_causes(exc))
    for classes, code in (
        ((HarnessThrottleError,), "local_model_rate_limited"),
        ((HarnessConfigurationError,), "local_model_configuration"),
        ((QueueTimeout, QueueStateError), "local_queue_failure"),
        ((HarnessError,), "local_model_failure"),
        ((FileNotFoundError, PermissionError), "local_configuration_failure"),
    ):
        if any(isinstance(cause, classes) for cause in causes):
            return _decision(memory_status="local_error", reason_code=code, attribution="local")
    for cause in causes:
        if isinstance(cause, httpx.HTTPStatusError):
            code = cause.response.status_code
            if code >= 500 or code in {404, 408, 429}:
                return _decision(memory_status="unavailable", penalize_shop=True,
                    reason_code="counterparty_http_unavailable", attribution="counterparty")
            return _decision(reason_code="counterparty_http_rejected", attribution="counterparty")
        if isinstance(cause, (httpx.TimeoutException, httpx.NetworkError,
                              httpx.RemoteProtocolError, httpx.ProxyError)):
            # A local network/proxy failure can look like a shop outage.
            return _decision(memory_status="unavailable", penalize_shop=False,
                reason_code="transport_unavailable_unattributed", attribution="unknown")
    return _decision(reason_code="unattributed_execution_error")
