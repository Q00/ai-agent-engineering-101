"""Run fair A/B/C/D phases and save complete CSV/JSON evidence."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import statistics
from pathlib import Path
from typing import Any, Iterable

from .simulator import rows_as_dicts, run_buyer_episode, run_seller_episode, world_fingerprint
from .strategies import BUYER_STRATEGIES, SELLER_STRATEGIES


def code_version() -> str:
    digest = hashlib.sha256()
    for path in sorted(Path(__file__).parent.glob("*.py")):
        digest.update(path.name.encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()[:16]


def numeric_summary(rows: list[dict[str, Any]], metric: str) -> dict[str, float]:
    values = [float(row[metric]) for row in rows]
    mean = statistics.fmean(values)
    stdev = statistics.stdev(values) if len(values) > 1 else 0.0
    half = 1.96 * stdev / math.sqrt(len(values)) if values else 0.0
    return {"mean": mean, "stdev": stdev, "ci95_low": mean - half, "ci95_high": mean + half,
            "min": min(values), "max": max(values)}


def aggregate(role: str, summaries: list[dict[str, Any]]) -> dict[str, Any]:
    metrics = (
        ("balance_profit", "revenue", "settled", "success_rate", "inventory_turnover", "expired_loss",
         "storage_cost", "warranty_refunds", "remaining_stock_cost", "inventory_purchase_cost",
         "stockout_failures", "safety_violations")
        if role == "seller" else
        ("surplus", "settled", "success_rate", "average_price", "quality_failures", "refunds",
         "safety_violations", "early_success_rate", "late_success_rate")
    )
    result = {}
    for strategy in sorted({row["strategy"] for row in summaries}):
        subset = [row for row in summaries if row["strategy"] == strategy]
        result[strategy] = {"seeds": len(subset), **{metric: numeric_summary(subset, metric) for metric in metrics}}
    return result


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    keys = sorted({key for row in rows for key in row})
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=keys)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: json.dumps(value, ensure_ascii=False, sort_keys=True) if isinstance(value, dict) else value
                             for key, value in row.items()})


def run_phase(output: Path, phase: str, seeds: Iterable[int], attempts: int, max_turns: int = 3,
              seller_codes: str = "ABCD", buyer_codes: str = "ABCD") -> dict[str, Any]:
    if output.exists():
        raise FileExistsError(f"refusing to overwrite {output}")
    output.mkdir(parents=True)
    seeds = list(seeds)
    attempt_rows: list[dict[str, Any]] = []
    seed_rows: list[dict[str, Any]] = []
    fingerprints = {str(seed): world_fingerprint(seed, attempts) for seed in seeds}
    for code in seller_codes:
        for seed in seeds:
            rows, summary = run_seller_episode(SELLER_STRATEGIES[code](), seed, attempts, max_turns)
            attempt_rows.extend(rows_as_dicts(rows))
            seed_rows.append(summary)
    for code in buyer_codes:
        for seed in seeds:
            rows, summary = run_buyer_episode(BUYER_STRATEGIES[code](), seed, attempts, max_turns)
            attempt_rows.extend(rows_as_dicts(rows))
            seed_rows.append(summary)
    seller_rows = [row for row in seed_rows if row["role"] == "seller"]
    buyer_rows = [row for row in seed_rows if row["role"] == "buyer"]
    result = {
        "phase": phase, "status": "offline_mock_only", "code_version": code_version(),
        "seeds": seeds, "attempts_per_seed": attempts, "max_negotiation_turns": max_turns,
        "seller_strategies": list(seller_codes), "buyer_strategies": list(buyer_codes),
        "seller_attempts": len(seller_codes) * len(seeds) * attempts,
        "buyer_attempts": len(buyer_codes) * len(seeds) * attempts,
        "total_attempts": len(attempt_rows), "world_fingerprints": fingerprints,
        "seller": aggregate("seller", seller_rows), "buyer": aggregate("buyer", buyer_rows),
        "official_percentiles": None, "real_stars": None, "real_forum_reputation": None,
        "mock_assumptions": {
            "goods_break_probability": 0.15,
            "competitor_behavior": "deterministic seeded Mock offers and concessions",
            "demand_weights": "deterministic seeded Mock demand shift",
        },
    }
    _write_csv(output / "attempts.csv", attempt_rows)
    _write_csv(output / "seed_summaries.csv", seed_rows)
    (output / "summary.json").write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                                         encoding="utf-8")
    (output / "config.json").write_text(json.dumps({
        "phase": phase, "code_version": result["code_version"], "seeds": seeds, "attempts": attempts,
        "max_turns": max_turns, "seller_codes": seller_codes, "buyer_codes": buyer_codes,
        "external_llm_calls": 0, "network_calls": 0,
    }, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (output / "REPORT.md").write_text(markdown_report(result), encoding="utf-8")
    return result


def markdown_report(result: dict[str, Any]) -> str:
    lines = [
        f"# {result['phase']} strategy comparison", "",
        f"Offline attempts: {result['total_attempts']} (Seller {result['seller_attempts']}, Buyer {result['buyer_attempts']}).",
        "Official percentiles, stars and forum reputation are not estimated.", "",
        "## Seller", "", "| Strategy | Profit mean | Revenue mean | Settled mean | Success | Worst profit |", "|---|---:|---:|---:|---:|---:|",
    ]
    for code, row in result["seller"].items():
        lines.append(f"| {code} | {row['balance_profit']['mean']:.1f} | {row['revenue']['mean']:.1f} | "
                     f"{row['settled']['mean']:.2f} | {row['success_rate']['mean']:.3f} | {row['balance_profit']['min']:.1f} |")
    lines += ["", "## Buyer", "", "| Strategy | Surplus mean | Settled mean | Success | Average price | Quality rejects |", "|---|---:|---:|---:|---:|---:|"]
    for code, row in result["buyer"].items():
        lines.append(f"| {code} | {row['surplus']['mean']:.1f} | {row['settled']['mean']:.2f} | "
                     f"{row['success_rate']['mean']:.3f} | {row['average_price']['mean']:.1f} | "
                     f"{row['quality_failures']['mean']:.2f} |")
    lines += ["", "All metrics are Mock evidence, not an official leaderboard score.", ""]
    return "\n".join(lines)


def top_and_separation(summary: dict[str, Any], role: str) -> tuple[list[str], bool]:
    metric = "balance_profit" if role == "seller" else "surplus"
    ranked = sorted(summary[role], key=lambda code: summary[role][code][metric]["mean"], reverse=True)
    if len(ranked) < 2:
        return ranked, False
    first, second = summary[role][ranked[0]][metric], summary[role][ranked[1]][metric]
    separated = first["ci95_low"] > second["ci95_high"]
    return ranked[:2], separated


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--phase", required=True)
    parser.add_argument("--seeds", required=True, help="comma-separated integers")
    parser.add_argument("--attempts", type=int, required=True)
    parser.add_argument("--max-turns", type=int, default=3)
    parser.add_argument("--seller-codes", default="ABCD")
    parser.add_argument("--buyer-codes", default="ABCD")
    args = parser.parse_args(argv)
    result = run_phase(args.output, args.phase, [int(x) for x in args.seeds.split(",")], args.attempts,
                       args.max_turns, args.seller_codes, args.buyer_codes)
    print(json.dumps({
        "phase": result["phase"], "total_attempts": result["total_attempts"],
        "seller_top": top_and_separation(result, "seller") if result["seller"] else None,
        "buyer_top": top_and_separation(result, "buyer") if result["buyer"] else None,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

