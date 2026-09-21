"""Aggregate results.csv / results_bias.csv into the tables REPORT.md needs.

    python summarize.py            # the main sweep
    python summarize.py free-tier  # the free-model sweep kept for comparison
"""
import csv
import statistics
import sys
from pathlib import Path

# a cp949 console must not be able to kill the summary
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

BASE = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).parent


def rows(name):
    p = BASE / name
    if not p.is_file():
        return []
    out = []
    for r in csv.DictReader(p.open(encoding="utf-8")):
        if "CRASH" in (r.get("note") or ""):
            out.append({**r, "_crashed": True})
        else:
            out.append({**r, "_crashed": False})
    return out


def num(r, k):
    v = (r.get(k) or "").strip()
    return int(v) if v.isdigit() else None


def note_field(r, key):
    for part in (r.get("note") or "").split():
        if part.startswith(key + "="):
            return part.split("=", 1)[1]
    return ""


def agg(group, keys):
    line = {}
    for k in keys:
        vals = [num(r, k) for r in group if not r["_crashed"]]
        vals = [v for v in vals if v is not None]
        line[k] = (statistics.mean(vals) if vals else None, vals)
    return line


def fmt(cell):
    mean, vals = cell
    if mean is None:
        return "-"
    return f"{mean:.1f} ({', '.join(map(str, vals))})"


def delivered(group):
    got = tot = 0
    for r in group:
        d = note_field(r, "delivered")
        if "/" in d:
            a, b = d.split("/")
            got += int(a); tot += int(b)
    return f"{got}/{tot}" if tot else "-"


def section(title, data, group_key, keys, net=False):
    print(f"\n### {title}\n")
    groups = {}
    for r in data:
        groups.setdefault(group_key(r), []).append(r)
    head = ["group", "runs", "crashed"] + keys
    if net:
        # helped - hurt is the paired comparison: Bias never talks to the
        # contractors, so the counterfactual it is measured against used the
        # identical bids. Comparing arms instead compares different samples.
        head += ["net(helped-hurt)"]
    head += ["delivered", "judge_disagree"]
    print("| " + " | ".join(head) + " |")
    print("|" + "---|" * len(head))
    for g, rs in groups.items():
        a = agg(rs, keys)
        jd = sum(int(note_field(r, "judge_disagree") or 0) for r in rs if not r["_crashed"])
        cells = [g, str(len(rs)), str(sum(r["_crashed"] for r in rs))]
        cells += [fmt(a[k]) for k in keys]
        if net:
            h = a["bias_helped"][0] or 0
            u = a["bias_hurt"][0] or 0
            cells += [f"{h - u:+.1f}"]
        cells += [delivered(rs), str(jd)]
        print("| " + " | ".join(cells) + " |")


core = rows("results.csv")
bias = rows("results_bias.csv")

print(f"# {BASE.resolve().name}  -  mean (per-run values)")
section("core arm - plain contract net",
        core, lambda r: r["condition"],
        ["correct", "misawards", "unassigned", "messages"])

if bias:
    section("bias arms",
            bias, lambda r: f"{r['condition']} / {r.get('arm', '')}",
            ["correct", "misawards", "unassigned", "multi_bidder",
             "bias_helped", "bias_hurt", "first_intervention", "first_flip",
             "bias_messages", "icebreak_messages"], net=True)
