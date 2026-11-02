"""Pull the evidence for challenges 1 and 2 out of logs/market-kit.log (the log itself is left untouched).

    python extract_challenges.py      -> logs/challenge-1/shop-asks.txt, logs/challenge-2/avoided-shops.txt,
                                         logs/challenge-2/dishonest-buyer.txt
"""
import collections
import re
from pathlib import Path

HERE = Path(__file__).parent
LOG = HERE / "logs" / "market-kit.log"
COMPLAINT = re.compile(r"receipt (said|says)|overcharg|higher than|did not match|forged|fake", re.I)


def main():
    lines = LOG.read_text(encoding="utf-8", errors="replace").splitlines()
    rnd, out1 = None, []
    tasks = collections.OrderedDict()
    for l in lines:
        m = re.search(r"\[market\] shop registered as \S+, round (\d)", l)
        if m:
            rnd = int(m.group(1))
        m = re.match(r"(\S+) \[seller\] \[(\w+)\] (.*)", l)
        if m:
            tasks.setdefault((rnd, m.group(2)), []).append(f"{m.group(1)} {m.group(3)}")
    for (r, tid), evs in tasks.items():
        keep = [e for e in evs if re.search(r"buyer:|ASK|DEAL|REJECT|\[guard\]|\[rule\]", e)]
        asks = [int(x) for e in evs for x in re.findall(r"ASK (\d+)", e)]
        deal = next((e.split("DEAL ")[1] for e in evs if "DEAL " in e), "no deal")
        out1.append(f"round {r} task {tid}: first ask {asks[0] if asks else '-'}, asks {asks}, {deal}")
        out1 += ["    " + k[:160] for k in keep]
    d1 = HERE / "logs" / "challenge-1"
    d1.mkdir(parents=True, exist_ok=True)
    (d1 / "shop-asks.txt").write_text("\n".join(out1) + "\n", encoding="utf-8")

    means, complaints, examples = collections.defaultdict(list), collections.Counter(), {}
    for l in lines:
        m = re.match(r"\S+ \[buyer\] (\S+) \| (\d+) reviews, mean ([\d.]+) stars; newest: (.*) \| (.*)$", l)
        if m:
            means[m.group(1)].append(float(m.group(3)))
            if COMPLAINT.search(m.group(4)):
                complaints[m.group(1)] += 1
                examples.setdefault(m.group(1), l[:300])
    picks = collections.Counter(re.sub(r".*\[pick\] (\S+).*", r"\1", l) for l in lines if "[pick]" in l)
    out2 = [f"{sum(picks.values())} orders picked a shop. Shops whose reviews (shown to my buyer) report receipt problems:"]
    for n, c in complaints.most_common():
        out2.append(f"  {n}: {c} listings with a receipt complaint, mean stars {sum(means[n]) / len(means[n]):.1f}, "
                    f"picked by my buyer {picks.get(n, 0)} times")
        out2.append(f"    e.g. {examples[n]}")
    out2.append("\nPick reasons that name receipts or overcharging:")
    out2 += ["  " + l[:260] for l in lines if re.search(r"\[pick\]|harness\] pick", l)
             and re.search(r"receipt|overcharg|mismatch", l, re.I)][:12]
    out2.append("\nMost picked shops: " + ", ".join(f"{n} {c}" for n, c in picks.most_common(8)))
    d2 = HERE / "logs" / "challenge-2"
    d2.mkdir(parents=True, exist_ok=True)
    (d2 / "avoided-shops.txt").write_text("\n".join(out2) + "\n", encoding="utf-8")
    pressure = [l for l in lines if "[seller]" in l and re.search(r"good review|\[guard\]", l)]
    (d2 / "dishonest-buyer.txt").write_text(
        "A buyer offering a good review in exchange for a price below my reserve, and the code guard:\n"
        + "\n".join(pressure) + "\n", encoding="utf-8")
    print(f"challenge-1: {len(tasks)} negotiations; challenge-2: {len(complaints)} flagged shops, {len(pressure)} pressure lines")


if __name__ == "__main__":
    main()
