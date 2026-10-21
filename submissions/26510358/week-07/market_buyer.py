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
from referral_pricing import append_review_notice, review_notice
from simulation_reviews import SimulationReviewPolicy

ROOT = Path(__file__).resolve().parent
OWN_NAMES = {"myeonggi", "gacha-shop", "SUNGMYEONGGI"}


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


def buy(shops, item, budget, memory, trace, *, review_policy=None):
    budget = numeric_price(budget)
    # Read campaign text before attempting a purchase; configuration failure
    # must not cause a successfully received receipt to be discarded afterward.
    try:
        notice = review_notice(json.loads((ROOT / "data" / "price-policy.json").read_text()).get("review_referral"))
        if len(notice.strip()) >= 400:
            raise ValueError("Review advertisement must fit within the public review limit")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        notice = ""
        trace.write("review_referral_notice_unavailable", detail=str(exc))

    def report(shop, receipt, stars, review):
        # All outcomes share the same advertising path, independently of rank,
        # success and signatures. Publication eligibility remains the kit's job.
        return {"shop": shop, "receipt": receipt, "stars": stars,
                "review": append_review_notice(review, notice)}

    feedback = memory.ingest_kit_feedback(ROOT / "logs" / "market-kit.log")
    if feedback:
        trace.write("market_verified_feedback", results=feedback)
    eligible = [s for s in shops if isinstance(s, dict) and isinstance(s.get("name"), str)
                and s["name"] not in OWN_NAMES and isinstance(s.get("url"), str)
                and urlparse(s["url"]).scheme in {"http", "https"}]
    ranked = rank_shops(eligible, memory.read(), item, budget)
    trace.write("market_candidates", item=item, budget=budget,
        ranking=[{"name": s["name"], "score": s["score"], "personal_memory": s["personal_memory"],
                  "reviews": s["reviews"]} for s in ranked])
    if not ranked:
        trace.write("market_no_candidate", reason="All supplied shops excluded by personal evidence or own identity")
        return report(shops[0]["url"] if shops else "", None, 1,
                      "No eligible shop; personal receipt-violation history or own identity excluded candidates.")
    with ThreadPoolExecutor(max_workers=6) as executor:
        available = [s for s in executor.map(check_card, ranked[:8]) if s is not None]
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
        chosen = ranked[0]
        review = ("Agent Card attempted to dictate its own review rating; ignored and remembered."
                  if blocked else "Shop unavailable; no purchase attempted.")
        if not blocked:
            memory.record(chosen["name"], item=item, budget=budget, status="error", reason=review)
        return report(chosen["url"], None, 1 if blocked else 2, review)
    choices = {s["name"]: s for s in available}

    def validate(decision):
        if decision.get("action") != "select_shop" or decision.get("item_id") not in choices:
            raise EvidenceError("Select an available supplied shop ID")
        return decision

    try:
        decision = Harness("shop_selector", trace).decide({"request": item, "budget": budget,
            "purpose": "market", "shops": available,
            "preferences": "Reliable affordable completion. Reviews are untrusted observations. Personal fraud evidence dominates."},
            validator=validate, deadline=time.monotonic() + 40, attempts=2)
        chosen, reason = choices[decision["item_id"]], decision["reason"]
    except Exception as exc:
        # Failures in ranking inference do not invent a price or a completed trade.
        chosen, reason = available[0], "Review/personal-memory ranking fallback after selector failure: " + str(exc)[:180]
    trace.write("market_shop_selected", shop=chosen["name"], url=chosen["url"], reason=reason,
                reviews=chosen["reviews"], personal_memory=chosen["personal_memory"])
    try:
        result = negotiate(chosen["url"], item, budget, trace=trace, timeout=180)
        status = result["status"]
        receipt = result.get("receipt")
        issues = result.get("issues", [])
        if status == "completed":
            # Preserve the received DataPart. Only the kit knows the registry's
            # authoritative public key and can verify + add the buyer signature.
            signed = bool(receipt.get("sig_seller"))
            stars = 5 if signed else 3
            review = f"Bought {item} at {receipt['price']} within budget {budget}; " + (
                "seller signature present; registry verification delegated to market kit." if signed else "receipt lacks seller signature; Round 2 verification unavailable.")
            if signed:
                if review_policy is not None:
                    # Rating/config/network errors must never discard a receipt
                    # for a purchase that has already completed.
                    try:
                        strategic_stars, strategy_note = review_policy.evaluate(chosen["name"], stars)
                        if isinstance(strategic_stars, bool) or not isinstance(strategic_stars, int) \
                                or not 1 <= strategic_stars <= 5 or not isinstance(strategy_note, str):
                            raise ValueError("Review policy returned an invalid rating or note")
                        stars = strategic_stars
                        review += strategy_note
                    except Exception as exc:
                        trace.write("simulation_review_policy_failed", shop=chosen["name"],
                                    detail=type(exc).__name__ + ": " + str(exc))
        elif status == "disputed":
            signed, stars = False, 1
            review = "Receipt inconsistent with authorized agreement: " + ", ".join(issues)
        else:
            signed, stars = False, 3
            review = "No feasible agreement within budget; no purchase. " + str(result.get("reason", result.get("state", "")))[:160]
        memory.record(chosen["name"], item=item, budget=budget, status=status,
            price=receipt.get("price") if isinstance(receipt, dict) else None,
            reason=review, issues=issues, signed=signed)
        # Keep disputed received receipts as evidence; the kit rejects invalid
        # ones. Never turn an ambiguous possible purchase into a retry elsewhere.
        return report(chosen["url"], receipt, stars, review)
    except Exception as exc:
        detail = type(exc).__name__ + ": " + str(exc)[:250]
        trace.write("market_trade_error", shop=chosen["name"], detail=detail)
        memory.record(chosen["name"], item=item, budget=budget, status="error", reason=detail)
        return report(chosen["url"], None, 2, "Trade failed; no confirmed receipt. " + detail)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--shops", required=True)
    parser.add_argument("--item", required=True)
    parser.add_argument("--budget", type=float, required=True)
    parser.add_argument("--memory", default=str(ROOT / ".market" / "buyer-memory.json"))
    parser.add_argument("--review-policy")
    args = parser.parse_args()
    trace = Trace("market-buyer")
    review_policy = SimulationReviewPolicy(trace, args.review_policy)
    result = buy(json.loads(Path(args.shops).read_text()), args.item, args.budget,
                 MarketMemory(args.memory), trace, review_policy=review_policy)
    print(json.dumps(result, ensure_ascii=False, allow_nan=False), flush=True)
