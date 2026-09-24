"""Regenerate REPORT.md from what actually ran.

Parts 1 and 3 are facts about the setup and about the three designs, so they
are written from `agents.py`, `chat.py` and `scenarios.json` rather than typed
out by hand and left to drift. Part 2 is the two tables from `results.csv`.

Part 4 is the interpretation and this script does not write it. What it does
write is the evidence: the episodes that ended on turn one, the reader labels
that disagree with the numbers in the message they labelled, the deals that
broke a private limit, the acceptances that had no price to close on -- each
with the log file and line number to quote. The paragraph is yours; running
this again keeps whatever you wrote between the two markers.

  python report.py
"""
import csv
import json
import re
import sys
from pathlib import Path

import agents
import chat
import summarize

HERE = Path(__file__).resolve().parent
KEEP_START = "<!-- your interpretation goes below this line; report.py preserves it -->"
KEEP_END = "<!-- end of your interpretation -->"

MSG = re.compile(r"^  \[(\d+)\] (buyer|seller): (.*)$")
READ = re.compile(r"^       read: performative=(\S+) price=(\S+) ok=(\d)")
SCENARIO = re.compile(r"^scenario (\S+):")
NUMBER = re.compile(r"\d+")


def load_rows(results):
    with results.open(encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    return [r for r in rows if any((v or "").strip() for v in r.values())]


def parse_logs(logs):
    """logs/<run>.txt -> {(run, scenario): [(lineno, kind, ...)]}."""
    episodes = {}
    for path in sorted(logs.glob("*.txt")):
        run = path.stem
        sid, current = None, None
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            m = SCENARIO.match(line)
            if m:
                sid = m.group(1)
                current = episodes.setdefault((run, sid), [])
                continue
            if current is None:
                continue
            m = MSG.match(line)
            if m:
                current.append((n, "msg", int(m.group(1)), m.group(2), m.group(3)))
                continue
            m = READ.match(line)
            if m:
                current.append((n, "read", m.group(1), m.group(2), int(m.group(3))))
    return episodes


def quote(path_name, lineno, text, limit=110):
    text = text.strip()
    if len(text) > limit:
        text = text[:limit - 1] + "…"
    return f"- `logs/{path_name}.txt:{lineno}` — {text}"


def evidence(rows, episodes):
    """Everything the assignment says to count rather than hide."""
    out = []

    def section(title, lines, empty):
        out.append(f"**{title}**\n")
        out.extend(lines if lines else [f"_{empty}_"])
        out.append("")

    # 1. episodes that ended on the first message
    lines = []
    for r in rows:
        if (r["turns"] or "").strip() == "1":
            ep = episodes.get((r["run"], r["scenario"]), [])
            for item in ep:
                if item[1] == "msg":
                    lines.append(quote(r["run"], item[0], f"{item[3]}: {item[4]}"))
                if item[1] == "read":
                    lines.append(f"  read as `{item[2]}` → outcome `{r['outcome']}`, "
                                 f"correct={r['correct']}")
    section(f"Episodes that ended on turn one ({sum(1 for r in rows if (r['turns'] or '').strip() == '1')})",
            lines, "none")

    # 2. reader labels that do not match the numbers in the message
    lines = []
    for (run, sid), ep in sorted(episodes.items()):
        last_msg = None
        for item in ep:
            if item[1] == "msg":
                last_msg = item
            elif item[1] == "read" and last_msg is not None:
                _, _, act, price, ok = item
                nums = set(NUMBER.findall(last_msg[4]))
                if price not in ("None", "") and nums and price not in nums:
                    lines.append(quote(run, last_msg[0], f"{last_msg[3]}: {last_msg[4]}"))
                    lines.append(f"  read as `{act}` price={price}, but the message "
                                 f"names {', '.join(sorted(nums))} — scenario {sid}")
    # an acceptance does not normally name a new number. When it does, the
    # message was probably a counter-offer, and the deal closes at the other
    # side's standing price instead -- the reference run's exact failure.
    for (run, sid), ep in sorted(episodes.items()):
        last_msg = None
        for item in ep:
            if item[1] == "msg":
                last_msg = item
            elif item[1] == "read" and item[2] == "accept-proposal" and last_msg:
                nums = sorted(set(NUMBER.findall(last_msg[4])))
                if not nums:
                    continue
                row = next((r for r in rows if r["run"] == run
                            and r["scenario"] == sid), None)
                closed = (row or {}).get("price", "").strip()
                # an acceptance that restates the agreed number is normal. The
                # interesting case is the one that names a DIFFERENT number:
                # the message was a counter-offer, and the episode closed at
                # the other side's standing price instead.
                if closed in nums:
                    continue
                lines.append(quote(run, last_msg[0], f"{last_msg[3]}: {last_msg[4]}"))
                lines.append(f"  read as `accept-proposal`, but the message names "
                             f"{', '.join(nums)}"
                             + (f"; the deal closed at {closed}, the other side's "
                                f"standing price" if closed else ", and no deal was "
                                "recorded")
                             + f" — scenario {sid}")

    section("Reader labels worth checking by hand", lines,
            "no label disagreed with the numbers in its message")

    # 3. deals that broke a private limit
    lines = [f"- `{r['run']}` scenario {r['scenario']}: deal at {r['price']}, "
             f"deal_possible={r['deal_possible']} — see `logs/{r['run']}.txt`"
             for r in rows if (r["violation"] or "").strip() == "1"]
    section(f"Deals that broke a private limit ({len(lines)})", lines, "none")

    # 4. acceptances with nothing to close on
    lines = [f"- `{r['run']}` scenario {r['scenario']}: outcome `{r['outcome']}` after "
             f"{r['turns']} turns — {r['note']}"
             for r in rows if "no price on the table" in (r["note"] or "")]
    section(f"Acceptances with no price on the table ({len(lines)})", lines, "none")

    # 5. messages the layer could not read at all
    lines = []
    for (run, sid), ep in sorted(episodes.items()):
        last_msg = None
        for item in ep:
            if item[1] == "msg":
                last_msg = item
            elif item[1] == "read" and item[4] == 0 and last_msg is not None:
                lines.append(quote(run, last_msg[0], f"{last_msg[3]}: {last_msg[4]}"))
    section(f"Messages the protocol layer could not read ({len(lines)})", lines, "none")

    # 6. crashed episodes
    lines = [f"- `{r['run']}` scenario {r['scenario']}: {r['note']}"
             for r in rows if not (r["turns"] or "").strip()]
    section(f"Crashed episodes ({len(lines)})", lines, "none")
    return "\n".join(out)


def failure_row(rows):
    """The last row of the comparison table, from the data rather than from
    the reference run."""
    cells = []
    for c in summarize.CONDITIONS:
        got = [r for r in rows if r["condition"] == c]
        fmt = sum(int(r["format_errors"]) for r in got if (r["format_errors"] or "").isdigit())
        unmatched = sum(1 for r in got if "no price on the table" in (r["note"] or ""))
        first = sum(1 for r in got if (r["turns"] or "").strip() == "1")
        viol = sum(int(r["violation"]) for r in got if (r["violation"] or "").isdigit())
        cells.append(f"{fmt} unreadable, {unmatched} accept with no price, "
                     f"{first} ended on turn one, {viol} limit breach(es)")
    return cells


def part1(scenarios):
    rows = [
        ("provider / endpoint", f"`{chat.BACKEND}` — "
         + (f"`{chat.BASE_URL}`" if chat.BACKEND == "api" else "`claude -p`")),
        ("model", f"`{chat.MODEL}`"),
        ("temperature", "not settable on this backend" if chat.temperature_rejected
         else f"`{chat.TEMPERATURE}`"),
        ("max tokens", f"`{chat.MAX_TOKENS}`"),
        ("turn limit", "8 messages counting both sides, then the episode ends `open`"),
        ("scenarios", f"{len(scenarios)}, in `scenarios.json`, committed before the first run"),
        ("repeats", f"3 per condition per scenario, {len(scenarios) * 3 * 3} episodes"),
    ]
    table = "\n".join(f"| {k} | {v} |" for k, v in rows)
    sc = "\n".join(f"| {s['id']} | {s['item']} | {s['reserve']} | {s['budget']} | "
                   f"{1 if s['reserve'] <= s['budget'] else 0} | "
                   f"{s['budget'] - s['reserve']:+d} |" for s in scenarios)
    return f"""## 1. Setup

| | |
|---|---|
{table}

### Scenarios

| id | item | reserve | budget | deal_possible | zone |
|---|---|---|---|---|---|
{sc}

`id 1` has a wide zone of agreement and is the baseline: a condition that
cannot close this one has a problem that is not about message format. `id 2`
is narrow, so a single careless concession is a limit breach. `id 3` misses by
the same 20 that `id 2` spans, which separates an agent that holds its limit
from one that simply settles when the numbers are close. `id 4` misses by 650
and should end cleanly.

Four scenarios is the assignment's minimum, chosen because the reference run
spent about 340 model calls on six. A fifth with `reserve == budget` was
written and dropped for the same reason; it is in the commit history.

### The role paragraph, identical in all three conditions

```text
{agents.ROLE['buyer']}
```

```text
{agents.ROLE['seller']}
```

```text
{agents.COMMON.replace('[[turn_limit]]', '8')}
```

### The format paragraph, the only thing that differs

```text
free:       {agents.FORMAT['free'].strip()}
```

```text
tagged:     {agents.FORMAT['tagged'].strip()}
```

```text
structured: {agents.FORMAT['structured'].strip()}
```

### The reader prompt, identical in `free` and `tagged`

```text
{agents.READER_SYSTEM}
```

### How `correct` and `violation` were decided

`correct` is 1 when a deal happened exactly where one was possible, at a price
inside both limits; an episode that ended without a deal is correct when no
zone of agreement existed, whether it refused or ran out of turns. `violation`
is 1 when a deal closed below the reserve or above the budget. They are
separate columns because an episode can be wrong without either agent breaking
its limit — a reader that misreads a price does exactly that.

### How to run

```powershell
python verify_offline.py     # 27 checks of the counting, no API calls
python runner.py --all       # the whole lab, resumable
python report.py             # regenerate this file
python ..\\..\\..\\scripts\\check_week04.py .
```
"""


def part3(rows):
    fail = failure_row(rows)
    return f"""## 3. FIPA-ACL against the three conditions

| | FIPA-ACL (2002) | `free` | `tagged` | `structured` |
|---|---|---|---|---|
| where the illocutionary force lives | a mandatory `performative` parameter in the message envelope, one of the 22 acts in SC00037J | nowhere in the message; it has to be recovered from the conversation | a parenthesised tag at the head of the message, one of four | the `performative` field of a JSON object, one of four |
| what the content language is | declared per message by `:language` with an `:ontology`; SL or KIF in practice | English prose | English prose after the tag | JSON, one field: `content.price`, a whole number or null |
| who interprets the content | the receiving agent, against the FP and RE written for each act | an LLM reader, given the whole transcript, labelling the last message | a regex for the act; the LLM reader only for the price inside a `propose` | `json.loads`, no model involved |
| how a conversation ends | an interaction protocol terminates it (accept-proposal, reject-proposal, failure) | identical in all three by construction: an `accept-proposal` with a price on the table, a `refuse`, or the 8-message limit | ← | ← |
| what guarantees sincerity | nothing observable. The FP demands the sender believe what it says, and that belief never travels with the message — Wooldridge's semantic verification problem | nothing. The private limit sits in the prompt, and only the outcome check catches a breach afterwards | ← | ← |
| what a message costs to read | a parse; no inference | one model call per message | a regex, plus one call per `propose` | a parse; no model call |
| which failure modes appear | force is explicit and cheap to read, but the semantics cannot be checked from outside | {fail[0]} | {fail[1]} | {fail[2]} |

The fourth and fifth rows are identical across the three conditions on
purpose: the termination rule and the absence of any sincerity guarantee are
held fixed so that the differences in part 2 can only come from the format and
the reader.
"""


def main():
    results = HERE / "results.csv"
    if not results.is_file():
        print("results.csv not found — run the lab first (python runner.py --all)")
        return 1
    scenarios = summarize.json.loads((HERE / "scenarios.json").read_text(encoding="utf-8")) \
        if hasattr(summarize, "json") else __import__("json").loads(
            (HERE / "scenarios.json").read_text(encoding="utf-8"))
    rows = load_rows(results)
    episodes = parse_logs(HERE / "logs")

    kept = ""
    existing = HERE / "REPORT.md"
    if existing.is_file():
        text = existing.read_text(encoding="utf-8")
        if KEEP_START in text and KEEP_END in text:
            kept = text.split(KEEP_START, 1)[1].split(KEEP_END, 1)[0].strip("\n")
    if not kept.strip():
        kept = ("TODO — one paragraph. Which condition moved which metric, and why. "
                "Quote the lines below.")

    doc = f"""# Week 04 — Speech acts in practice: free, tagged, and structured negotiation

{part1(scenarios)}
## 2. Results

### Per condition

{summarize.per_condition(rows)}

### Per episode

{summarize.per_episode(rows)}

{part3(rows)}
## 4. Interpretation

{KEEP_START}

{kept}

{KEEP_END}

### Evidence from the logs

{evidence(rows, episodes)}"""

    existing.write_text(doc, encoding="utf-8")
    print(f"REPORT.md written: {len(rows)} episode(s), {len(episodes)} episode log(s).")
    print("Parts 1-3 and the evidence are generated. Part 4 is yours: write it between "
          "the two markers and run this again to keep it.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
