"""Did holding the tool change the armed side's stance even when it was never used?

Per episode: the armed side's first proposed price (as the protocol recorded it), its concessions,
the deal price. Control episodes supply the same role's numbers without any tool. Rows are split by
whether the armed side actually called the tool, so "held but unused" is the possession effect alone.
"""
import csv
import json
from statistics import mean

import compare

luna, lab = compare.luna, compare.lab
GROUPS = {"armed-luna-20260928": "communicator", "shotgun-auto-luna-20260928": "shotgun-auto",
          "shotgun-forced-luna-20260928": "shotgun-forced"}


def stance(run, scenario, role):
    """First proposal, number of later proposals that moved toward the other side, and all proposals."""
    offers, speaker = [], None
    for line in (luna.ROOT / "logs" / f"{run}.jsonl").read_text().splitlines():
        e = json.loads(line)
        if str(e["scenario"]) != scenario:
            continue
        if e["event"] == "message":
            speaker = e["speaker"]
        elif e["event"] == "parse_result" and e["ok"] and e["performative"] == "propose" and speaker == role:
            offers.append(e["price"])
    toward = (lambda a, b: b > a) if role == "buyer" else (lambda a, b: b < a)
    concessions = sum(toward(a, b) for a, b in zip(offers, offers[1:]))
    return (offers[0] if offers else None), concessions, offers


def episodes():
    out = []
    control = [r for r in compare.read_rows(compare.CONTROL / "results.csv") if r["reasoning_effort"] == "low"]
    for r in control:
        for role in ("buyer", "seller"):
            first, conc, offers = stance(r["run"], r["scenario"], role)
            out.append({"group": "control", "role": role, "used_tool": "", **pick(r), "first_offer": first,
                        "concessions": conc, "offers": offers})
    for suite, label in GROUPS.items():
        for r in compare.read_rows(compare.armed.HERE / "runs" / suite / "results.csv"):
            role = r["armed_role"]
            first, conc, offers = stance(r["run"], r["scenario"], role)
            out.append({"group": label, "role": role, "used_tool": int(int(r["tool_calls"]) > 0), **pick(r),
                        "first_offer": first, "concessions": conc, "offers": offers})
    return out


def pick(r):
    return {"run": r["run"], "condition": r["condition"], "scenario": r["scenario"], "outcome": r["outcome"],
            "price": int(r["price"]) if r["price"] else None, "turns": int(r["turns"])}


def fmt(values):
    values = [v for v in values if v is not None]
    return f"{mean(values):.1f} (n={len(values)})" if values else "-"


def main():
    rows = episodes()
    scenarios = {str(s["id"]): s for s in json.loads((luna.ROOT / "scenarios.json").read_text())}
    with (compare.armed.HERE / "runs" / "possession.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]), lineterminator="\n")
        w.writeheader()
        w.writerows({**r, "offers": " ".join(map(str, r["offers"]))} for r in rows)
    cells = [("control", ""), ("communicator", 0), ("communicator", 1), ("shotgun-auto", 0), ("shotgun-auto", 1),
             ("shotgun-forced", 1)]
    for role in ("buyer", "seller"):
        print(f"\n### {role} (first offer / deal price / concessions / turns)")
        print("| group | used | " + " | ".join(f"{sid} {scenarios[sid]['item']} ({scenarios[sid]['reserve']}/{scenarios[sid]['budget']})" for sid in scenarios) + " | concessions | turns |")
        print("|---|---|" + "---|" * (len(scenarios) + 2))
        for group, used in cells:
            g = [r for r in rows if r["group"] == group and r["role"] == role and r["used_tool"] == used]
            if not g:
                continue
            per = []
            for sid in scenarios:
                s = [r for r in g if r["scenario"] == sid]
                per.append(f"{fmt([r['first_offer'] for r in s])} / {fmt([r['price'] for r in s if r['outcome'] == 'deal'])}")
            print(f"| {group} | {used} | " + " | ".join(per) + f" | {fmt([r['concessions'] for r in g])} | {fmt([r['turns'] for r in g])} |")


if __name__ == "__main__":
    main()
