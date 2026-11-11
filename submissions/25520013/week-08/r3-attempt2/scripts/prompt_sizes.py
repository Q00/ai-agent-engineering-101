"""Prompt size per agent from the run records: steps, median and max chars, mean share by section, compactions.

    python3 scripts/prompt_sizes.py --root <W> [--hours 12]

Reads <W>/a2/trajectory.jsonl (the kernel's run records, kernel.trajectory): llm rows that carry "sections" (section
name -> chars, written by kernel.llm inside kernel.llm.sections) and "compact" rows (kernel.operator._compact). An
agent is the row's role when it has one (shop, buyer, analyst), else its agent (operator, auditor). Model calls made
for a compaction (purpose "compact") are counted apart from the agent's steps. Stdlib only, no model call.
"""

import argparse
import json
import os
import statistics
import sys
import time

TRAJ = os.path.join("a2", "trajectory.jsonl")


def read(path, since):
    """-> {agent: {"steps": [(total, sections)], "compact_calls": n, "compact_ok": n, "compact_failed": n}}."""
    out = {}
    try:
        f = open(path, "rb")
    except OSError:
        return out
    with f:
        for line in f:
            if b'"llm"' not in line and b'"compact"' not in line:
                continue
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if (
                not isinstance(r, dict)
                or not isinstance(r.get("ts"), (int, float))
                or r["ts"] < since
            ):
                continue
            who = r.get("role") or r.get("agent") or "?"
            a = out.setdefault(
                who,
                {"steps": [], "compact_calls": 0, "compact_ok": 0, "compact_failed": 0},
            )
            if r.get("kind") == "llm" and isinstance(r.get("sections"), dict):
                if r.get("purpose") == "compact":
                    a["compact_calls"] += 1
                    continue
                sec = {
                    k: v
                    for k, v in r["sections"].items()
                    if isinstance(v, (int, float))
                }
                total = (
                    r.get("prompt_chars")
                    if isinstance(r.get("prompt_chars"), (int, float))
                    else sum(sec.values())
                )
                a["steps"].append((total, sec))
            elif r.get("kind") == "compact":
                a["compact_ok" if r.get("status") == "ok" else "compact_failed"] += 1
    return out


def report(stats):
    """The printed lines, one block per agent."""
    lines = []
    for who in sorted(stats):
        a = stats[who]
        totals = [t for t, _ in a["steps"]]
        if not totals and not a["compact_ok"] and not a["compact_failed"]:
            continue
        head = f"{who}: steps {len(totals)}"
        if totals:
            head += f", total chars median {statistics.median(totals):,.0f} max {max(totals):,.0f}"
        head += f", compactions {a['compact_ok']} (failed {a['compact_failed']}, summary calls {a['compact_calls']})"
        lines.append(head)
        share = {}
        for total, sec in a["steps"]:
            for k, v in sec.items():
                share[k] = share.get(k, 0.0) + (v / total if total else 0.0)
        for k, v in sorted(share.items(), key=lambda kv: -kv[1]):
            lines.append(f"  {k}: {100 * v / len(totals):.1f}%")
    return lines or ["no llm rows with sections in the window"]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", required=True)
    ap.add_argument("--hours", type=float, default=12.0)
    a = ap.parse_args(argv)
    path = os.path.join(os.path.expanduser(a.root), TRAJ)
    print("\n".join(report(read(path, time.time() - a.hours * 3600))))
    return 0


if __name__ == "__main__":
    sys.exit(main())
