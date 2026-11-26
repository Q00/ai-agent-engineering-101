"""Compare each supplemental CSV row with its corresponding original console log."""

import argparse
import csv
import hashlib
import json
from collections import Counter
from pathlib import Path

from run_followup import BASE, HERE, ORDERS, TERMINATION_POLICY, HEADER


def verify(study):
    with (HERE / f"{study}.csv").open(newline="", encoding="utf-8") as stream:
        reader = csv.DictReader(stream)
        assert reader.fieldnames == HEADER
        rows = list(reader)
    scenario_bytes = (BASE / "scenarios.json").read_bytes()
    scenarios = {str(s["id"]): s for s in json.loads(scenario_bytes)}
    assert len(rows) == 3 * 3 * len(scenarios), len(rows)
    keys = [(int(r["run"]), r["scenario"]) for r in rows]
    assert len(set(keys)) == len(keys)
    counts = Counter((r["condition"], r["scenario"]) for r in rows)
    assert all(counts[(c, s)] == 3 for c in ("free", "tagged", "structured")
               for s in scenarios)
    expected_policy = TERMINATION_POLICY if study == "termination" else ""
    policy_hash = hashlib.sha256(expected_policy.encode()).hexdigest()
    scenario_hash = hashlib.sha256(scenario_bytes).hexdigest()
    for row in rows:
        run = int(row["run"])
        idx = run - (10 if study == "replication" else 19)
        assert 0 <= idx < 9
        block, slot = divmod(idx, 3)
        condition = ORDERS[study][block][slot]
        assert row["condition"] == condition
        lines = (BASE / "logs" / f"{study}-{run:02d}.txt").read_text(
            encoding="utf-8").splitlines()
        assert f"study={study} number={run}" in lines[0]
        assert f"policy_sha256={policy_hash}" in lines[0]
        assert f"scenarios_sha256={scenario_hash}" in lines[0]
        current = None
        result = {}
        read_count = Counter()
        tag_count = Counter()
        parser_count = Counter()
        for line in lines:
            if line.startswith("[scenario] id="):
                current = line.split()[1].split("=", 1)[1]
                # A resumed incomplete attempt may be followed by a new attempt.
                read_count[current] = tag_count[current] = parser_count[current] = 0
            elif line.startswith("[reader-raw] "):
                read_count[current] += 1
            elif line.startswith("[tag] "):
                tag_count[current] += 1
            elif line.startswith("[parser] "):
                parser_count[current] += 1
            elif line.startswith("[result] "):
                result[current] = (json.loads(line[len("[result] "):]),
                                   read_count[current], tag_count[current],
                                   parser_count[current])
        scenario = row["scenario"]
        if not row["outcome"]:
            assert result.get(scenario) is None and row["note"].startswith("crash:")
            continue
        observed, reads, tags, parses = result[scenario]
        for field in ("deal_possible", "outcome", "price", "correct", "violation",
                      "turns", "format_errors", "reader_calls"):
            value = "" if observed[field] is None else str(observed[field])
            assert row[field] == value, (study, run, scenario, field)
        reserve, budget = scenarios[scenario]["reserve"], scenarios[scenario]["budget"]
        assert int(row["deal_possible"]) == int(reserve <= budget)
        if row["outcome"] == "deal":
            price = int(row["price"])
            assert int(row["violation"]) == int(not reserve <= price <= budget)
        if condition == "free":
            assert reads == observed["turns"] and tags == parses == 0
        elif condition == "tagged":
            assert tags == observed["turns"] and reads <= tags and parses == 0
        else:
            assert parses == observed["turns"] and reads == tags == observed["reader_calls"] == 0
        assert observed["reader_calls"] >= reads
    print(f"verified {study}: {len(rows)} episodes, 9 logs, 4 scenarios")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("study", choices=tuple(ORDERS))
    verify(parser.parse_args().study)


if __name__ == "__main__":
    main()
