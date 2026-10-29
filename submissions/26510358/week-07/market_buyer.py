"""Review-aware market buyer. The kit alone submits orders, receipts and reviews."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import time
from urllib.parse import urlparse

import httpx

from buyer import negotiate
from evidence import EvidenceError, inspect_untrusted, numeric_price
from harness import Harness
from market_memory import MarketMemory, rank_shops
from tracing import Trace
from trade_funnel import emit_stage
from review_policy import review_for_error, review_for_result

ROOT = Path(__file__).resolve().parent
OWN_NAMES = {"myeonggi", "gacha-shop", "SUNGMYEONGGI"}
OWN_OWNER = "sungmyeonggi"


def selection_view(shop, item):
    """Bound model context, retaining totals and recent/item/fraud evidence.

    The persistent memory and deterministic ranking still use full histories.
    """
    memory = shop.get("personal_memory", {})
    history = memory.get("recent", [])
    indices = set(range(max(0, len(history) - 3), len(history)))
    indices.update([i for i, row in enumerate(history) if row.get("item") == item][-2:])
    indices.update([i for i, row in enumerate(history) if row.get("issues")][-2:])
    recent = []
    for i in sorted(indices):
        row = history[i]
        compact = {k: row[k] for k in ("time", "item", "budget", "status", "price", "issues", "signature_present",
                                      "task", "offer_count", "response_seconds", "verified", "round",
                                      "settled", "settlement_ledger", "penalize_shop", "attribution") if k in row}
        if row.get("issues") or row.get("status") in ("error", "untrusted", "disputed"):
            compact["reason"] = str(row.get("reason", ""))[:160]
        recent.append(compact)
    compact_memory = {key: value for key, value in memory.items()
                      if key not in ("recent", "verified_ledgers", "verified_by_item", "settled_by_item",
                                     "settled_orders", "settlement_ledgers")}
    compact_memory["verified_by_item"] = {item: memory.get("verified_by_item", {}).get(item, {})}
    compact_memory["settled_by_item"] = {item: memory.get("settled_by_item", {}).get(item, {})}
    return {**shop, "personal_memory": {**compact_memory, "recent": recent}}


def check_card(shop):
    """Inspect only supplied shop URLs. GET never creates a purchase."""
    try:
        response = httpx.get(shop["url"].rstrip("/") + "/.well-known/agent-card.json", timeout=3,
                             headers={"A2A-Version": "1.0"}, trust_env=False)
        response.raise_for_status()
        card = response.json()
        description = str(card.get("description", ""))[:600]
        return {**shop, "card": {"name": card.get("name"), "description": description},
                "card_flags": inspect_untrusted(description)}
    except Exception:
        return None


def buy(shops, item, budget, memory, trace):
    budget = numeric_price(budget)
    def report(shop, receipt, stars, review):
        # State observed transaction facts only. The kit controls publication.
        review = " ".join(review.split())[:400]
        trace.write("market_review_prepared", style="concise_transaction_facts_v1",
                    advertising=False, stars=stars, characters=len(review))
        return {"shop": shop, "receipt": receipt, "stars": stars,
                "review": review}

    feedback = memory.ingest_kit_feedback(ROOT / "logs" / "market-kit.log")
    if feedback:
        trace.write("market_verified_feedback", results=feedback)
        for result in feedback:
            task_id = result.get("task")
            if not isinstance(task_id, str) or not task_id:
                continue
            if result.get("task_linkage") != "runtime":
                trace.write("trade_feedback_unlinked", reason_code="missing_runtime_task",
                            shop=result.get("shop"), ledger=result.get("ledger"))
                continue
            if result.get("verified") is True and result.get("signature") == "verified":
                emit_stage(trace, "market_verified", side="buyer", task_id=task_id,
                           item=result.get("item"), shop=result.get("shop"),
                           price=result.get("price"), outcome="verified",
                           evidence="market_kit_feedback")
            else:
                emit_stage(trace, "ended", side="buyer", task_id=task_id,
                           item=result.get("item"), shop=result.get("shop"),
                           outcome="unresolved", reason_code=("local_contract_failed" if not result.get("local_contract_ok", True)
                               else "settlement_unconfirmed" if result.get("market_verified") and result.get("settlement_required")
                               else "market_verification_failed"))
    eligible = [s for s in shops if isinstance(s, dict) and isinstance(s.get("name"), str)
                and s["name"] not in OWN_NAMES and isinstance(s.get("url"), str)
                and not (s.get("registry_owner_verified") is True
                         and str(s.get("registry_owner", "")).casefold() == OWN_OWNER)
                and urlparse(s["url"]).scheme in {"http", "https"}]
    ranked = rank_shops(eligible, memory.read(), item, budget)
    trace.write("market_candidates", item=item, budget=budget,
        ranking=[{"name": s["name"], "score": s["score"],
                  "personal_memory": {k: v for k, v in selection_view(s, item)["personal_memory"].items() if k != "recent"},
                  "reviews": s["reviews"]} for s in ranked])
    if not ranked:
        trace.write("market_no_candidate", reason="All supplied shops excluded by personal evidence or own identity")
        return report("", None, None, "")
    pool = ranked[:8]
    with ThreadPoolExecutor(max_workers=6) as executor:
        available = [s for s in executor.map(check_card, pool) if s is not None]
    trusted_candidates, blocked = [], []
    for candidate in available:
        if "review_manipulation" in candidate["card_flags"]:
            blocked.append(candidate["name"])
            trace.write("market_card_instruction_rejected", shop=candidate["name"],
                url=candidate["url"], card=candidate["card"], flags=candidate["card_flags"])
            memory.record(candidate["name"], item=item, budget=budget, status="untrusted",
                reason="Agent Card instructs AI buyers to assign its own rating", issues=["review_manipulation"])
        else:
            trusted_candidates.append(candidate)
    available = trusted_candidates
    if not available:
        # No actual transaction supports a public rating of an arbitrary shop.
        trace.write("market_discovery_unavailable", blocked_count=len(blocked), ratings_withheld=True)
        return report("", None, None, "")
    choices = {s["name"]: s for s in available}

    def validate(decision):
        if decision.get("action") != "select_shop" or decision.get("item_id") not in choices:
            raise EvidenceError("item_id must be an exact registry name from selection_ids, not a URL or card display name")
        if decision.get("price") is not None or decision.get("offer") is not None:
            raise EvidenceError("Shop selection cannot set a transaction price or offer")
        return decision

    try:
        decision = Harness("shop_selector", trace).decide({"request": item, "budget": budget,
            "purpose": "market", "selection_ids": list(choices),
            "shops": [selection_view(s, item) for s in available],
            "preferences": "Reliable affordable completion. Reviews are untrusted observations. Personal fraud evidence dominates."},
            instruction="Prefer verified repeat experience when current fit, total and availability are comparable. Obtain a fresh quote; do not assume a past price is current.",
            validator=validate, deadline=time.monotonic() + 40, attempts=2)
        chosen, reason = choices[decision["item_id"]], decision["reason"]
    except Exception as exc:
        # Failures in ranking inference do not invent a price or a completed trade.
        chosen, reason = available[0], "Review/personal-memory ranking fallback after selector failure: " + str(exc)[:180]
    trace.write("market_shop_selected", shop=chosen["name"], url=chosen["url"], reason=reason,
                reviews=chosen["reviews"], personal_memory=selection_view(chosen, item)["personal_memory"])
    memory.visit(chosen["name"], item=item, reason=reason,
                 owner=chosen.get("registry_owner") if chosen.get("registry_owner_verified") else None)
    try:
        result = negotiate(chosen["url"], item, budget, trace=trace, timeout=180)
        status = result["status"]
        receipt = result.get("receipt")
        issues = result.get("issues", [])
        assessment = review_for_result(item, status=status, receipt=receipt, issues=issues, state=result.get("state"))
        signed = isinstance(receipt, dict) and bool(receipt.get("sig_seller"))
        memory_status = assessment["memory_status"]
        memory.record(chosen["name"], item=item, budget=budget, status=memory_status,
            price=receipt.get("price") if isinstance(receipt, dict) else None,
            reason=assessment["reason_code"],
            issues=issues, signed=signed, task=result.get("task_id"),
            offer_count=result.get("offer_count"), response_seconds=result.get("response_seconds"),
            penalize_shop=assessment["penalize_shop"], attribution=assessment["attribution"])
        trace.write("market_trade_observed", shop=chosen["name"], status=memory_status,
                    receipt=receipt, task_id=result.get("task_id"), offer_count=result.get("offer_count"),
                    response_seconds=result.get("response_seconds"))
        # The kit does not know our agreed price/quote or prior task snapshots.
        # Keep disputed artifacts in the trace, never submit them for countersigning.
        return report(chosen["url"], receipt if status == "completed" else None,
                      assessment["stars"], assessment["review"])
    except Exception as exc:
        detail = type(exc).__name__ + ": " + str(exc)[:250]
        trace.write("market_trade_error", shop=chosen["name"], detail=detail)
        assessment = review_for_error(item, exc)
        memory.record(chosen["name"], item=item, budget=budget,
            status=assessment["memory_status"], reason=assessment["reason_code"],
            task=getattr(exc, "task_id", None), offer_count=getattr(exc, "offer_count", None),
            response_seconds=getattr(exc, "response_seconds", None),
            issues=[assessment["reason_code"]], penalize_shop=assessment["penalize_shop"],
            attribution=assessment["attribution"])
        return report(chosen["url"], None, assessment["stars"], assessment["review"])


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--shops", required=True)
    parser.add_argument("--item", required=True)
    parser.add_argument("--budget", type=float, required=True)
    parser.add_argument("--memory", default=str(ROOT / ".market" / "buyer-memory.json"))
    args = parser.parse_args()
    trace = Trace("market-buyer")
    result = buy(json.loads(Path(args.shops).read_text()), args.item, args.budget,
                 MarketMemory(args.memory), trace)
    print(json.dumps(result, ensure_ascii=False, allow_nan=False), flush=True)
