"""Compare B-original against inventory-capped Seller and quality-aware Buyer variants."""
from __future__ import annotations

import csv
import json
import math
import statistics
from pathlib import Path
from typing import Any

from .runner import aggregate, code_version, numeric_summary
from .simulator import rows_as_dicts, run_buyer_episode, run_seller_episode
from .strategies import DiscountBuyer, DiscountSeller, QualityDiscountBuyer, SafeDiscountSeller1, SafeDiscountSeller2

SELLERS = {
    "B-original": DiscountSeller,
    "B-safe-1": SafeDiscountSeller1,
    "B-safe-2": SafeDiscountSeller2,
}
BUYERS = {"B-original": DiscountBuyer, "B-quality": QualityDiscountBuyer}


def paired(rows: list[dict[str, Any]], original: str, candidate: str, metric: str) -> dict[str, float]:
    by = {(row["strategy"], row["seed"], row.get("stress", "base")): float(row[metric]) for row in rows}
    diffs = [by[(candidate, seed, stress)] - value for (strategy, seed, stress), value in by.items()
             if strategy == original and (candidate, seed, stress) in by]
    mean = statistics.fmean(diffs)
    sd = statistics.stdev(diffs) if len(diffs) > 1 else 0
    half = 1.96 * sd / math.sqrt(len(diffs)) if diffs else 0
    return {"mean_difference": mean, "ci95_low": mean - half, "ci95_high": mean + half,
            "min_difference": min(diffs), "max_difference": max(diffs), "pairs": len(diffs)}


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    keys = sorted({key for row in rows for key in row})
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=keys)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: json.dumps(v, sort_keys=True) if isinstance(v, dict) else v for k, v in row.items()})


def run_base(output: Path) -> dict[str, Any]:
    if output.exists():
        raise FileExistsError(output)
    output.mkdir(parents=True)
    seeds, attempts = list(range(401, 411)), 50
    attempts_rows, summaries = [], []
    for name, cls in SELLERS.items():
        for seed in seeds:
            strategy = cls(); strategy.code = name
            rows, summary = run_seller_episode(strategy, seed, attempts)
            attempts_rows += rows_as_dicts(rows); summaries.append(summary)
    for name, cls in BUYERS.items():
        for seed in seeds:
            strategy = cls(); strategy.code = name
            rows, summary = run_buyer_episode(strategy, seed, attempts)
            attempts_rows += rows_as_dicts(rows); summaries.append(summary)
    seller_rows = [r for r in summaries if r["role"] == "seller"]
    buyer_rows = [r for r in summaries if r["role"] == "buyer"]
    result = {
        "status": "offline_mock_only", "code_version": code_version(), "seeds": seeds, "attempts_per_seed": attempts,
        "seller_attempts": 1500, "buyer_attempts": 1000, "total_attempts": 2500,
        "seller": aggregate("seller", seller_rows), "buyer": aggregate("buyer", buyer_rows),
        "paired": {
            name: {metric: paired(seller_rows, "B-original", name, metric)
                   for metric in ("balance_profit", "revenue", "success_rate", "remaining_stock_cost")}
            for name in ("B-safe-1", "B-safe-2")
        } | {"B-quality": {metric: paired(buyer_rows, "B-original", "B-quality", metric)
                            for metric in ("surplus", "success_rate", "average_price")}},
        "official_percentiles": None, "real_stars": None, "real_forum_reputation": None,
    }
    write_csv(output / "attempts.csv", attempts_rows); write_csv(output / "seed_summaries.csv", summaries)
    (output / "summary.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    return result


def run_stress(output: Path, safe_seller: type, seller_name: str) -> dict[str, Any]:
    if output.exists():
        raise FileExistsError(output)
    output.mkdir(parents=True)
    stresses = ("low_demand", "competitor_discount", "high_break", "short_food")
    seeds, attempts = list(range(501, 506)), 20
    attempts_rows, summaries = [], []
    for stress in stresses:
        for name, cls in (("B-original", DiscountSeller), (seller_name, safe_seller)):
            for seed in seeds:
                strategy = cls(); strategy.code = name
                rows, summary = run_seller_episode(strategy, seed, attempts, stress=stress)
                attempts_rows += rows_as_dicts(rows); summaries.append(summary)
        for name, cls in BUYERS.items():
            for seed in seeds:
                strategy = cls(); strategy.code = name
                rows, summary = run_buyer_episode(strategy, seed, attempts, stress=stress)
                attempts_rows += rows_as_dicts(rows); summaries.append(summary)
    seller_rows = [r for r in summaries if r["role"] == "seller"]
    buyer_rows = [r for r in summaries if r["role"] == "buyer"]
    result = {
        "status": "offline_mock_only", "stresses": list(stresses), "seeds": seeds, "attempts_per_seed": attempts,
        "total_attempts": 1600, "seller": aggregate("seller", seller_rows), "buyer": aggregate("buyer", buyer_rows),
        "seller_by_stress": {stress: aggregate("seller", [r for r in seller_rows if r["stress"] == stress]) for stress in stresses},
        "buyer_by_stress": {stress: aggregate("buyer", [r for r in buyer_rows if r["stress"] == stress]) for stress in stresses},
    }
    write_csv(output / "attempts.csv", attempts_rows); write_csv(output / "seed_summaries.csv", summaries)
    (output / "summary.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    return result


def run_seller_stress_candidate(output: Path, seller: type, seller_name: str) -> dict[str, Any]:
    """Use the remaining 400-attempt stress budget for one additional Seller candidate."""
    if output.exists():
        raise FileExistsError(output)
    output.mkdir(parents=True)
    stresses = ("low_demand", "competitor_discount", "high_break", "short_food")
    seeds, attempts = list(range(501, 506)), 20
    attempt_rows, summaries = [], []
    for stress in stresses:
        for seed in seeds:
            strategy = seller(); strategy.code = seller_name
            rows, summary = run_seller_episode(strategy, seed, attempts, stress=stress)
            attempt_rows += rows_as_dicts(rows); summaries.append(summary)
    result = {"status": "offline_mock_only", "strategy": seller_name, "stresses": list(stresses),
              "seeds": seeds, "attempts_per_seed": attempts, "total_attempts": 400,
              "seller": aggregate("seller", summaries),
              "seller_by_stress": {s: aggregate("seller", [r for r in summaries if r["stress"] == s])
                                   for s in stresses}}
    write_csv(output / "attempts.csv", attempt_rows); write_csv(output / "seed_summaries.csv", summaries)
    (output / "summary.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    return result


def main() -> None:
    root = Path(__file__).parents[1] / "results"
    base = run_base(root / "b_improvement")
    # Select a safe candidate by mean profit, then stress it; selection is recorded, not hidden.
    safe_name = max(("B-safe-1", "B-safe-2"), key=lambda name: base["seller"][name]["balance_profit"]["mean"])
    safe_cls = SELLERS[safe_name]
    stress = run_stress(root / "b_improvement_stress", safe_cls, safe_name)
    final = {"base": base, "stress": stress, "selected_safe_for_stress": safe_name,
             "base_attempts": 2500, "stress_attempts": 1600, "external_llm_calls": 0, "network_calls": 0}
    (root / "B_IMPROVEMENT_ANALYSIS.json").write_text(json.dumps(final, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"selected_safe_for_stress": safe_name, "base_attempts": 2500, "stress_attempts": 1600}))


if __name__ == "__main__":
    main()

