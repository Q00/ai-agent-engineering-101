"""Was a stated confidence worth anything? Measured from the logs.

No model calls. This reads logs/*.txt and answers the question the lecture
raises with Xiong et al. (ICLR 2024): the contractor's confidence is the
model's own assessment of itself, so does a higher number actually mean a
higher chance of being the right contractor for the job?

    python calibration.py            # every log in logs/
    python calibration.py logs_reversed

Two different questions, and the distinction matters:

  A. Over every bid — P(bidder is the gold contractor | confidence bucket).
     This is calibration in Xiong's sense. It asks whether the number the
     model produced tracks the truth, independently of what the manager then
     did with it.

  B. Over awarded bids only — P(award was correct | confidence bucket).
     This is what the allocation actually got. It is not calibration: the
     award rule already filtered these bids by taking the maximum, so the
     sample is selected. Reported because it is the number the lab measures,
     and separated from A because conflating them would credit the bidders
     for the manager's filtering.

`gold` is read only from the announcement lines the manager logged. Nothing
here reaches a prompt, and nothing here can change a recorded run.
"""

import collections
import glob
import os
import re
import sys

ANNOUNCE = re.compile(r"^\[announce\] contract (\d+) to \d+ contractors \(gold ([ABC])\)")
BID = re.compile(r"^\s+\[bid\] ([ABC]): bid=(True|False) confidence=(\d+(?:\.\d+)?) how=(\w+)")
AWARD = re.compile(r"^\s+\[award\] ([ABC]) at (\d+(?:\.\d+)?) \(gold ([ABC])\) -> (\w+)")
NOBID = re.compile(r"^\s+\[no-bid\] ([ABC]): unparseable \((\w+)\)")

# Buckets follow the values the model actually produced rather than even
# tenths: it clusters hard on 90, 95, 97 and 100, so even-width buckets would
# leave most of them empty and hide the clustering.
BUCKETS = [(90, 94, "90-94"), (95, 96, "95-96"), (97, 99, "97-99"), (100, 100, "100")]


def bucket_of(conf):
    for lo, hi, label in BUCKETS:
        if lo <= conf <= hi:
            return label
    return f"<90" if conf < 90 else ">100"


def read_logs(logdir):
    """Yield (condition, run, task, gold, bidder, confidence) for every bid,
    and (condition, run, task, gold, winner, confidence, outcome) per award."""
    bids, awards, unparsed = [], [], []
    for path in sorted(glob.glob(os.path.join(logdir, "*.txt"))):
        base = os.path.basename(path)[:-4]
        condition, _, run_no = base.rpartition("-")
        task = gold = None
        for line in open(path, encoding="utf-8"):
            m = ANNOUNCE.match(line)
            if m:
                task, gold = int(m.group(1)), m.group(2)
                continue
            m = BID.match(line)
            if m and gold:
                who, is_bid, conf, how = m.group(1), m.group(2) == "True", float(m.group(3)), m.group(4)
                if is_bid:
                    bids.append((condition, run_no, task, gold, who, conf, how))
                continue
            m = NOBID.match(line)
            if m and gold:
                unparsed.append((condition, run_no, task, gold, m.group(1), m.group(2)))
                continue
            m = AWARD.match(line)
            if m and gold:
                awards.append((condition, run_no, task, m.group(3), m.group(1),
                               float(m.group(2)), m.group(4)))
    return bids, awards, unparsed


def table(title, rows, header):
    widths = [max(len(str(r[i])) for r in [header] + rows) for i in range(len(header))]
    print(f"\n{title}")
    print("  " + "  ".join(h.ljust(w) for h, w in zip(header, widths)))
    print("  " + "  ".join("-" * w for w in widths))
    for r in rows:
        print("  " + "  ".join(str(c).ljust(w) for c, w in zip(r, widths)))


def rate(hit, n):
    return f"{hit}/{n}" + (f"  {100*hit/n:.0f}%" if n else "")


def main():
    logdir = sys.argv[1] if len(sys.argv) > 1 else "logs"
    bids, awards, unparsed = read_logs(logdir)
    if not bids:
        raise SystemExit(f"no bids parsed from {logdir}/*.txt")

    print(f"source: {logdir}/  —  {len(bids)} bid(s), {len(awards)} award(s), "
          f"{len(unparsed)} unparseable repl(ies)")

    # A. calibration over every bid
    per = collections.defaultdict(lambda: [0, 0])
    for _, _, _, gold, who, conf, _ in bids:
        b = per[bucket_of(conf)]
        b[1] += 1
        if who == gold:
            b[0] += 1
    order = [lbl for _, _, lbl in BUCKETS] + sorted(set(per) - {l for _, _, l in BUCKETS})
    rows = [[lbl, per[lbl][1], rate(*per[lbl])] for lbl in order if lbl in per]
    table("A. 모든 입찰 — P(입찰자 == gold | 확신도 구간)",
          rows, ["confidence", "bids", "gold 일치"])

    # A, split by condition — the interesting part is whether the same number
    # means different things under different instructions.
    conds = sorted({c for c, *_ in bids})
    rows = []
    for cond in conds:
        per_c = collections.defaultdict(lambda: [0, 0])
        for c, _, _, gold, who, conf, _ in bids:
            if c != cond:
                continue
            b = per_c[bucket_of(conf)]
            b[1] += 1
            if who == gold:
                b[0] += 1
        rows.append([cond] + [rate(*per_c[lbl]) if lbl in per_c else "—"
                              for lbl in [l for _, _, l in BUCKETS]])
    table("A-2. 조건별 — 같은 숫자가 조건에 따라 다른 것을 뜻하는가",
          rows, ["condition"] + [l for _, _, l in BUCKETS])

    # B. awarded bids only
    per_a = collections.defaultdict(lambda: [0, 0])
    for _, _, _, gold, who, conf, outcome in awards:
        b = per_a[bucket_of(conf)]
        b[1] += 1
        if outcome == "correct":
            b[0] += 1
    rows = [[lbl, per_a[lbl][1], rate(*per_a[lbl])]
            for lbl in [l for _, _, l in BUCKETS] if lbl in per_a]
    table("B. 낙찰된 입찰만 — P(correct | 확신도 구간)  ※ 최댓값으로 걸러진 표본",
          rows, ["confidence", "awards", "correct"])

    # Out-of-skill bidding, which is what the overconfident instruction moves.
    rows = []
    for cond in conds:
        off = [(t, who, conf) for c, _, t, gold, who, conf, _ in bids
               if c == cond and who != gold]
        tasks_crossed = sorted({t for t, _, _ in off})
        rows.append([cond, len(off), len([1 for c, *_ in bids if c == cond]),
                     ",".join(str(t) for t in tasks_crossed) or "—"])
    table("C. 능력 밖 입찰 — gold 가 아닌 contractor 가 넣은 입찰",
          rows, ["condition", "off-skill", "all bids", "넘은 태스크"])


if __name__ == "__main__":
    main()
