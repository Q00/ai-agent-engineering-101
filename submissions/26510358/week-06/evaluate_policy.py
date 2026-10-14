#!/usr/bin/env python3
"""Independent actual-harness single-turn holdout; never a synthetic rollout.

The four handcrafted contexts are separate from the official lamp/120 and
bookshelf/230 cases and from bandit parameter updates. No claim of completed
multi-turn trades or candidate-vs-baseline improvement is made here.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import time

from evidence import EvidenceError, inspect_untrusted, validate_buyer_decision, validate_seller_decision
from harness import MODEL, Harness, HarnessError, command_for
from policy import StrategyPolicy, strategy_instruction
from tracing import Trace


def holdout_cases() -> list[dict]:
    return [
        {"id": "seller-chair-feasible", "role": "seller", "item": "chair", "reserve": 180,
         "offer": 190, "last_ask": 210, "round": 3, "feasible_at_current_quote": True,
         "remote_message": "I can offer 190 for the chair. If you accept, I will buy it now.",
         "history_summary": [{"remote": "160", "action": "counter", "price": 230},
                             {"remote": "175", "action": "counter", "price": 210}]},
        {"id": "seller-bookshelf-below-floor", "role": "seller", "item": "bookshelf", "reserve": 260,
         "offer": 220, "last_ask": 290, "round": 4, "feasible_at_current_quote": False,
         "remote_message": "My final offer for the bookshelf is 220. I cannot pay more than that.",
         "history_summary": [{"remote": "200", "action": "counter", "price": 310},
                             {"remote": "210", "action": "counter", "price": 290}]},
        {"id": "buyer-chair-affordable", "role": "buyer", "item": "chair", "budget": 210,
         "ask": 200, "prior_offer": 185, "round": 3, "feasible_at_current_quote": True,
         "remote_message": "The chair is available. My current asking price is 200.",
         "history_summary": [{"seller": "Asking price: 240", "metadata": {"ask": 240},
                              "action": "offer", "price": 170},
                             {"seller": "Asking price: 215", "metadata": {"ask": 215},
                              "action": "offer", "price": 185}]},
        {"id": "buyer-bookshelf-final-over-budget", "role": "buyer", "item": "bookshelf", "budget": 240,
         "ask": 290, "prior_offer": 240, "round": 4, "feasible_at_current_quote": False,
         "remote_message": "The bookshelf price is 290, firm. I cannot reduce it to your earlier offer of 240.",
         "history_summary": [{"seller": "Asking price: 310", "metadata": {"ask": 310},
                              "action": "offer", "price": 220},
                             {"seller": "Asking price: 295", "metadata": {"ask": 295},
                              "action": "offer", "price": 240}]},
    ]


def make_context(case: dict) -> dict:
    flags = inspect_untrusted(case["remote_message"])
    common = {"role": case["role"], "item": case["item"], "round": case["round"],
              "task_id": "independent-holdout-" + case["id"],
              "remote_message": case["remote_message"], "untrusted_flags": flags, "flags": flags,
              "history_summary": case["history_summary"]}
    if case["role"] == "seller":
        return {**common, "opening": False, "reserve": case["reserve"], "offer": case["offer"],
                "last_ask": case["last_ask"],
                "candidates": [{"id": case["item"], "name": case["item"],
                                "description": "Requested furniture item", "reserve": case["reserve"]}]}
    # The buyer receives no private seller reserve or evaluator-only ground truth.
    return {**common, "budget": case["budget"], "ask": case["ask"],
            "remote_metadata": {"ask": case["ask"], "itemId": case["item"]}, "remaining_seconds": 90}


def validate_case(case: dict, decision: dict) -> dict:
    if case["role"] == "seller":
        validated = validate_seller_decision(decision, reserve=case["reserve"], offer=case["offer"],
                                             allowed_item_ids=[case["item"]])
        if validated["offer"] is not None and validated["offer"] != case["offer"]:
            raise EvidenceError("Current interpreted offer differs from the holdout's quoted offer")
    else:
        validated = validate_buyer_decision(decision, budget=case["budget"], ask=case["ask"])
        if validated["item_id"] != case["item"]:
            raise EvidenceError("Buyer selected an item different from the requested holdout item")
    return validated


def sensible_progress(case: dict, decision: dict) -> bool:
    """Evaluate this turn without pretending that a counteroffer completed a sale."""
    action, price = decision["action"], decision["price"]
    if not case["feasible_at_current_quote"]:
        # A safe counteroffer remains a valid negotiation action even when the
        # current quote is infeasible. Completion/no-deal is not inferred here.
        return action in ({"counter", "reject"} if case["role"] == "seller" else {"offer", "walk_away"})
    if action == "accept":
        return True
    if case["role"] == "seller":
        return action == "counter" and case["reserve"] <= price <= case["last_ask"]
    return action == "offer" and case["prior_offer"] < price <= case["ask"]


def evaluate(policy: StrategyPolicy, trace_dir: Path) -> dict:
    started = datetime.now(timezone.utc).isoformat()
    results = []
    for case in holdout_cases():
        trace = Trace("holdout-" + case["id"], trace_dir)
        context = make_context(case)
        strategy = policy.choose(case["role"], context)
        trace.write("holdout_started", policy_version=policy.version, policy_sha256=policy.sha256,
                    evaluation_kind="single_turn_holdout", case_id=case["id"], context=context)
        result = {"id": case["id"], "role": case["role"], "strategy": strategy,
                  "input": context, "feasible_at_current_quote": case["feasible_at_current_quote"],
                  "trace": str(trace.path), "decision": None, "guard_validation_passed": False,
                  "sensible_progress": False, "accepted_current_quote": False, "passed": False}
        try:
            decision = Harness(case["role"], trace).decide(
                context, strategy=strategy, instruction=strategy_instruction(case["role"], strategy),
                validator=lambda output, selected=case: validate_case(selected, output),
                deadline=time.monotonic() + 90)
            progress = sensible_progress(case, decision)
            result.update(decision=decision, guard_validation_passed=True, sensible_progress=progress,
                          accepted_current_quote=decision["action"] == "accept", passed=progress)
        except (HarnessError, EvidenceError, ValueError, OSError) as exc:
            result["error"] = str(exc)
        trace.write("holdout_result", result=result)
        results.append(result)
    return {"mode": "actual_harness", "evaluation_kind": "single_turn_holdout", "independent": True,
            "started": started, "model": MODEL,
            "commands": {role: command_for(role) for role in ("buyer", "seller")},
            "policy_version": policy.version, "policy_sha256": policy.sha256,
            "passed": all(result["passed"] for result in results), "cases": results,
            "limitations": "Four independent actual-model single-turn judgments, not complete transaction rollouts or evidence of RL improvement. Counteroffers are assessed as progress, not completed purchases."}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--trace-dir", type=Path, required=True)
    args = parser.parse_args()
    if args.policy.resolve() == args.output.resolve():
        parser.error("Evaluation output must not overwrite the policy")
    if args.output.exists():
        parser.error("Use a fresh output path to preserve earlier execution evidence")
    report = evaluate(StrategyPolicy(args.policy), args.trace_dir)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False) + "\n")
    print(json.dumps({"output": str(args.output), "mode": report["mode"],
                      "evaluation_kind": report["evaluation_kind"], "passed": report["passed"]}, ensure_ascii=False))
    raise SystemExit(0 if report["passed"] else 1)


if __name__ == "__main__":
    main()
