"""Replay Week 04 messages with legacy and explicit pending-offer semantics."""

import ast
import csv
import json
import re
from dataclasses import dataclass
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
MESSAGE = re.compile(r"^\[(buyer|seller) turn=(\d+)\] (.*)$")


@dataclass(frozen=True)
class Event:
    sender: str
    act: str | None
    price: int | None


@dataclass(frozen=True)
class Offer:
    sender: str
    price: int


def simulate(events, pending_only=False):
    """Return outcome, price, invalid accepts, expired offers."""
    last = {"buyer": None, "seller": None}
    pending = None
    invalid_accepts = 0
    expired = 0
    for event in events:
        sender = event.sender
        other = "seller" if sender == "buyer" else "buyer"
        if event.act == "propose":
            assert type(event.price) is int and event.price > 0
            if pending is not None:
                expired += 1
            pending = Offer(sender, event.price)
            last[sender] = event.price
        elif event.act == "reject-proposal":
            if pending is not None and pending.sender == other:
                pending = None
                expired += 1
        elif event.act == "accept-proposal":
            price = (pending.price if pending_only and pending is not None and
                     pending.sender == other else None) if pending_only else last[other]
            if price is not None:
                return "deal", price, invalid_accepts, expired
            invalid_accepts += 1
        elif event.act == "refuse":
            return "no_deal", None, invalid_accepts, expired
        else:
            assert event.act is None
    return "open", None, invalid_accepts, expired


def parse_log(condition, run):
    path = BASE / "logs" / f"{condition}-{run:02d}.txt"
    episodes = {}
    scenario = None
    events = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("[scenario] id="):
            scenario = line.split()[1].split("=", 1)[1]
            events = []
        elif match := MESSAGE.match(line):
            sender, _, raw = match.groups()
            json.loads(raw)  # the raw utterance is preserved, but acts come from the harness
            events.append(Event(sender, None, None))
        elif line.startswith("[tag] "):
            assert events[-1].act is None
            events[-1] = Event(events[-1].sender, line[len("[tag] "):], None)
        elif line.startswith(("[reader] ", "[parser] ")):
            prefix = "[reader] " if line.startswith("[reader] ") else "[parser] "
            parsed = ast.literal_eval(line[len(prefix):])
            assert parsed is not None, (run, scenario, line)
            act, price = parsed
            if condition == "tagged":
                assert events[-1].act == act
            events[-1] = Event(events[-1].sender, act, price)
        elif line.startswith("[result] "):
            episodes[scenario] = (list(events), json.loads(line[len("[result] "):]))
    return episodes


def replay_all():
    results = list(csv.DictReader((BASE / "results.csv").open(newline="", encoding="utf-8")))
    details = []
    for run in range(1, 10):
        condition = ("free", "tagged", "structured")[(run - 1) // 3]
        episodes = parse_log(condition, run)
        for row in (r for r in results if int(r["run"]) == run):
            events, recorded = episodes[row["scenario"]]
            assert len(events) == int(row["turns"])
            legacy = simulate(events)
            strict = simulate(events, pending_only=True)
            assert legacy[:2] == (recorded["outcome"], recorded["price"]), (row, legacy)
            details.append((run, condition, row["scenario"], legacy, strict))
    assert len(details) == 36
    return details


def main():
    details = replay_all()
    differences = [x for x in details if x[3][:2] != x[4][:2]]
    print(f"replayed={len(details)} outcome_differences={len(differences)}")
    print("strict_invalid_accepts=", sum(x[4][2] for x in details))
    print("strict_expired_offers=", sum(x[4][3] for x in details))
    for item in differences:
        print("difference", item)


if __name__ == "__main__":
    main()
