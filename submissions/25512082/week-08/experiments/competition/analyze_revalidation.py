"""Analyze saved revalidation evidence without running any new trades."""
from __future__ import annotations

import csv
import json
import math
import statistics
from pathlib import Path
from typing import Any


def paired(rows: list[dict[str, str]], role: str, left: str, right: str, metric: str) -> dict[str, Any]:
    values = {(r["strategy"], int(r["seed"])): float(r[metric]) for r in rows if r["role"] == role}
    seeds = sorted(seed for strategy, seed in values if strategy == left and (right, seed) in values)
    diffs = [values[(left, seed)] - values[(right, seed)] for seed in seeds]
    mean = statistics.fmean(diffs)
    sd = statistics.stdev(diffs) if len(diffs) > 1 else 0.0
    half = 1.96 * sd / math.sqrt(len(diffs))
    return {"left": left, "right": right, "metric": metric, "pairs": len(diffs),
            "mean_difference": mean, "ci95_low": mean - half, "ci95_high": mean + half,
            "left_wins": sum(value > 0 for value in diffs), "differences": diffs}


def compact(summary: dict[str, Any], role: str) -> list[dict[str, Any]]:
    metric = "balance_profit" if role == "seller" else "surplus"
    rows = []
    for strategy, data in summary[role].items():
        row = {"strategy": strategy, "mean_primary": data[metric]["mean"],
               "ci95_low": data[metric]["ci95_low"], "ci95_high": data[metric]["ci95_high"],
               "worst": data[metric]["min"], "success_rate": data["success_rate"]["mean"]}
        if role == "seller":
            for key in ("revenue", "inventory_purchase_cost", "storage_cost", "expired_loss",
                        "warranty_refunds", "remaining_stock_cost"):
                row[key] = data[key]["mean"]
        else:
            row["average_price"] = data["average_price"]["mean"]
            row["quality_failures"] = data["quality_failures"]["mean"]
        rows.append(row)
    return sorted(rows, key=lambda row: row["mean_primary"], reverse=True)


def main() -> None:
    results = Path(__file__).parents[1] / "results"
    stage1 = json.loads((results / "revalidation_abcd_20x100" / "summary.json").read_text())
    stage2 = json.loads((results / "revalidation_top_30x200" / "summary.json").read_text())
    improvement = json.loads((results / "b_improvement" / "summary.json").read_text())
    with (results / "revalidation_abcd_20x100" / "seed_summaries.csv").open() as handle:
        seed_rows = list(csv.DictReader(handle))
    analysis = {
        "status": "analysis_of_saved_offline_evidence",
        "new_trade_attempts": 0,
        "evidence_attempts": {"stage1": stage1["total_attempts"], "stage2": stage2["total_attempts"],
                              "total": stage1["total_attempts"] + stage2["total_attempts"]},
        "stage1": {"seller_ranking": compact(stage1, "seller"), "buyer_ranking": compact(stage1, "buyer")},
        "stage2": {"seller_B": compact(stage2, "seller")[0], "buyer_B": compact(stage2, "buyer")[0]},
        "paired": {"seller_B_minus_A": paired(seed_rows, "seller", "B", "A", "balance_profit"),
                   "buyer_B_minus_C": paired(seed_rows, "buyer", "B", "C", "surplus")},
        "b_safe_same_seed_experiment": improvement["paired"]["B-safe-1"],
        "selection": {"seller": "B-safe-1 operational candidate", "buyer": "B-original with kit safety",
                      "note": "B-safe-1 was compared in a separate 10-seed x 50-attempt paired experiment; do not pool it with the 28,000-attempt stages."},
        "official_percentiles": None, "real_stars": None, "real_forum_reputation": None,
    }
    (results / "REVALIDATION_ANALYSIS.json").write_text(json.dumps(analysis, indent=2, sort_keys=True) + "\n")
    fields = sorted({key for role in ("seller_ranking", "buyer_ranking") for row in analysis["stage1"][role] for key in row})
    with (results / "REVALIDATION_RANKINGS.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["role"] + fields)
        writer.writeheader()
        for role in ("seller", "buyer"):
            for row in analysis["stage1"][role + "_ranking"]:
                writer.writerow({"role": role, **row})
    print(json.dumps({"evidence_attempts": 28000, "new_trade_attempts": 0}))


if __name__ == "__main__":
    main()
