"""Create the final evidence report from preserved phase outputs without rerunning trades."""
from __future__ import annotations

import csv
import json
from collections import Counter
from pathlib import Path
from typing import Any


def load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def failures(path: Path, role: str) -> dict[str, dict[str, int]]:
    result: dict[str, Counter] = {}
    with path.open(encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            if row["role"] != role or row["status"] == "simulated_settled":
                continue
            result.setdefault(row["strategy"], Counter())[row["reason"]] += 1
    return {strategy: dict(counter.most_common()) for strategy, counter in sorted(result.items())}


def adaptive_halves(path: Path, role: str) -> dict[str, float]:
    rows = []
    with path.open(encoding="utf-8") as handle:
        rows = [row for row in csv.DictReader(handle) if row["role"] == role and row["strategy"] == "D"]
    answer = {}
    for label, flag in (("early", "True"), ("late", "False")):
        half = [row for row in rows if row["early_half"] == flag]
        answer[f"{label}_success_rate"] = sum(row["status"] == "simulated_settled" for row in half) / len(half)
    return answer


def build(results: Path) -> dict[str, Any]:
    phase1 = load(results / "phase1" / "summary.json")
    phase2 = load(results / "phase2" / "summary.json")
    phase3 = load(results / "phase3_buyer" / "summary.json")
    return {
        "status": "offline_mock_only",
        "attempt_accounting": {
            "preserved_initial_failed_safety_diagnostic": 800,
            "valid_phase1": phase1["total_attempts"],
            "valid_phase2": phase2["total_attempts"],
            "valid_phase3_buyer_only": phase3["total_attempts"],
            "valid_total": phase1["total_attempts"] + phase2["total_attempts"] + phase3["total_attempts"],
            "all_executed_including_preserved_diagnostic": 800 + phase1["total_attempts"] + phase2["total_attempts"] + phase3["total_attempts"],
        },
        "seller_phase2": phase2["seller"],
        "buyer_phase2": phase2["buyer"],
        "buyer_phase3": phase3["buyer"],
        "seller_failure_reasons_phase2": failures(results / "phase2" / "attempts.csv", "seller"),
        "buyer_failure_reasons_phase3": failures(results / "phase3_buyer" / "attempts.csv", "buyer"),
        "adaptive_learning_phase2": {
            "seller": adaptive_halves(results / "phase2" / "attempts.csv", "seller"),
            "buyer": adaptive_halves(results / "phase2" / "attempts.csv", "buyer"),
        },
        "selection": {
            "seller": {
                "mean_performance_candidate": "B",
                "risk_control_alternative": "A",
                "reason": "B has the highest mean cash profit and its phase-2 CI clears A; A has the smaller worst-seed loss.",
            },
            "buyer": {
                "mean_performance_candidate": "B",
                "risk_control_alternative": "C",
                "reason": "B has higher phase-3 mean surplus with non-overlapping CI; C has the better worst-seed surplus.",
            },
        },
        "official_score": None,
        "real_stars": None,
        "real_forum_reputation": None,
    }


def markdown(data: dict[str, Any]) -> str:
    seller = data["seller_phase2"]
    buyer = data["buyer_phase3"]
    lines = [
        "# Final offline strategy evidence", "",
        f"Valid attempts: {data['attempt_accounting']['valid_total']}; all executed including the preserved failed diagnostic: "
        f"{data['attempt_accounting']['all_executed_including_preserved_diagnostic']}.",
        "Official leaderboard score, stars and forum reputation are not estimated.", "",
        "## Seller phase 2", "", "| Strategy | Profit mean (95% CI) | Revenue | Success | Worst profit |", "|---|---:|---:|---:|---:|",
    ]
    for code, row in seller.items():
        lines.append(f"| {code} | {row['balance_profit']['mean']:.0f} ({row['balance_profit']['ci95_low']:.0f}, "
                     f"{row['balance_profit']['ci95_high']:.0f}) | {row['revenue']['mean']:.0f} | "
                     f"{row['success_rate']['mean']:.3f} | {row['balance_profit']['min']:.0f} |")
    lines += ["", "## Buyer phase 3", "", "| Strategy | Surplus mean (95% CI) | Success | Average price | Worst surplus |", "|---|---:|---:|---:|---:|"]
    for code, row in buyer.items():
        lines.append(f"| {code} | {row['surplus']['mean']:.0f} ({row['surplus']['ci95_low']:.0f}, "
                     f"{row['surplus']['ci95_high']:.0f}) | {row['success_rate']['mean']:.3f} | "
                     f"{row['average_price']['mean']:.0f} | {row['surplus']['min']:.0f} |")
    lines += [
        "", "## Selection", "",
        "- Seller B is the mean-performance candidate; Seller A is the downside-risk alternative.",
        "- Buyer B is the mean-surplus candidate; Buyer C is the downside-risk alternative.",
        "- These are Mock candidates, not claims about the official leaderboard.", "",
        "## Adaptive strategy", "",
        f"- Seller D early/late settlement rate: {data['adaptive_learning_phase2']['seller']['early_success_rate']:.3f} / "
        f"{data['adaptive_learning_phase2']['seller']['late_success_rate']:.3f}.",
        f"- Buyer D early/late settlement rate: {data['adaptive_learning_phase2']['buyer']['early_success_rate']:.3f} / "
        f"{data['adaptive_learning_phase2']['buyer']['late_success_rate']:.3f}.",
        "The current adaptive rules did not demonstrate a reliable learning improvement and should not be selected as-is.", "",
    ]
    return "\n".join(lines)


def main() -> None:
    results = Path(__file__).parents[1] / "results"
    data = build(results)
    (results / "FINAL_ANALYSIS.json").write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                                                  encoding="utf-8")
    (results / "FINAL_REPORT.md").write_text(markdown(data), encoding="utf-8")
    print(json.dumps(data["selection"], ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()

