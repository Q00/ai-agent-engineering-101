"""The protocol layer, one episode, and the runner for all three conditions.

Usage:
    python negotiate.py                 # every condition, three repeats
    python negotiate.py free 1          # one run, for a smoke test or a retry

An episode is one buyer, one seller, and one scenario. The buyer opens and the
two alternate until an act ends the negotiation or the turn limit is reached.
Between them sits the protocol layer: `read` turns one message into an act and
a price, and that is the only thing the three conditions do differently.

Results are appended to results.csv as each episode finishes, and a run already
in that file is skipped, so an interrupted run continues where it stopped.
"""

import csv
import json
import re
import sys
from pathlib import Path

import acl
import backend

HERE = Path(__file__).resolve().parent
SCENARIOS = HERE / "scenarios.json"
RESULTS = HERE / "results.csv"
SINCERITY = HERE / "results_sincerity.csv"
LOGS = HERE / "logs"

HEADER = [
    "run",
    "condition",
    "scenario",
    "deal_possible",
    "outcome",
    "price",
    "correct",
    "violation",
    "turns",
    "format_errors",
    "reader_calls",
    "note",
]
CONDITIONS = ("free", "tagged", "structured")
REPEATS = (1, 2, 3)
MAX_TURNS = 8


# --------------------------------------------------------------- reading a message


def _first_json_object(text: str):
    """The first balanced JSON object in `text`, and the text left outside it.

    A model that was asked for JSON often wraps it in a code fence or adds a
    sentence after it. Finding the object anyway is what makes the structured
    condition cheap; the text left over is what that cheapness costs, so it is
    returned rather than discarded.
    """
    start = text.find("{")
    while start != -1:
        depth = 0
        for i in range(start, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    try:
                        obj = json.loads(text[start : i + 1])
                    except json.JSONDecodeError:
                        break
                    outside = text[:start] + text[i + 1 :]
                    outside = re.sub(r"```+\w*", "", outside).strip()
                    return obj, outside
        start = text.find("{", start + 1)
    return None, text.strip()


def _whole_number(value):
    """`value` as an int when it is a whole number, else None."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        return int(value.strip())
    return None


def _ask_reader(transcript: list, meter: backend.Meter):
    """One reader call over the whole transcript; labels the last message."""
    body = "Label the LAST message only.\n\n" + "\n".join(
        f"[{role}] {text}" for role, text in transcript
    )
    obj, _ = _first_json_object(backend.ask(acl.READER_SYSTEM, body, meter))
    if not isinstance(obj, dict):
        return None, None
    perf = obj.get("performative")
    return (perf if perf in acl.PERFORMATIVES else None), _whole_number(
        obj.get("price")
    )


def read(condition: str, text: str, transcript: list, meter: backend.Meter):
    """Read one message.

    Returns (performative, price, ok, leftover_prose, read_by), where `read_by`
    names what actually named the act, so a log line never credits the reader
    for work a parser did.

    `ok` is False when this layer could not name the act, which the episode
    counts in format_errors and otherwise ignores: an unread message is still
    delivered to the other agent, exactly as an unparsed one would be.
    """
    if condition == "structured":
        obj, outside = _first_json_object(text)
        if not isinstance(obj, dict):
            return None, None, False, outside, "parser"
        perf = obj.get("performative")
        if perf not in acl.PERFORMATIVES:
            return None, None, False, outside, "parser"
        content = obj.get("content")
        price = (
            _whole_number(content.get("price")) if isinstance(content, dict) else None
        )
        if perf == "propose" and price is None:
            return perf, None, False, outside, "parser"
        return perf, price, True, outside, "parser"

    if condition == "tagged":
        tag = re.match(r"\s*\(\s*([a-zA-Z-]+)\s*\)", text)
        perf = tag.group(1).lower() if tag else None
        if perf not in acl.PERFORMATIVES:
            return None, None, False, "", "regex"
        if perf != "propose":
            return perf, None, True, "", "regex"
        _, price = _ask_reader(transcript, meter)  # the price only
        if price is None:
            return perf, None, False, "", "regex+reader"
        return perf, price, True, "", "regex+reader"

    perf, price = _ask_reader(transcript, meter)  # free: the act and the price
    if perf is None:
        return None, None, False, "", "reader"
    if perf == "propose" and price is None:
        return perf, None, False, "", "reader"
    return perf, price, True, "", "reader"


# --------------------------------------------------------------------- one episode


def run_episode(condition: str, scenario: dict, run: str, log, pressure=False) -> dict:
    """One negotiation. Returns the results.csv row as a dict."""
    item, reserve, budget = scenario["item"], scenario["reserve"], scenario["budget"]
    meter = backend.Meter()
    row = {
        "run": run,
        "condition": condition,
        "scenario": scenario["id"],
        "deal_possible": int(reserve <= budget),
        "outcome": "",
        "price": "",
        "correct": "",
        "violation": "",
        "turns": 0,
        "format_errors": 0,
        "reader_calls": 0,
        "note": "",
    }

    sessions = {
        "buyer": backend.Session(
            acl.system_prompt("buyer", item, budget, condition, pressure), meter
        ),
        "seller": backend.Session(
            acl.system_prompt("seller", item, reserve, condition), meter
        ),
    }
    transcript, last_price = [], {"buyer": None, "seller": None}
    role, other, incoming = "buyer", "seller", acl.OPENING
    outcome = price = None
    unpriced_accepts = 0

    log(
        f"\n--- scenario {scenario['id']} ({item}) reserve={reserve} budget={budget} "
        f"deal_possible={row['deal_possible']}"
    )
    try:
        for _ in range(MAX_TURNS):
            text = sessions[role].send(incoming)
            row["turns"] += 1
            transcript.append((role, text))
            log(f"[{role}] {text}")

            perf, read_price, ok, leftover, read_by = read(
                condition, text, transcript, meter
            )
            log(
                f"  [{read_by}] {{'performative': {perf!r}, 'price': {read_price!r}}}"
                f"{'' if ok else '   # unread, format_errors += 1'}"
            )
            if leftover:
                log(f"           # outside the JSON, dropped: {leftover!r}")

            if not ok:
                row["format_errors"] += 1
            elif perf == "propose":
                last_price[role] = read_price
            elif perf == "accept-proposal":
                if last_price[other] is None:
                    unpriced_accepts += 1
                    log(
                        "           # accept with no recorded price from the other "
                        "side, not a deal"
                    )
                else:
                    outcome, price = "deal", last_price[other]
                    break
            elif perf == "refuse":
                outcome = "no_deal"
                break

            incoming = text
            role, other = other, role
        else:
            outcome = "open"
    except backend.BackendError as err:
        row["reader_calls"] = meter.reader_calls
        row["note"] = f"crashed: {err}"
        log(f"[result] crashed: {err}")
        return row

    row["outcome"] = outcome
    row["reader_calls"] = meter.reader_calls
    if outcome == "deal":
        row["price"] = price
        row["violation"] = int(price < reserve or price > budget)
        row["correct"] = int(row["deal_possible"] and not row["violation"])
    else:
        row["violation"] = 0
        row["correct"] = int(outcome == "no_deal" and not row["deal_possible"])
    notes = [f"agent_calls={meter.agent_calls}", f"tokens={meter.tokens}"]
    if unpriced_accepts:
        notes.append(f"unpriced_accepts={unpriced_accepts}")
    if meter.failures:
        notes.append(f"retried_calls={meter.failures}")
    row["note"] = " ".join(notes)

    log(
        f"[result] outcome={row['outcome']} price={row['price']} "
        f"correct={row['correct']} violation={row['violation']} turns={row['turns']} "
        f"format_errors={row['format_errors']} reader_calls={row['reader_calls']}"
    )
    return row


# ------------------------------------------------------------------------ the runner


def _done_pairs(results: Path) -> set:
    """(run, scenario) pairs already in the results file."""
    if not results.is_file():
        return set()
    with results.open(encoding="utf-8", newline="") as f:
        return {(r["run"], r["scenario"]) for r in csv.DictReader(f)}


def _append(results: Path, row: dict) -> None:
    new = not results.is_file()
    with results.open("a", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=HEADER)
        if new:
            writer.writeheader()
        writer.writerow(row)


def main(argv: list) -> int:
    pressure = "--pressure" in argv
    argv = [a for a in argv if a != "--pressure"]
    results = SINCERITY if pressure else RESULTS
    prefix = "pressure-" if pressure else ""

    scenarios = json.loads(SCENARIOS.read_text(encoding="utf-8"))
    conditions = [argv[0]] if argv else list(CONDITIONS)
    repeats = [int(argv[1])] if len(argv) > 1 else list(REPEATS)
    LOGS.mkdir(exist_ok=True)
    done = _done_pairs(results)

    for condition in conditions:
        for repeat in repeats:
            run = f"{condition}-{repeat}"
            todo = [s for s in scenarios if (run, str(s["id"])) not in done]
            if not todo:
                print(f"{run}: already complete, skipped")
                continue
            path = LOGS / f"{prefix}{run}.txt"
            with path.open("a", encoding="utf-8") as handle:

                def log(line, handle=handle):
                    handle.write(line + "\n")
                    handle.flush()

                log(
                    f"run={run} provider={backend.provider()} model={backend.MODEL} "
                    f"temperature=not settable turn_limit={MAX_TURNS} "
                    f"buyer={'pressure' if pressure else 'neutral'}"
                )
                for scenario in todo:
                    row = run_episode(condition, scenario, run, log, pressure)
                    _append(results, row)
                    print(
                        f"{run} scenario {row['scenario']}: "
                        f"outcome={row['outcome'] or 'crash'} price={row['price']} "
                        f"correct={row['correct']} violation={row['violation']} "
                        f"turns={row['turns']} fmt_err={row['format_errors']} "
                        f"reader={row['reader_calls']}"
                    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
