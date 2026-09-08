"""Print measurements for the report; interpretation is written by the student."""
import csv
import json
from pathlib import Path
from statistics import mean, variance

from run_ab import HEADER

ROOT = Path(__file__).resolve().parent
CONTROLS = ("provider", "base_url", "model", "task", "expected", "tools", "prompts",
            "max_steps", "max_replan", "max_tool_rounds", "max_tokens", "temperature",
            "timeout_seconds", "sdk_retries", "seed", "python", "openai", "sha256")


def summarize(root=ROOT):
    with (root / "results.csv").open(encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames != HEADER:
            raise ValueError("Unexpected results.csv header")
        rows = list(reader)
    if not rows:
        raise ValueError("No live measurements yet. Run run_ab.py --runs 3 first.")
    fingerprints = set()
    for row in rows:
        path = root / "logs" / f"{row['harness']}-{int(row['run']):02d}.txt"
        with path.open(encoding="utf-8") as stream:
            first = stream.readline()
        if not first.startswith("[conditions] "):
            raise ValueError(f"Missing experiment conditions: {path.name}")
        config = json.loads(first.removeprefix("[conditions] "))
        fingerprints.add(json.dumps({key: config[key] for key in CONTROLS}, sort_keys=True))
    if len(fingerprints) != 1:
        raise ValueError("Experiment conditions differ. Keep the rows and compare each condition separately.")
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
    print("\nVariance is sample variance (n - 1). All failed runs are included.")


if __name__ == "__main__":
    try:
        summarize()
    except (OSError, ValueError, KeyError) as error:
        raise SystemExit(str(error))
