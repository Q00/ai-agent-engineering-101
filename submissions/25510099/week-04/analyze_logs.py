"""Post-hoc reading of the logs for the report: where force and content
disagree in the tagged condition, and how the free reader labelled openings.

Usage: python analyze_logs.py

Tagged: a message tagged reject-proposal or accept-proposal whose body names
a number that did not appear in the previous message is a counter-offer
hidden under another tag (the harness, trusting the tag, recorded no
proposal). Numbers that merely quote the other side's price are not counted.
"""
import re
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
MSG = re.compile(r"^\[t(\d+) (buyer|seller)\s*\] (.*)$")


def episodes(path: Path):
    ep, msgs = None, []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("## episode"):
            ep, msgs = line, []
        elif m := MSG.match(line):
            msgs.append((int(m.group(1)), m.group(2), m.group(3)))
        elif line.strip().startswith("RESULT"):
            yield ep, msgs, line.strip()


def numbers(s):
    return set(re.findall(r"\d+", s))


def tagged():
    hidden = Counter()
    affected = []
    for path in sorted(HERE.glob("logs/run-0[456]-tagged.txt")):
        for ep, msgs, result in episodes(path):
            n_hidden = 0
            prev = ""
            for t, who, text in msgs:
                m = re.match(r"\((reject-proposal|accept-proposal|refuse)\)\s*(.*)", text)
                if m:
                    new = numbers(m.group(2)) - numbers(prev)
                    if new and m.group(1) == "reject-proposal":
                        n_hidden += 1
                        hidden[who] += 1
                prev = text
            if n_hidden:
                sid = re.search(r"scenario=(\w+)", ep).group(1)
                run = path.name[4:6]
                affected.append((run, sid, n_hidden, re.search(r"outcome=(\w+) price=(\S+)", result).groups()))
    print(f"tagged: counter-offers hidden under (reject-proposal): {sum(hidden.values())} "
          f"(buyer {hidden['buyer']}, seller {hidden['seller']}) in {len(affected)} episode(s)")
    for run, sid, n, (outcome, price) in affected:
        print(f"  run {run} {sid}: {n} hidden counter-offer(s), outcome={outcome} price={price}")


def free_openings():
    labels = Counter()
    for path in sorted(HERE.glob("logs/run-0[123]-free.txt")):
        lines = path.read_text(encoding="utf-8").splitlines()
        for i, line in enumerate(lines):
            if line.startswith("[t01 buyer"):
                perf = re.search(r"performative=(\S+)", lines[i + 1]).group(1)
                labels[(perf, "?" in line)] += 1
    print("free: reader label of the buyer's opening message (label, phrased as a question):")
    for (perf, q), n in sorted(labels.items()):
        print(f"  {perf:16} question={q}: {n}")


if __name__ == "__main__":
    tagged()
    free_openings()
