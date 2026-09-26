"""Extract and audit the Week 04 free/tagged messages without model calls."""

import argparse
import ast
import csv
import json
import re
from collections import Counter
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
HERE = Path(__file__).resolve().parent
INPUT_HEADER = ["run", "condition", "scenario", "turn", "speaker", "message",
                "protocol_act", "protocol_price"]
LABEL_HEADER = ["run", "scenario", "turn", "reference_act", "reference_price", "note"]
MESSAGE = re.compile(r"^\[(buyer|seller) turn=(\d+)\] (.*)$")
ACTS = {"propose", "accept-proposal", "reject-proposal", "refuse", "ambiguous"}


def extract():
    result_rows = list(csv.DictReader((BASE / "results.csv").open(newline="", encoding="utf-8")))
    expected = {(int(r["run"]), r["scenario"]): int(r["turns"])
                for r in result_rows if int(r["run"]) <= 6}
    rows = []
    for run in range(1, 7):
        condition = "free" if run <= 3 else "tagged"
        log_path = BASE / "logs" / f"{condition}-{run:02d}.txt"
        scenario = None
        current = None

        def finish():
            if current is not None:
                assert current["protocol_act"] in ACTS - {"ambiguous"}, current
                rows.append(current)

        for line in log_path.read_text(encoding="utf-8").splitlines():
            if line.startswith("[scenario] id="):
                finish()
                current = None
                scenario = line.split()[1].split("=", 1)[1]
                continue
            match = MESSAGE.match(line)
            if match:
                finish()
                speaker, turn, encoded = match.groups()
                current = {"run": str(run), "condition": condition,
                           "scenario": scenario, "turn": turn, "speaker": speaker,
                           "message": json.loads(encoded), "protocol_act": "",
                           "protocol_price": ""}
            elif current is not None and line.startswith("[reader] "):
                parsed = ast.literal_eval(line[len("[reader] "):])
                assert parsed is not None
                if condition == "free":
                    current["protocol_act"] = parsed[0]
                else:
                    assert current["protocol_act"] == parsed[0]
                current["protocol_price"] = "" if parsed[1] is None else str(parsed[1])
            elif current is not None and line.startswith("[tag] "):
                current["protocol_act"] = line[len("[tag] "):]
        finish()
    counts = Counter((int(r["run"]), r["scenario"]) for r in rows)
    assert counts == expected, (counts, expected)
    return rows


def write_input(rows):
    with (HERE / "audit_input.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=INPUT_HEADER, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def audit(rows):
    with (HERE / "audit_labels.csv").open(newline="", encoding="utf-8") as stream:
        reader = csv.DictReader(stream)
        assert reader.fieldnames == LABEL_HEADER
        labels = list(reader)
    keyed = {(r["run"], r["scenario"], r["turn"]): r for r in labels}
    assert len(keyed) == len(labels) == len(rows)
    summary = Counter()
    disagreements = []
    for row in rows:
        key = (row["run"], row["scenario"], row["turn"])
        label = keyed[key]
        act = label["reference_act"]
        assert act in ACTS
        if act == "ambiguous":
            assert label["note"]
            summary[(row["condition"], "ambiguous")] += 1
            continue
        assert (label["reference_price"].isdigit() if act == "propose"
                else not label["reference_price"])
        summary[(row["condition"], "audited")] += 1
        if act != row["protocol_act"]:
            summary[(row["condition"], "act_mismatch")] += 1
            disagreements.append((key, "act", act, row["protocol_act"]))
        if act == "propose" and row["protocol_act"] == "propose":
            summary[(row["condition"], "comparable_prices")] += 1
            if label["reference_price"] != row["protocol_price"]:
                summary[(row["condition"], "price_mismatch")] += 1
                disagreements.append((key, "price", label["reference_price"],
                                      row["protocol_price"]))
    for condition in ("free", "tagged"):
        print(condition, {name: summary[(condition, name)] for name in
                          ("audited", "ambiguous", "act_mismatch", "comparable_prices",
                           "price_mismatch")})
    for difference in disagreements:
        print("mismatch", *difference)


def audit_structured():
    """Independent syntax/type check; JSON has no prose for an intent audit."""
    count = 0
    mismatches = 0
    for run in range(7, 10):
        path = BASE / "logs" / f"structured-{run:02d}.txt"
        last_message = None
        for line in path.read_text(encoding="utf-8").splitlines():
            match = MESSAGE.match(line)
            if match:
                last_message = json.loads(match.group(3))
            elif line.startswith("[parser] "):
                parsed = ast.literal_eval(line[len("[parser] "):])
                try:
                    obj = json.loads(last_message)
                    content = obj["content"]
                    act = obj["performative"]
                    price = content["price"]
                    valid = (set(obj) == {"performative", "content"} and
                             set(content) == {"price"} and
                             act in ACTS - {"ambiguous"} and
                             ((act == "propose" and type(price) is int and price > 0) or
                              (act != "propose" and price is None)))
                except (TypeError, ValueError, KeyError):
                    valid = False
                count += 1
                if not valid or parsed != (act, price):
                    mismatches += 1
    print(f"structured schema messages={count} invalid_or_parser_mismatch={mismatches}")
    assert count == 94
    return count, mismatches


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--extract", action="store_true")
    args = parser.parse_args()
    rows = extract()
    if args.extract:
        write_input(rows)
        print(f"extracted {len(rows)} messages to audit_input.csv")
    else:
        audit(rows)
        audit_structured()


if __name__ == "__main__":
    main()
