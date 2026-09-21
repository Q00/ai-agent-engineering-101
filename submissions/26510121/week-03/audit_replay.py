"""Additional analysis, track A-prime: replay the recorded bids under a second
manager policy. No model is called here.

The required runs award to the highest stated confidence and have nothing to
check that confidence against. This file asks what the same nine runs would
have produced if the manager had kept one extra thing -- what each contractor's
past confidence turned out to be worth -- and nothing else.

  brier       per contractor per run, over every parseable reply.
              p = confidence/100 when the contractor bid, 0 when it refused
              (a refusal is a claim that it is not the one for this task),
              y = 1 when the contractor is the gold contractor for that task.
  reliability 1 - mean(brier) over that contractor's EARLIER runs in the same
              condition. Run 1 has no history, so reliability is 1.0 and the
              adjusted policy is identical to the raw one by construction.
              Reliability is never computed from the current run: the manager
              must not see the current round's gold.
  adjusted    award to max(confidence x reliability) instead of max(confidence).

Usage: python audit_replay.py
Writes audit_results.csv (run level) and audit_contractors.csv (per contractor).
"""
import argparse
import csv
import json
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
NAMES = ("A", "B", "C")


def load_runs(bids: Path):
    """bids/*.jsonl -> {(condition, run): [record, ...]}, in run order."""
    runs = defaultdict(list)
    for path in sorted(bids.glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rec = json.loads(line)
                runs[(rec["condition"], rec["run"])].append(rec)
    return dict(sorted(runs.items(), key=lambda kv: (kv[0][0], kv[0][1])))


def claimed_probability(rec):
    """What the reply claims about 'I am the right contractor for this task'."""
    if not rec["parse_ok"]:
        return None
    return (rec["confidence"] / 100.0) if rec["bid"] else 0.0


def brier_by_contractor(records):
    """Mean squared error of that claim, per contractor, over one run."""
    acc = defaultdict(list)
    for rec in records:
        p = claimed_probability(rec)
        if p is None:
            continue
        y = 1.0 if rec["contractor"] == rec["gold"] else 0.0
        acc[rec["contractor"]].append((p - y) ** 2)
    return {n: sum(v) / len(v) for n, v in acc.items() if v}


def award(records, reliability=None):
    """Replay one run's awards. reliability=None is the policy the required
    runs actually used. Ties go to the contractor polled first, which is the
    order the records were written in."""
    by_task, order = defaultdict(list), []
    for rec in records:
        if rec["task"] not in by_task:
            order.append(rec["task"])
        by_task[rec["task"]].append(rec)

    correct = misawards = unassigned = 0
    winners = []
    for task in order:
        bids = [r for r in by_task[task] if r["parse_ok"] and r["bid"]]
        if not bids:
            unassigned += 1
            winners.append(None)
            continue
        if reliability is None:
            scored = [(b["confidence"], b) for b in bids]
        else:
            scored = [(b["confidence"] * reliability.get(b["contractor"], 1.0), b)
                      for b in bids]
        scored.sort(key=lambda x: -x[0])
        win = scored[0][1]
        winners.append(win["contractor"])
        if win["contractor"] == win["gold"]:
            correct += 1
        else:
            misawards += 1
    return dict(tasks=len(order), correct=correct, misawards=misawards,
                unassigned=unassigned), winners


def build_rows(runs):
    history = defaultdict(lambda: defaultdict(list))   # condition -> name -> briers
    run_rows, contractor_rows = [], []

    for (condition, run_no), records in runs.items():
        # reliability from earlier runs of this condition only
        reliability = {}
        for name in NAMES:
            past = history[condition][name]
            reliability[name] = 1.0 - (sum(past) / len(past)) if past else 1.0

        raw, raw_winners = award(records)
        adj, adj_winners = award(records, reliability)
        run_rows.append([run_no, condition, "raw", raw["tasks"], raw["correct"],
                         raw["misawards"], raw["unassigned"], ""])
        run_rows.append([run_no, condition, "adjusted", adj["tasks"], adj["correct"],
                         adj["misawards"], adj["unassigned"],
                         " ".join("%s=%.3f" % (n, reliability[n]) for n in NAMES)])

        brier = brier_by_contractor(records)
        for name in NAMES:
            mine = [r for r in records if r["contractor"] == name]
            parsed = [r for r in mine if r["parse_ok"]]
            bids = [r for r in parsed if r["bid"]]
            off = [r for r in bids if r["contractor"] != r["gold"]]
            mean_conf = ""
            if bids:
                mean_conf = "%.1f" % (sum(b["confidence"] for b in bids) / len(bids))
            contractor_rows.append([
                run_no, condition, name, len(mine), len(mine) - len(parsed),
                len(bids), len(off), mean_conf,
                "%.4f" % brier[name] if name in brier else "",
                "%.4f" % reliability[name],
                raw_winners.count(name), adj_winners.count(name)])
            if name in brier:
                history[condition][name].append(brier[name])
    return run_rows, contractor_rows


def write_csv(path, header, rows):
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


def summarise(run_rows, contractor_rows):
    """Printed so the audit has a console capture of its own."""
    totals = defaultdict(lambda: [0, 0, 0, 0])
    for run_no, condition, policy, tasks, correct, mis, un, _rel in run_rows:
        t = totals[(condition, policy)]
        t[0] += tasks
        t[1] += correct
        t[2] += mis
        t[3] += un
    print("%-14s %-9s %9s %10s %11s" % ("condition", "policy", "correct",
                                        "misawards", "unassigned"))
    for key in sorted(totals):
        tasks, correct, mis, un = totals[key]
        print("%-14s %-9s %6d/%-3d %10d %11d" % (key[0], key[1], correct, tasks, mis, un))

    agg = defaultdict(lambda: [0, 0, [], [], 0, 0])
    for row in contractor_rows:
        (_run, condition, name, _ann, _pf, bids, off, mconf, brier,
         _rel, aw_raw, aw_adj) = row
        a = agg[(condition, name)]
        a[0] += bids
        a[1] += off
        if mconf:
            a[2].append(float(mconf))
        if brier:
            a[3].append(float(brier))
        a[4] += aw_raw
        a[5] += aw_adj
    print("\nper contractor, summed over runs:")
    print("%-14s %-5s %5s %11s %10s %11s %11s %11s"
          % ("condition", "name", "bids", "off-domain", "mean conf", "mean brier",
             "awards raw", "awards adj"))
    for key in sorted(agg):
        bids, off, mconfs, briers, aw_raw, aw_adj = agg[key]
        mconf = "%.1f" % (sum(mconfs) / len(mconfs)) if mconfs else "-"
        brier = "%.4f" % (sum(briers) / len(briers)) if briers else "-"
        print("%-14s %-5s %5d %11d %10s %11s %11d %11d"
              % (key[0], key[1], bids, off, mconf, brier, aw_raw, aw_adj))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bids", default="bids",
                    help="directory of bid records (bids-fake for a stub run)")
    args = ap.parse_args()
    bids = HERE / args.bids
    suffix = "-fake" if "fake" in args.bids else ""
    if not bids.is_dir() or not any(bids.glob("*.jsonl")):
        raise SystemExit("no bid records in %s/ -- run run_lab.py first" % args.bids)
    runs = load_runs(bids)
    run_rows, contractor_rows = build_rows(runs)
    write_csv(HERE / ("audit_results%s.csv" % suffix),
              ["run", "condition", "policy", "tasks", "correct", "misawards",
               "unassigned", "reliability_used"], run_rows)
    write_csv(HERE / ("audit_contractors%s.csv" % suffix),
              ["run", "condition", "contractor", "announcements", "parse_fails",
               "bids", "off_domain_bids", "mean_confidence", "brier",
               "reliability_used", "awards_raw", "awards_adjusted"], contractor_rows)
    summarise(run_rows, contractor_rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
