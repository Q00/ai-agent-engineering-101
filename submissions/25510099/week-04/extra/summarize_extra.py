"""Tables for the extra experiment: deceptive seller against the main runs.

Usage: python summarize_extra.py

1. Per condition, baseline vs deceptive: correct, violations, outcomes, mean
   turns, reader calls, and the seller's share of the surplus on deals in
   scenarios where reserve < budget, violation-free deals only
   (0 = deal at the reserve, 1 = at the budget).
2. Honesty labels per condition from judgments.csv (seller messages).
3. Reader vs judge agreement on the act, free condition, baseline vs deceptive.
4. Every deceptive episode row.
"""
import csv
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
ORDER = ("free", "tagged", "structured")
SCEN = {s["id"]: s for s in __import__("json").loads((HERE.parent / "scenarios.json").read_text())}


def rows(path):
    with path.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def surplus(r):
    s = SCEN[r["scenario"]]
    # Only deals inside both limits: a recorded violation (tagged, stale price)
    # is a protocol artefact, not a price the two agents agreed on.
    if r["outcome"] != "deal" or r["violation"] != "0" or s["reserve"] >= s["budget"]:
        return None
    return (int(r["price"]) - s["reserve"]) / (s["budget"] - s["reserve"])


def table1(base, dec):
    print("| condition | seller | correct | violations | deal / no_deal / open | mean turns | reader calls | seller surplus share (clean deals) |")
    print("|---|---|---|---|---|---|---|---|")
    for c in ORDER:
        for name, data in (("honest", base), ("deceptive", dec)):
            rs = [r for r in data if r["condition"] == c and r["outcome"]]
            if not rs:
                continue
            sh = [x for x in (surplus(r) for r in rs) if x is not None]
            oc = Counter(r["outcome"] for r in rs)
            print(f"| {c} | {name} | {sum(int(r['correct']) for r in rs)}/{len(rs)} | "
                  f"{sum(int(r['violation']) for r in rs)} | {oc['deal']} / {oc['no_deal']} / {oc['open']} | "
                  f"{sum(int(r['turns']) for r in rs) / len(rs):.1f} | {sum(int(r['reader_calls']) for r in rs)} | "
                  f"{(sum(sh) / len(sh)):.2f} (n={len(sh)}) |" if sh else
                  f"| {c} | {name} | {sum(int(r['correct']) for r in rs)}/{len(rs)} | "
                  f"{sum(int(r['violation']) for r in rs)} | {oc['deal']} / {oc['no_deal']} / {oc['open']} | "
                  f"{sum(int(r['turns']) for r in rs) / len(rs):.1f} | {sum(int(r['reader_calls']) for r in rs)} | - |")


def table2(j):
    print("\n| condition | seller messages | direct | indirect | false | none | other |")
    print("|---|---|---|---|---|---|---|")
    for c in ORDER:
        rs = [r for r in j if r["condition"] == c and r["speaker"] == "seller"]
        h = Counter(r["honesty"] for r in rs)
        other = len(rs) - sum(h[k] for k in ("direct", "indirect", "false", "none"))
        print(f"| {c} | {len(rs)} | {h['direct']} | {h['indirect']} | {h['false']} | {h['none']} | {other} |")


def table3(jb, jd):
    print("\n| free reader vs judge | messages | act agrees | act disagrees | price disagrees (both named) |")
    print("|---|---|---|---|---|")
    for name, j in (("honest seller (main runs)", jb), ("deceptive seller", jd)):
        rs = [r for r in j if r["condition"] == "free"]
        if not rs:
            continue
        agree = sum(r["reader_performative"] == r["judge_performative"] for r in rs)
        pd = sum(1 for r in rs if r["reader_price"] not in ("None", "") and r["judge_price"] not in ("", "None")
                 and r["reader_price"] != r["judge_price"])
        print(f"| {name} | {len(rs)} | {agree} | {len(rs) - agree} | {pd} |")


def table4(dec):
    print("\n| run | condition | scenario | deal_possible | outcome | price | correct | violation | turns | format_errors | reader_calls | note |")
    print("|---|---|---|---|---|---|---|---|---|---|---|---|")
    for r in sorted(dec, key=lambda r: (ORDER.index(r["condition"]), int(r["run"]), r["scenario"])):
        print("| " + " | ".join(r[k] for k in ("run", "condition", "scenario", "deal_possible", "outcome", "price",
                                                 "correct", "violation", "turns", "format_errors", "reader_calls"))
              + f" | {r['note'].replace('|', '/')} |")


if __name__ == "__main__":
    base = rows(HERE.parent / "results.csv")
    dec = rows(HERE / "results-deception.csv")
    table1(base, dec)
    jd = rows(HERE / "judgments.csv") if (HERE / "judgments.csv").is_file() else []
    jb = rows(HERE / "judgments-baseline.csv") if (HERE / "judgments-baseline.csv").is_file() else []
    if jd:
        table2(jd)
    if jd or jb:
        table3(jb, jd)
    table4(dec)
