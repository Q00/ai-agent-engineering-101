"""Section 9's analysis: does bid token cost track task-description length,
or confidence-manipulation (system prompt) length? No API calls -- reads the
bids.csv files already committed under runs/*/bids.csv plus tasks.json and
contract_net.CONDITIONS.

Usage (from submissions/26620029/week-03/):
    python analysis/token_correlation.py
"""
import csv
import glob
import json
import sys
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).parent.parent
sys.path.insert(0, str(HERE))
import contract_net as cn


def pearson(xs, ys):
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    sx = sum((x - mx) ** 2 for x in xs) ** 0.5
    sy = sum((y - my) ** 2 for y in ys) ** 0.5
    return cov / (sx * sy) if sx > 0 and sy > 0 else float("nan")


def load_rows():
    tasks = {t["id"]: t["desc"] for t in json.loads((HERE / "tasks.json").read_text())}
    desc_len = {tid: len(d) for tid, d in tasks.items()}
    sysprompt_len = {(c, n): len(p) for c, profs in cn.CONDITIONS.items() for n, p in profs.items()}

    rows = []
    for path in glob.glob(str(HERE / "runs" / "*" / "bids.csv")):
        with open(path, newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                r["input_tokens"] = int(r["input_tokens"])
                r["output_tokens"] = int(r["output_tokens"])
                r["desc_len"] = desc_len[r["task_id"]]
                r["sp_len"] = sysprompt_len[(r["condition"], r["contractor"])]
                rows.append(r)
    return rows


def main():
    rows = load_rows()
    print(f"loaded {len(rows)} bids from runs/*/bids.csv\n")

    by_cc = defaultdict(list)
    for r in rows:
        by_cc[(r["condition"], r["contractor"])].append(r)

    print("=== (A) desc_len vs input_tokens, within fixed (condition, contractor) ===")
    for (cond, name), rs in sorted(by_cc.items()):
        xs = [r["desc_len"] for r in rs]
        ys = [r["input_tokens"] for r in rs]
        if len(set(xs)) < 2:
            continue
        print(f"  {cond:22s} {name:8s} n={len(rs):3d}  r={pearson(xs, ys):+.2f}")

    print("\n=== (B) group-average input_tokens vs system-prompt length (chars) ===")
    sps = [rs[0]["sp_len"] for rs in by_cc.values()]
    avg_in = [sum(r["input_tokens"] for r in rs) / len(rs) for rs in by_cc.values()]
    print(f"  r = {pearson(sps, avg_in):+.3f}")
    n = len(sps)
    mx, my = sum(sps) / n, sum(avg_in) / n
    b = sum((x - mx) * (y - my) for x, y in zip(sps, avg_in)) / sum((x - mx) ** 2 for x in sps)
    a = my - b * mx
    print(f"  input_tokens ~= {a:.2f} + {b:.4f} * sysprompt_chars  (~{1/b:.2f} chars/token)")

    print("\n=== (C) desc_len vs output_tokens, all bids ===")
    xs = [r["desc_len"] for r in rows]
    ys = [r["output_tokens"] for r in rows]
    print(f"  r = {pearson(xs, ys):+.3f}  (n={len(rows)})")

    print("\n=== (D) per-condition average input/output/total tokens ===")
    by_cond = defaultdict(list)
    for r in rows:
        by_cond[r["condition"]].append(r)
    for cond, rs in sorted(by_cond.items()):
        avg_in = sum(r["input_tokens"] for r in rs) / len(rs)
        avg_out = sum(r["output_tokens"] for r in rs) / len(rs)
        print(f"  {cond:22s} n={len(rs):3d}  input={avg_in:6.1f}  output={avg_out:5.1f}  total={avg_in+avg_out:6.1f}")


if __name__ == "__main__":
    main()
