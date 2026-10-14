#!/usr/bin/env python3
"""Real tabular contextual-bandit updates in an explicitly synthetic environment.

The simulator below uses simplified price formulas ONLY for training/evaluation;
production prices/actions always come from the actual harness. Synthetic gains
cannot establish actual-harness gains or promote a policy.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import random
from typing import Any

from policy import ACTIONS, BASELINE, StrategyPolicy, context_key


@dataclass(frozen=True)
class Scenario:
    role: str
    reserve: float
    budget: float
    ask: float
    offer: float
    round: int
    fraud: bool
    suspicious: bool
    counterpart: str

    def observation(self) -> dict[str, Any]:
        shared = {"round": self.round, "flags": ["ambiguous_evidence"] if self.suspicious else []}
        if self.role == "buyer":
            return {**shared, "budget": self.budget, "ask": self.ask}
        return {**shared, "reserve": self.reserve, "offer": self.offer}


def sample_scenario(rng: random.Random) -> Scenario:
    reserve = rng.choice((45, 100, 180, 260, 420, 750)) * rng.uniform(0.9, 1.1)
    budget = reserve * rng.uniform(0.65, 1.55)
    fraud = rng.random() < 0.18
    return Scenario(role=rng.choice(("buyer", "seller")), reserve=reserve,
                    budget=budget, ask=reserve * rng.uniform(1.05, 1.7),
                    offer=budget * rng.uniform(0.5, 0.95), round=rng.randrange(8),
                    fraud=fraud, suspicious=(rng.random() < (0.8 if fraud else 0.12)),
                    counterpart=rng.choice(("steady", "impatient", "stubborn")))


def simulate(scenario: Scenario, strategy: str) -> dict[str, Any]:
    """One bandit action runs the remainder of a surrogate negotiation episode.

    Private limits are used by the simulated counterpart and reward evaluator.
    The simulated buyer's offers do not inspect seller reserve; simulated seller
    asks do not inspect buyer budget. All strategies share binding guard checks.
    """
    if strategy not in ACTIONS:
        raise ValueError("Unknown strategy")
    observation = scenario.observation()
    # Clarification consumes a call, and early evidence checking can save later
    # calls. Benign ambiguous cases remain negotiable rather than being rejected.
    calls = 1 if strategy == "patient" and observation["flags"] else 0
    if calls and scenario.fraud:
        return _outcome(scenario, "fraud_blocked", calls, None)
    max_rounds = {"balanced": 5, "patient": 8, "direct": 3}[strategy]
    remaining = max(1, 9 - scenario.round)
    max_rounds = min(max_rounds, remaining)
    counterpart_patience = {"steady": 6, "impatient": 2, "stubborn": 8}[scenario.counterpart]
    max_rounds = min(max_rounds, counterpart_patience)
    if scenario.role == "buyer":
        own_budget = observation["budget"]
        ask = observation["ask"]
        fractions = {"balanced": (0.80, 0.08), "patient": (0.68, 0.06), "direct": (1.0, 0.0)}
        start, increment = fractions[strategy]
        for turn in range(max_rounds):
            calls += 1
            if ask <= own_budget:
                return _finish(scenario, calls, ask)
            offer = own_budget * min(1.0, start + turn * increment)
            # Counterpart has access to its own private reserve.
            if offer >= scenario.reserve:
                return _finish(scenario, calls, offer)
            concession = {"steady": 0.30, "impatient": 0.15, "stubborn": 0.12}[scenario.counterpart]
            ask = max(scenario.reserve, ask - (ask - scenario.reserve) * concession)
    else:
        own_reserve = observation["reserve"]
        offer = observation["offer"]
        markups = {"balanced": (0.25, 0.08), "patient": (0.40, 0.07), "direct": (0.02, 0.02)}
        start, decrement = markups[strategy]
        for turn in range(max_rounds):
            calls += 1
            ask = own_reserve * (1.0 + max(0.0, start - turn * decrement))
            if offer >= own_reserve:
                return _finish(scenario, calls, offer)
            # Simulated counterpart compares against its own budget.
            if ask <= scenario.budget:
                return _finish(scenario, calls, ask)
            growth = {"steady": 0.22, "impatient": 0.08, "stubborn": 0.09}[scenario.counterpart]
            offer = min(scenario.budget, offer + (scenario.budget - offer) * growth)
    return _outcome(scenario, "no_deal", calls, None)


def _finish(scenario: Scenario, calls: int, price: float) -> dict[str, Any]:
    # The evidence guard blocks altered product/price receipts for every policy.
    if scenario.fraud:
        return _outcome(scenario, "fraud_blocked", calls, None)
    if price < scenario.reserve or price > scenario.budget:
        return _outcome(scenario, "constraint_blocked", calls, None)
    return _outcome(scenario, "deal", calls, price)


def _outcome(scenario: Scenario, reason: str, calls: int, price: float | None) -> dict[str, Any]:
    feasible = scenario.budget >= scenario.reserve and not scenario.fraud
    if reason == "deal":
        quality = (scenario.budget - price) / scenario.budget if scenario.role == "buyer" else (price - scenario.reserve) / scenario.reserve
        reward = 6.0 + 2.0 * quality
    elif reason == "fraud_blocked":
        reward = 4.0
    elif reason == "constraint_blocked":
        reward = -20.0
    elif scenario.fraud:
        # No trade occurred, but evidence was not positively resolved.
        reward = 1.0
    else:
        reward = -6.0 if feasible else 3.0
    return {"role": scenario.role, "reason": reason, "feasible": feasible,
            "fraud": scenario.fraud, "calls": calls, "price": price,
            "reward": reward - 0.12 * calls,
            "executed_constraint_violation": False}


def evaluate(policy: StrategyPolicy, scenarios: list[Scenario]) -> dict[str, Any]:
    outcomes = [simulate(scenario, policy.choose(scenario.role, scenario.observation())) for scenario in scenarios]
    feasible = [result for result in outcomes if result["feasible"]]
    fraud = [result for result in outcomes if result["fraud"]]
    infeasible = [result for result in outcomes if not result["feasible"] and not result["fraud"]]
    return {
        "mode": "synthetic_surrogate", "episodes": len(outcomes),
        "mean_reward": round(sum(result["reward"] for result in outcomes) / len(outcomes), 6),
        "mean_calls": round(sum(result["calls"] for result in outcomes) / len(outcomes), 6),
        "feasible_episodes": len(feasible),
        "feasible_deal_rate": round(sum(result["reason"] == "deal" for result in feasible) / max(1, len(feasible)), 6),
        "infeasible_episodes": len(infeasible),
        "legitimate_no_deal_rate": round(sum(result["reason"] == "no_deal" for result in infeasible) / max(1, len(infeasible)), 6),
        "fraud_episodes": len(fraud),
        "fraud_block_rate": round(sum(result["reason"] == "fraud_blocked" for result in fraud) / max(1, len(fraud)), 6),
        "constraint_blocks": sum(result["reason"] == "constraint_blocked" for result in outcomes),
        "executed_constraint_violations": sum(result["executed_constraint_violation"] for result in outcomes),
        "false_walkaways": sum(result["feasible"] and result["reason"] != "deal" for result in outcomes),
    }


def train(seed: int = 42, episodes: int = 5000, holdout_episodes: int = 800) -> tuple[dict[str, Any], dict[str, Any]]:
    if episodes < 100 or holdout_episodes < 20:
        raise ValueError("Use at least 100 training and 20 holdout episodes")
    rng = random.Random(seed)
    values: dict[str, dict[str, float]] = {}
    counts: dict[str, dict[str, int]] = {}
    total_reward = 0.0
    # Contextual bandit: each selected strategy receives a whole episode return;
    # incremental sample means update action values (not hand-coded strategy maps).
    for index in range(episodes):
        scenario = sample_scenario(rng)
        key = context_key(scenario.role, scenario.observation())
        row = values.setdefault(key, {action: 0.0 for action in ACTIONS})
        visits = counts.setdefault(key, {action: 0 for action in ACTIONS})
        epsilon = 0.9 - 0.75 * index / max(1, episodes - 1)
        action = rng.choice(ACTIONS) if rng.random() < epsilon else max(ACTIONS, key=lambda option: row[option])
        result = simulate(scenario, action)
        visits[action] += 1
        row[action] += (result["reward"] - row[action]) / visits[action]
        total_reward += result["reward"]
    candidate = {
        "schema_version": 1, "version": f"synthetic-bandit-s{seed}-n{episodes}",
        "algorithm": "tabular_contextual_bandit", "q_values": values,
        "metadata": {"synthetic": True, "seed": seed, "episodes": episodes,
                     "state_action_visits": counts, "promoted": False,
                     "runtime_scope": "Strategy/prompt selection only; actual harness decides all trade actions",
                     "limitations": "Surrogate price formulas; no evidence of actual-harness improvement"},
    }
    # Separate PRNG and disjoint seed: holdout cases are never used to update Q.
    holdout_seed = seed ^ 0x5A17C9E3
    holdout_rng = random.Random(holdout_seed)
    cases = [sample_scenario(holdout_rng) for _ in range(holdout_episodes)]
    # Evaluate via the same frozen loader used at runtime, without installing it.
    import tempfile
    with tempfile.TemporaryDirectory(prefix="a2a-bandit-") as directory:
        path = Path(directory) / "candidate.json"
        path.write_text(json.dumps(candidate, sort_keys=True))
        candidate_metrics = evaluate(StrategyPolicy(path), cases)
        baseline_path = Path(directory) / "baseline.json"
        baseline_path.write_text(json.dumps(BASELINE, sort_keys=True))
        baseline_metrics = evaluate(StrategyPolicy(baseline_path), cases)
    report = {"mode": "synthetic_surrogate", "algorithm": "tabular_contextual_bandit",
              "seed": seed, "episodes": episodes, "state_count": len(values),
              "parameter_updates": episodes, "training_mean_reward": round(total_reward / episodes, 6),
              "holdout_seed": holdout_seed, "paired_holdout": True,
              "baseline": baseline_metrics, "candidate": candidate_metrics,
              "actual_harness_evaluated": False, "promoted": False,
              "limitations": "Synthetic metrics only. Independent actual-harness evaluation, guard/adversarial tests and the current official 21 results are required before promotion."}
    return candidate, report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, epilog=(
        "Never auto-promotes. promote_policy requires validation JSON with policy_sha256, policy_version, "
        "actual_harness_holdout {mode:actual_harness, independent:true, passed:true, cases:[{passed:true}]}, "
        "guard_tests and adversarial_tests {passed:true, cases:[{passed:true}]}, official_policy_sha256, "
        "official_report_sha256. Official report must match the current kit SHA and agent.json commands."))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--episodes", type=int, default=5000)
    parser.add_argument("--holdout-episodes", type=int, default=800)
    args = parser.parse_args()
    if args.output.resolve() == args.report.resolve():
        parser.error("Candidate and report paths must differ")
    candidate, report = train(args.seed, args.episodes, args.holdout_episodes)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(candidate, indent=2, sort_keys=True) + "\n")
    report["policy_sha256"] = hashlib.sha256(args.output.read_bytes()).hexdigest()
    report["policy_version"] = candidate["version"]
    args.report.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"mode": report["mode"], "updates": args.episodes,
                      "baseline": report["baseline"], "candidate": report["candidate"],
                      "promoted": False}, sort_keys=True))


if __name__ == "__main__":
    main()
