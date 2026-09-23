"""Check the submitted CSV against the raw episode outcomes in all nine logs."""
import csv
import json
from collections import Counter
from pathlib import Path

from negotiation import CONDITIONS, score
from run import HEADER

ROOT = Path(__file__).resolve().parent


def main():
    scenarios = {str(x["id"]): x for x in json.loads((ROOT / "scenarios.json").read_text())}
    with (ROOT / "results.csv").open(newline="", encoding="utf-8") as stream:
        reader = csv.DictReader(stream)
        assert reader.fieldnames == HEADER
        rows = list(reader)
    assert len(rows) == 3 * len(CONDITIONS) * len(scenarios), len(rows)
    keys = [(int(row["run"]), row["scenario"]) for row in rows]
    assert len(set(keys)) == len(keys)
    counts = Counter((row["condition"], row["scenario"]) for row in rows)
    assert all(counts[(condition, scenario)] == 3
               for condition in CONDITIONS for scenario in scenarios)
    for row in rows:
        run = int(row["run"])
        condition = CONDITIONS[(run - 1) // 3]
        assert condition == row["condition"] and row["scenario"] in scenarios
        log_path = ROOT / "logs" / f"{condition}-{run:02d}.txt"
        lines = log_path.read_text(encoding="utf-8").splitlines()
        assert lines[0].startswith(f"[run] number={run} condition={condition}")
        current = None
        observed = {}
        for line in lines:
            if line.startswith("[scenario] id="):
                current = line.split()[1].split("=", 1)[1]
            elif line.startswith("[result] "):
                observed[current] = json.loads(line[len("[result] "):])
        result = observed.get(row["scenario"])
        if row["outcome"] == "":
            assert result is None and row["note"].startswith("crash:")
            continue
        assert result is not None
        expected = score(scenarios[row["scenario"]], row["outcome"],
                         int(row["price"]) if row["price"] else None)
        assert expected == (int(row["deal_possible"]), int(row["correct"]),
                            int(row["violation"]))
        for field in ("deal_possible", "outcome", "price", "correct", "violation",
                      "turns", "format_errors", "reader_calls"):
            actual = "" if result[field] is None else str(result[field])
            assert row[field] == actual, (run, row["scenario"], field, row[field], actual)
        assert len([line for line in lines if line.startswith("[reader-raw] ")]) >= (
            result["reader_calls"] if condition == "free" else 0)
        if condition == "structured":
            assert result["reader_calls"] == 0
    print(f"verified {len(rows)} episodes against 9 original run logs")


if __name__ == "__main__":
    main()
