"""Print measurements for the report; interpretation is written by the student.

python summarize_results.py                 one condition only; refuses to pool
python summarize_results.py --by-condition  one section per condition
"""
import argparse
import csv
import json
from pathlib import Path
from statistics import mean, variance

from run_ab import HEADER

ROOT = Path(__file__).resolve().parent
CONTROLS = ("provider", "base_url", "model", "task", "expected", "tools", "prompts",
            "plan_prompt", "plan_parser",
            "max_steps", "max_replan", "max_tool_rounds", "max_tokens", "temperature",
            "timeout_seconds", "sdk_retries", "seed", "python", "openai", "sha256")
# Runs 1-6 predate the named conditions. They used the v1 prompt with the
# strict parser, so those are the defaults a log without the keys stands for.
CONTROL_DEFAULTS = {"plan_prompt": "v1", "plan_parser": "strict"}


def load(root=ROOT):
    """Return the result rows paired with the condition each one ran under."""
    with (root / "results.csv").open(encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames != HEADER:
            raise ValueError("Unexpected results.csv header")
        rows = list(reader)
    if not rows:
        raise ValueError("No live measurements yet. Run run_ab.py --runs 3 first.")
    paired = []
    for row in rows:
        path = root / "logs" / f"{row['harness']}-{int(row['run']):02d}.txt"
        with path.open(encoding="utf-8") as stream:
            first = stream.readline()
        if not first.startswith("[conditions] "):
            raise ValueError(f"Missing experiment conditions: {path.name}")
        config = json.loads(first.removeprefix("[conditions] "))
        fingerprint = json.dumps(
            {key: config.get(key, CONTROL_DEFAULTS.get(key)) for key in CONTROLS},
            sort_keys=True)
        paired.append((row, fingerprint, config))
    return paired


def label(config):
    """The short name of a condition, for section headings."""
    return (f"plan_prompt={config.get('plan_prompt', CONTROL_DEFAULTS['plan_prompt'])} "
            f"plan_parser={config.get('plan_parser', CONTROL_DEFAULTS['plan_parser'])}")


def tables(rows):
    print("| " + " | ".join(HEADER) + " |")
    print("| " + " | ".join(["---"] * len(HEADER)) + " |")
    for row in rows:
        print("| " + " | ".join(row[key].replace("|", "\\|").replace("\n", " ") for key in HEADER) + " |")
    print("\n| harness | n | success rate | tokens mean / variance | iters mean / variance | interventions mean / variance |")
    print("| --- | --- | --- | --- | --- | --- |")
    for harness in ("react", "plan_exec"):
        group = [row for row in rows if row["harness"] == harness]
        if not group:
            continue
        cells = []
        for key in ("tokens", "iters", "interventions"):
            values = [int(row[key]) for row in group]
            spread = f"{variance(values):.2f}" if len(values) > 1 else "N/A"
            cells.append(f"{mean(values):.2f} / {spread}")
        wins = sum(row["success"] == "O" for row in group)
        print(f"| {harness} | {len(group)} | {wins}/{len(group)} ({wins / len(group):.1%}) | " + " | ".join(cells) + " |")


def summarize(root=ROOT, by_condition=False):
    paired = load(root)
    fingerprints = {fingerprint for _, fingerprint, _ in paired}
    if len(fingerprints) != 1 and not by_condition:
        raise ValueError("Experiment conditions differ. Keep the rows and compare each "
                         "condition separately, or pass --by-condition.")
    if not by_condition:
        tables([row for row, _, _ in paired])
    else:
        # Sections in first-appearance order, so run numbers stay ascending.
        seen = []
        for _, fingerprint, config in paired:
            if fingerprint not in [f for f, _ in seen]:
                seen.append((fingerprint, config))
        for fingerprint, config in seen:
            group = [row for row, f, _ in paired if f == fingerprint]
            runs = ", ".join(row["run"] for row in group)
            print(f"\n### condition: {label(config)}  (runs {runs})\n")
            tables(group)
        print(f"\n{len(seen)} condition(s). Rows are never averaged across conditions.")
    print("\nVariance is sample variance (n - 1). All failed runs are included.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Measurements for the report")
    parser.add_argument("--by-condition", action="store_true",
                        help="print one section per experiment condition instead of refusing")
    options = parser.parse_args()
    try:
        summarize(by_condition=options.by_condition)
    except (OSError, ValueError, KeyError) as error:
        raise SystemExit(str(error))
