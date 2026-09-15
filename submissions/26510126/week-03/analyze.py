"""Read the runs back. Nothing here calls a model.

    python analyze.py                 # both layers
    python analyze.py --layer ext     # extension only

Absorbs calibration.py's question and extends it, because the extension layer
finally has what week 03 lacked: an outcome to compare a stated confidence
against. Week 03 could only ask whether the bidder was the labelled owner;
here a task is actually done or not.

What is measured, and why each one is here rather than being interesting:

  feasible / optimal / solved   the award landed inside the capability set /
      on the most specialised holder / the work came out right. Kept apart
      because they come apart: `capable` is a lower bound on who can finish a
      task, so a solve from outside it is a finding about the task set and not
      a contradiction.

  Brier score                   (confidence/100 - solved)^2 over awarded bids.
      Week 03 could only show that confidence ordered candidates correctly
      while its level meant nothing; with outcomes the level becomes a number.
      It degenerates when the outcome does not vary, and it does here: with
      every awarded task solved, a stated 90 scores 0.01 by arithmetic. The
      function says so rather than letting a good-looking figure stand.

  calibration.py is kept separate and not folded in. It reads the spec layer's
      console logs, where the only comparison available was the gold label;
      this file reads the extension's journals, where an outcome exists. Two
      questions, two sources, and merging them would blur which was asked.

  agreement with max            how often the manager's award was the highest
      bid. 1.0 means a model reproducing the harness rule it replaced; well
      below means it used something else, and `reason` says what.

  false evidence                bids naming tools the bidder does not hold.
      The check that tests a claim without trusting the number on it.

  cost per solved task          not per call and not per round. Caching and
      round count both move the denominator, so the only honest unit is the
      work that came out.

  cache reuse                   whether the role-outside-the-prefix design
      held. Asserted in a docstring, decided here.
"""

import argparse
import csv
import glob
import json
import os
from collections import Counter, defaultdict

import tools as T

EXT_RESULTS = "results_ext.csv"
EXT_DETAIL = "tasks_ext_detail.csv"
SPEC_RESULTS = "results.csv"

# claude-sonnet-5, USD per million tokens. Cache reads are a tenth of input,
# cache writes a quarter more than it.
PRICE = {"in": 2.0, "out": 10.0, "cache_read": 0.20, "cache_write": 2.50}


def rows(path):
    if not os.path.exists(path):
        return []
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def num(v, default=0):
    try:
        return int(v)
    except (TypeError, ValueError):
        try:
            return float(v)
        except (TypeError, ValueError):
            return default


def table(title, header, body, note=""):
    if not body:
        return
    widths = [max(len(str(r[i])) for r in [header] + body) for i in range(len(header))]
    print(f"\n{title}")
    if note:
        print(f"  {note}")
    print("  " + "  ".join(str(h).ljust(w) for h, w in zip(header, widths)))
    print("  " + "  ".join("-" * w for w in widths))
    for r in body:
        print("  " + "  ".join(str(c).ljust(w) for c, w in zip(r, widths)))


def pct(a, b):
    return f"{a}/{b}" + (f"  {100 * a / b:.0f}%" if b else "")


# ---------------------------------------------------------------- extension


def journal_records():
    out = []
    for p in sorted(glob.glob(os.path.join("logs_ext", "*.jsonl"))):
        cond = os.path.basename(p).rsplit("-", 1)[0]
        for line in open(p, encoding="utf-8"):
            r = json.loads(line)
            r["_condition"] = cond
            r["_file"] = os.path.basename(p)
            out.append(r)
    return out


def ext_summary(res):
    by = defaultdict(lambda: Counter())
    for r in res:
        k = r["condition"]
        for f in ("tasks", "feasible", "optimal", "solved", "partial",
                  "unassigned", "messages", "calls", "tokens",
                  "cache_read", "cache_created"):
            by[k][f] += num(r.get(f))
        by[k]["rounds"] += 1
    body = []
    for cond in sorted(by):
        c = by[cond]
        cost = (c["tokens"] * 0.8 * PRICE["in"] + c["tokens"] * 0.2 * PRICE["out"]
                + c["cache_read"] * PRICE["cache_read"]
                + c["cache_created"] * PRICE["cache_write"]) / 1e6
        reuse = c["cache_read"] / (c["cache_read"] + c["tokens"] + c["cache_created"]) \
            if (c["cache_read"] + c["tokens"] + c["cache_created"]) else 0
        body.append([
            cond, c["rounds"], c["tasks"],
            pct(c["feasible"], c["tasks"]),
            pct(c["optimal"], c["tasks"]),
            pct(c["solved"], c["tasks"]),
            c["unassigned"], c["messages"], c["calls"],
            f"{reuse:.3f}",
            f"${cost:.2f}",
            f"${cost / c['solved']:.3f}" if c["solved"] else "—",
        ])
    table("EXT 1. conditions", ["condition", "rnds", "tasks", "feasible",
                                "optimal", "solved", "unasg", "msgs", "calls",
                                "reuse", "cost", "per solved"], body,
          "optimal is undefined under ext_uniform_tools: every candidate is "
          "capable and none more specialised, so gold is None")


def ext_rounds(res):
    by = defaultdict(lambda: Counter())
    for r in res:
        by[(r["condition"], r["round"])].update({
            k: num(r.get(k)) for k in ("tasks", "solved", "calls",
                                       "cache_read", "cache_created", "tokens")})
    body = []
    for (cond, rnd) in sorted(by):
        c = by[(cond, rnd)]
        denom = c["cache_read"] + c["tokens"] + c["cache_created"]
        body.append([cond, rnd, pct(c["solved"], c["tasks"]), c["calls"],
                     c["tokens"], c["cache_read"],
                     f"{c['cache_read'] / denom:.3f}" if denom else "—"])
    table("EXT 2. by round", ["condition", "round", "solved", "calls",
                              "uncached", "cache read", "reuse"], body,
          "get_trajectory has nothing to read in round 1, so its effect can "
          "only show from round 2")


def ext_calibration(recs, detail):
    """Confidence against the outcome. Week 03 could not ask this."""
    solved = {(d["condition"], d["round"], d["task"]): num(d["solved"])
              for d in detail}
    awarded = {(d["condition"], d["round"], d["task"]): d["awarded"]
               for d in detail}

    buckets = [(0, 69, "<70"), (70, 84, "70-84"), (85, 94, "85-94"),
               (95, 100, "95-100")]

    def bucket(c):
        for lo, hi, label in buckets:
            if lo <= c <= hi:
                return label
        return "?"

    per_all = defaultdict(lambda: [0, 0])       # bid -> was the bidder gold
    per_awarded = defaultdict(lambda: [0, 0])   # awarded bid -> solved
    briers = []
    gold = {}
    for d in detail:
        gold[(d["condition"], d["round"], d["task"])] = d["gold"] or None

    for r in recs:
        if r["type"] != "BID" or not r["payload"].get("bid"):
            continue
        key = (r["_condition"], str(r["round"]), r["task"])
        conf = num(r["payload"].get("confidence"))
        b = per_all[bucket(conf)]
        b[1] += 1
        if gold.get(key) and r["frm"] == gold[key]:
            b[0] += 1
        if awarded.get(key) == r["frm"]:
            got = solved.get(key, 0)
            a = per_awarded[bucket(conf)]
            a[1] += 1
            a[0] += got
            briers.append((conf / 100.0 - got) ** 2)

    order = [lbl for _, _, lbl in buckets]
    table("EXT 3. every bid — P(bidder is gold | confidence)",
          ["confidence", "bids", "was gold"],
          [[l, per_all[l][1], pct(*per_all[l])] for l in order if l in per_all])
    table("EXT 4. awarded bids — P(solved | confidence)",
          ["confidence", "awards", "solved"],
          [[l, per_awarded[l][1], pct(*per_awarded[l])] for l in order
           if l in per_awarded],
          "the sample is selected by the award rule, so this is not calibration")
    if briers:
        solved_rate = sum(solved.get(k, 0) for k in awarded if awarded[k]) / \
            max(1, sum(1 for k in awarded if awarded[k]))
        print(f"\n  Brier score over {len(briers)} awarded bid(s): "
              f"{sum(briers) / len(briers):.3f}   (0 is perfect, 0.25 is a "
              f"coin flip stated at 50)")
        if solved_rate > 0.95 or solved_rate < 0.05:
            print(f"  BUT the outcome has almost no variance "
                  f"({solved_rate:.0%} of awarded tasks solved), so this "
                  f"number is not informative: a stated 90 against an outcome "
                  f"that is always 1 scores 0.01 by arithmetic, not by being "
                  f"well calibrated. Read table EXT 3 instead, where the "
                  f"comparison target does vary.")


def ext_manager(recs, detail):
    """Did the manager's judgement differ from max(confidence)?"""
    bids = defaultdict(list)
    for r in recs:
        if r["type"] == "BID" and r["payload"].get("bid"):
            bids[(r["_condition"], str(r["round"]), r["task"])].append(
                (num(r["payload"].get("confidence")), r["frm"]))
    body = []
    per_cond = defaultdict(lambda: [0, 0, 0])
    for d in detail:
        key = (d["condition"], d["round"], d["task"])
        got = d["awarded"]
        if not got or key not in bids:
            continue
        top = max(c for c, _ in bids[key])
        winners = {w for c, w in bids[key] if c == top}
        cell = per_cond[d["condition"]]
        cell[1] += 1
        if got in winners:
            cell[0] += 1
        if len(bids[key]) > 1:
            cell[2] += 1
    for cond in sorted(per_cond):
        hit, n, contested = per_cond[cond]
        body.append([cond, n, contested, pct(hit, n)])
    table("EXT 5. manager against max(confidence)",
          ["condition", "awards", "contested", "agreed with max"], body,
          "1.0 in ext_coded_manager is the harness rule; anywhere else it is "
          "a model choosing to reproduce it")


def ext_failures(detail):
    per = defaultdict(Counter)
    for d in detail:
        for f in (d.get("failures") or "").split(";"):
            if f:
                per[d["condition"]][f.split(":")[0]] += 1
    modes = sorted({m for c in per.values() for m in c})
    if not modes:
        print("\nEXT 6. failure modes\n  none recorded")
        return
    table("EXT 6. failure modes",
          ["condition"] + modes,
          [[cond] + [per[cond].get(m, 0) for m in modes] for cond in sorted(per)])


def ext_evidence(recs):
    per = defaultdict(lambda: [0, 0])
    for r in recs:
        if r["type"] != "BID" or not r["payload"].get("bid"):
            continue
        ev = r["payload"].get("evidence") or []
        man = T.MANIFESTS.get(r["frm"], ())
        if r["_condition"] == "ext_uniform_tools":
            man = T.TOOL_NAMES
        cell = per[r["_condition"]]
        cell[1] += 1
        if T.false_evidence(ev, man):
            cell[0] += 1
    table("EXT 7. evidence naming a tool the bidder does not hold",
          ["condition", "bids", "false"],
          [[c, per[c][1], pct(*per[c])] for c in sorted(per)])


def ext_depth(recs, detail):
    subs = [r for r in recs if "." in r["task"]]
    body = [[c, sum(num(d["subtasks"]) for d in detail if d["condition"] == c),
             sum(1 for d in detail if d["condition"] == c and num(d["partial"]))]
            for c in sorted({d["condition"] for d in detail})]
    table("EXT 8. recursion", ["condition", "pieces handed out", "partial reports"],
          body,
          f"{len(subs)} subtask message(s) in the journals. At the default "
          f"read cap a candidate can survey the whole 60-line file in three "
          f"calls, so substitution is cheaper than decomposition")


# ---------------------------------------------------------------- spec layer


def spec_summary(res):
    by = defaultdict(lambda: Counter())
    for r in res:
        c = by[r["condition"]]
        for f in ("tasks", "correct", "messages", "unassigned", "misawards"):
            c[f] += num(r.get(f))
        c["runs"] += 1
    table("SPEC 1. week 03 conditions",
          ["condition", "runs", "tasks", "correct", "messages", "misawards"],
          [[cond, by[cond]["runs"], by[cond]["tasks"],
            pct(by[cond]["correct"], by[cond]["tasks"]),
            by[cond]["messages"], by[cond]["misawards"]]
           for cond in sorted(by)],
          "one label per task, no execution: correct is the whole story here")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--layer", choices=["spec", "ext", "both"], default="both")
    args = ap.parse_args()

    if args.layer in ("spec", "both"):
        spec = rows(SPEC_RESULTS)
        if spec:
            print("=" * 72)
            print("SPEC LAYER — capability is a string in a prompt")
            print("=" * 72)
            spec_summary(spec)

    if args.layer in ("ext", "both"):
        res, detail = rows(EXT_RESULTS), rows(EXT_DETAIL)
        if not res:
            print("\nno extension results yet")
            return
        recs = journal_records()
        print("\n" + "=" * 72)
        print("EXTENSION LAYER — capability is a tool the candidate holds")
        print("=" * 72)
        ext_summary(res)
        ext_rounds(res)
        ext_calibration(recs, detail)
        ext_manager(recs, detail)
        ext_failures(detail)
        ext_evidence(recs)
        ext_depth(recs, detail)


if __name__ == "__main__":
    main()
