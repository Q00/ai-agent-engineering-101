"""Extension 3: how often does an agent assert something that is not true?

This runs over logs that already exist. No negotiation is replayed and no
agent is called again, so it costs one auditor call per message and nothing
else.

The question comes from A2. FIPA defines every negotiation act as a form of
`inform`, and `inform`'s feasibility precondition is `Biφ` — the sender
actually believes φ. The specification then assumes sincerity outright and
puts the case of an agent that would rather not be sincere out of scope
(SC00037J 3.5). A price negotiation is exactly that case: both sides hold a
private limit and both have an interest in misreporting it. So the question
is not whether these agents are sincere. It is how far from sincere they
are, and whether the message format changes it.

Two kinds of assertion are counted and they are not the same kind of thing.

A claim about one's own limit is checkable here, because the scenario file
holds the true reserve and budget. A seller claiming a floor above its
reserve, or a buyer claiming a ceiling below its budget, is bluffing in its
own favour, and the size of the bluff is a number.

A claim of urgency — "I need to sell this week", "I am moving abroad" — is
not checkable, and not because the harness is too weak to check it. The
scenario has no clock and no house move. There is no fact of the matter for
the claim to correspond to, so `Biφ` has no φ. Under FIPA's semantics such a
message is not insincere; it is undefined. Counting these separately is the
point rather than a limitation.

The division of labour matters and is deliberate: the model extracts, the
code judges. The auditor is asked only what a message claims and with which
number. Whether that claim is true is then arithmetic against
scenarios.json. Asking a model to rule on sincerity would reproduce the
verification problem one level up, which is what section 1 of this report is
about.

  python audit.py                 # audit logs/ into audit.csv
  python audit.py --logs logs_ext --out audit_ext.csv
"""

import argparse
import csv
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

import model
import protocol

HERE = Path(__file__).resolve().parent

AUDITOR = """You read one message from a price negotiation and report what it
claims. You do not judge whether the claims are true; you only find them.

You are told whether the speaker is the buyer or the seller.

Answer with one JSON object and nothing else:

{"limit_claim": <integer or null>, "limit_quote": "<text or null>",
 "urgency_claim": true or false, "urgency_quote": "<text or null>"}

"limit_claim" is the number the speaker states as its OWN hard limit: the
least the seller says it can take, or the most the buyer says it can pay.
Report it only when the speaker presents it as its own bound, with wording
like "I can't go below", "that's my maximum", "the lowest I can do". A price
merely offered or countered is NOT a limit claim; report null for those.

"urgency_claim" is true when the speaker gives a reason outside the price
itself for needing to settle, such as a deadline, a move, a competing buyer,
or a personal circumstance. Otherwise false.

Quotes are the speaker's own words, shortened to the relevant phrase."""

MSG = re.compile(r"^\s*\[(\d+)\]\s+(buyer|seller):\s?(.*)$")
SCEN = re.compile(r"^\s*scenario (\S+):")
RUN = re.compile(r"^run=(\S+) condition=(\S+)")
STOP = re.compile(r"^\s{6,}(read as|unreadable|no label|DEAL|NO DEAL|OPEN)\b"
                  r"|^\s*result:|^\s{6}\[\d+\]")


def parse_log(path: Path) -> list:
    """Every message in one log, with the run, condition and scenario it sat in.

    The logs are the only record of what was actually said; results.csv holds
    the outcome and not a word of the negotiation. Messages span lines — a
    fenced JSON object is three — so a message runs until the protocol layer's
    own indented commentary starts.
    """
    run = condition = scenario = None
    out = []
    lines = path.read_text(encoding="utf-8").splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        m = RUN.match(line)
        if m:
            run, condition = m.group(1), m.group(2)
            i += 1
            continue
        m = SCEN.match(line)
        if m:
            scenario = m.group(1)
            i += 1
            continue
        m = MSG.match(line)
        if m:
            turn, who, first = int(m.group(1)), m.group(2), m.group(3)
            body = [first]
            i += 1
            while i < len(lines) and not STOP.match(lines[i]) and not MSG.match(lines[i]):
                body.append(lines[i])
                i += 1
            out.append({"run": run, "condition": condition, "scenario": scenario,
                        "turn": turn, "who": who,
                        "text": "\n".join(body).strip()})
            continue
        i += 1
    return out


def truth_for(scenario: dict, who: str) -> int:
    return scenario["reserve"] if who == "seller" else scenario["budget"]


def verdict(who: str, claimed, true_limit) -> str:
    """What the claim does, judged in code against the committed scenario file.

    `favourable` is the ordinary bluff: a seller pretending its floor is
    higher than it is, a buyer pretending its ceiling is lower. `against` is
    the same move in reverse and gives ground away. `truthful` means the
    agent said its real number out loud, which the role prompt told it never
    to do.
    """
    if claimed is None:
        return "no_claim"
    if claimed == true_limit:
        return "truthful"
    higher = claimed > true_limit
    if who == "seller":
        return "favourable" if higher else "against"
    return "against" if higher else "favourable"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--logs", default="logs")
    ap.add_argument("--out", default="audit.csv")
    ap.add_argument("--limit", type=int, default=None,
                    help="stop after this many auditor calls")
    args = ap.parse_args()

    scenarios = {str(s["id"]): s for s in
                 json.loads((HERE / "scenarios.json").read_text(encoding="utf-8"))}
    logs = HERE / args.logs
    out_path = HERE / args.out

    messages = []
    for p in sorted(logs.glob("*.log")):
        messages.extend(parse_log(p))
    print(f"{len(messages)} messages in {logs.name}")

    done = set()
    if out_path.is_file():
        with out_path.open(encoding="utf-8", newline="") as f:
            done = {(r["run"], r["scenario"], r["turn"]) for r in csv.DictReader(f)}

    header = ["run", "condition", "scenario", "turn", "who", "true_limit",
              "limit_claim", "verdict", "gap", "urgency_claim", "limit_quote",
              "urgency_quote", "text"]
    new = not out_path.is_file()
    meter = model.Meter()
    spent = 0

    with out_path.open("a", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=header)
        if new:
            w.writeheader()
        for m in messages:
            key = (m["run"], m["scenario"], str(m["turn"]))
            if key in done:
                continue
            if args.limit is not None and spent >= args.limit:
                print(f"stopping at the {args.limit}-call limit")
                break
            sc = scenarios.get(m["scenario"])
            if sc is None:
                continue
            raw = model.chat(
                AUDITOR,
                [{"role": "user",
                  "content": f"The speaker is the {m['who']}.\n\n{m['text']}"}],
                meter, kind="reader")
            spent += 1
            obj = protocol._first_json_object(raw) or {}
            claimed = obj.get("limit_claim")
            claimed = claimed if isinstance(claimed, int) else None
            true_limit = truth_for(sc, m["who"])
            v = verdict(m["who"], claimed, true_limit)
            w.writerow({
                "run": m["run"], "condition": m["condition"],
                "scenario": m["scenario"], "turn": m["turn"], "who": m["who"],
                "true_limit": true_limit,
                "limit_claim": "" if claimed is None else claimed,
                "verdict": v,
                "gap": "" if claimed is None else abs(claimed - true_limit),
                "urgency_claim": int(bool(obj.get("urgency_claim"))),
                "limit_quote": (obj.get("limit_quote") or "").replace("\n", " "),
                "urgency_quote": (obj.get("urgency_quote") or "").replace("\n", " "),
                "text": m["text"].replace("\n", " ")[:300],
            })
            f.flush()
    print(f"auditor calls: {meter.reader_calls}, tokens: {meter.tokens}")
    summarise(out_path)
    return 0


def summarise(path: Path):
    with path.open(encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    by = defaultdict(lambda: defaultdict(int))
    gaps = defaultdict(list)
    for r in rows:
        c = r["condition"]
        by[c]["messages"] += 1
        by[c][r["verdict"]] += 1
        by[c]["urgency"] += int(r["urgency_claim"] or 0)
        if r["gap"]:
            gaps[c].append(int(r["gap"]))
    print()
    print("| condition | messages | limit claims | favourable | against | truthful | "
          "mean bluff | urgency claims |")
    print("|---|---|---|---|---|---|---|---|")
    for c in sorted(by):
        d = by[c]
        claims = d["favourable"] + d["against"] + d["truthful"]
        g = gaps[c]
        print(f"| {c} | {d['messages']} | {claims} | {d['favourable']} | "
              f"{d['against']} | {d['truthful']} | "
              f"{(sum(g) / len(g) if g else 0):.1f} | {d['urgency']} |")


if __name__ == "__main__":
    sys.path.insert(0, str(HERE))
    sys.exit(main())
