"""Index existing market evidence without changing logs or inventing new runs.

Run from week-07:
    .venv/bin/python collect_submission_evidence.py --snapshot logs/preflight-market-20261026T224624
Outputs are derived analyses, not original market or model output.
"""
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import shutil
import uuid


ROOT = Path(__file__).resolve().parent


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def reference(path):
    return {"path": str(path.relative_to(ROOT)),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def save(path, data):
    with path.open("x", encoding="utf-8") as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--market-log", type=Path, default=ROOT / "logs/market-kit.log",
                        help="Original market log to analyze, including a preserved historical copy")
    parser.add_argument("--memory", type=Path, default=ROOT / "data/market-memory.jsonl",
                        help="Memory snapshot belonging to the observation period")
    args = parser.parse_args()
    snapshot = args.snapshot.resolve()
    if not snapshot.is_relative_to(ROOT / "logs"):
        parser.error("snapshot must be an existing directory inside this submission's logs")
    kit = args.market_log.resolve()
    memory = args.memory.resolve()
    for source in (kit, memory):
        if not source.is_relative_to(ROOT) or not source.is_file():
            parser.error("market log and memory must be existing files inside this submission")
    run = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    common = {"kind": "derived analysis of preserved evidence; not a new market experiment",
              "generated_at": datetime.now(timezone.utc).isoformat()}
    rounds = []
    for number in range(3):
        source = snapshot / f"board-round-{number}.json"
        board = read(source)
        shop = next(x for x in board["shops"] if x["name"] == "dyishappy")
        buyer = next(x for x in board["buyers"] if x["name"] == "dyishappy")
        total = sum(x["deals"] for x in board["shops"])
        rounds.append({"round": number, "source": reference(source),
                       "shop": shop, "buyer": buyer, "all_shop_deals": total,
                       "computed_share": shop["deals"] / total if total else None,
                       "mean_sale_price": shop["revenue"] / shop["deals"] if shop["deals"] else None})
    baseline = ROOT / "logs/owner-value-baseline-20261020.json"
    first = ROOT / "logs/challenge-1" / ("week07-evidence-" + run)
    first.mkdir(parents=True)
    save(first / "analysis.json", {
        **common, "baseline_source": reference(baseline), "baseline": read(baseline),
        "rounds": rounds,
        "limits": [
            "The Round 1 pre-change baseline has zero sales, so its mean sale price is undefined, not zero.",
            "Rounds have different items, buyers, rules and observation lengths; their averages do not establish a price-policy effect.",
            "The Round 2 snapshot includes market activity after our agents stopped, so its share is diluted by downtime.",
            "The existing policy changed alongside signing, public proxy and description changes; no controlled price-only experiment is claimed.",
        ],
    })
    second = ROOT / "logs/challenge-2" / ("week07-evidence-" + run)
    second.mkdir(parents=True)
    lines = kit.read_text(encoding="utf-8").splitlines()
    encounters = []
    for index, line in enumerate(lines):
        if "[kit] shop for this order: maple-home " not in line:
            continue
        start = index - 1
        end = next((i for i in range(index + 1, len(lines)) if "[order " in lines[i]), len(lines))
        selected = [{"line": i + 1, "text": lines[i]} for i in range(start, end)
                    if any(s in lines[i] for s in ("[order ", "shop for this order:",
                                                  "BUYER DECISION:", "BUYER ERROR:",
                                                  "[sign]", "ledger #"))]
        receipts = []
        for i in range(start, end):
            if "HTTP RESPONSE: " not in lines[i]:
                continue
            response = json.loads(lines[i].split("HTTP RESPONSE: ", 1)[1])
            body = json.loads(response["body"])
            result = body.get("result", {})
            task = result.get("task", result)
            if task.get("artifacts"):
                receipts.append({"line": i + 1, "status": task.get("status"),
                                 "artifacts": task["artifacts"]})
        encounters.append({"start_line": start + 1, "end_line": end,
                           "events": selected, "received_artifacts": receipts})
    warnings = []
    for source in sorted((ROOT / "logs").glob("market-select-*.log")):
        text = source.read_text(encoding="utf-8")
        if "VALIDATION: PASS" not in text:
            continue
        try:
            context = next(json.loads(line) for line in text.splitlines()
                           if line.startswith('{"item"') and '"candidates"' in line)
            output = text.split("STDOUT:\n", 1)[1].split("\nSTDERR:", 1)[0].strip()
            output = re.sub(r"^```(?:json)?\s*\n|\n```$", "", output)
            decision = json.loads(output)
        except (StopIteration, IndexError, ValueError):
            continue
        suspect = next((s for s in context["candidates"] if s["name"] == "maple-home"), None)
        if suspect and "maple" in decision.get("reason", "").lower():
            warnings.append({"source": reference(source), "item": context["item"],
                             "budget": context["budget"], "suspect_evidence": suspect,
                             "decision": decision})
    trades = [json.loads(line) for line in memory.read_text().splitlines() if line.strip()]
    save(second / "analysis.json", {
        **common, "original_market_log": reference(kit), "encounters": encounters,
        "selection_decisions_mentioning_maple_home": warnings,
        "memory_source": reference(memory), "memory_outcomes": dict(Counter(t.get("result") for t in trades)),
        "memory_shop_counts": dict(Counter(t["name"] for t in trades)),
        "limits": [
            "Round 0 incidents were not imported into market_buyer memory; later avoidance is explicitly based on reviews and trusted-shop history.",
            "Selection logs include batch comparisons, not necessarily distinct orders or final selections.",
            "The observed mismatched receipts establish inconsistent behavior; they do not reveal an external participant's intent.",
        ],
    })
    # Exact copies of selected original model logs, with original hashes above.
    for evidence in warnings[:3]:
        source = ROOT / evidence["source"]["path"]
        shutil.copyfile(source, second / source.name)
    print(first.relative_to(ROOT))
    print(second.relative_to(ROOT))
    print(f"Indexed {len(encounters)} encounters and {len(warnings)} selection decisions.")


if __name__ == "__main__":
    main()
