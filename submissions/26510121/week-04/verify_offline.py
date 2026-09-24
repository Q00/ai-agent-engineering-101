"""Check the counting against known answers, with no provider and no key.

Run this before every live run. The lab is about 230 model calls spread over
several days; a bug in `judge` or in `reader_calls` found on day four cannot
be paid for a second time.

Five things are checked:

  1. twelve scripted episodes (three conditions x four scenarios) against a
     hand-worked expected table. The same moves are played in all three
     conditions, so every difference in the table is caused by the format and
     the reader -- which is the claim the report has to make.
  2. `judge` against its truth table.
  3. the two protocol-level failures the reference run reports: a propose
     whose price never got recorded, and an accept-proposal with nothing on
     the table. Neither is a format error and neither ends the episode.
  4. a message the layer cannot read is still delivered to the other side.
  5. the results.csv contract: the exact header CI checks for, and the resume
     that lets an interrupted run continue.

  python verify_offline.py
"""
import csv
import sys
import tempfile
from pathlib import Path

import chat
import fake_provider
import protocol
import runner

TURN_LIMIT = runner.TURN_LIMIT      # 8

# condition -> scenario id -> the columns that must come out. Worked by hand
# from SCRIPTS in fake_provider.py; see that file for why each script exists.
EXPECTED = {
    "free": {
        # the reader misreads the seller's counter-offer as an acceptance, so
        # the episode ends a turn early and at the buyer's price, not the
        # seller's. Still "correct": a misread can land on a sane outcome,
        # which is why violation is counted separately from correct.
        1: dict(outcome="deal", price=150, correct=1, violation=0,
                turns=2, format_errors=0, reader_calls=2),
        2: dict(outcome="deal", price=200, correct=0, violation=1,
                turns=2, format_errors=0, reader_calls=2),
        3: dict(outcome="no_deal", price="", correct=1, violation=0,
                turns=3, format_errors=0, reader_calls=3),
        4: dict(outcome="open", price="", correct=1, violation=0,
                turns=8, format_errors=1, reader_calls=8),
    },
    "tagged": {
        1: dict(outcome="deal", price=200, correct=1, violation=0,
                turns=3, format_errors=0, reader_calls=2),
        2: dict(outcome="deal", price=200, correct=0, violation=1,
                turns=2, format_errors=0, reader_calls=1),
        3: dict(outcome="no_deal", price="", correct=1, violation=0,
                turns=3, format_errors=0, reader_calls=2),
        # the untagged opening message costs a format error but no call; only
        # the one propose among the eight messages is sent to the reader
        4: dict(outcome="open", price="", correct=1, violation=0,
                turns=8, format_errors=1, reader_calls=1),
    },
    "structured": {
        1: dict(outcome="deal", price=200, correct=1, violation=0,
                turns=3, format_errors=0, reader_calls=0),
        2: dict(outcome="deal", price=200, correct=0, violation=1,
                turns=2, format_errors=0, reader_calls=0),
        3: dict(outcome="no_deal", price="", correct=1, violation=0,
                turns=3, format_errors=0, reader_calls=0),
        4: dict(outcome="open", price="", correct=1, violation=0,
                turns=8, format_errors=1, reader_calls=0),
    },
}

failures = []


def check(label, got, want):
    if got == want:
        print(f"ok    {label}")
    else:
        print(f"FAIL  {label}\n        got  {got}\n        want {want}")
        failures.append(label)


def episode(scenario, condition, script=None, log=None):
    ask = fake_provider.make_fake_ask(scenario, condition, script=script)
    return runner.run_episode(scenario, condition, ask, chat.Meter(), TURN_LIMIT,
                              log or (lambda _line="": None))


def check_episodes(scenarios):
    for condition in ("free", "tagged", "structured"):
        for scenario in scenarios:
            want = EXPECTED[condition].get(scenario["id"])
            if want is None:
                continue
            row = episode(scenario, condition)
            check(f"{condition:<10} id={scenario['id']}", {k: row[k] for k in want}, want)


def check_judge():
    possible = {"id": 0, "item": "x", "reserve": 100, "budget": 200}
    impossible = {"id": 0, "item": "x", "reserve": 300, "budget": 200}
    cases = [
        ("deal inside both limits", possible, "deal", 150, (1, 1, 0)),
        ("deal below the reserve", possible, "deal", 80, (1, 0, 1)),
        ("deal above the budget", possible, "deal", 250, (1, 0, 1)),
        ("no deal when one was possible", possible, "no_deal", None, (1, 0, 0)),
        ("out of turns when one was possible", possible, "open", None, (1, 0, 0)),
        ("no deal when none was possible", impossible, "no_deal", None, (0, 1, 0)),
        ("out of turns when none was possible", impossible, "open", None, (0, 1, 0)),
        ("deal when none was possible", impossible, "deal", 250, (0, 0, 1)),
    ]
    for label, scenario, outcome, price, want in cases:
        check(f"judge: {label}", runner.judge(scenario, outcome, price), want)


def check_reference_failures(scenarios):
    """The two protocol-level failures the reference run names. Both must be
    readable messages -- format_errors 0 -- that simply fail to close the
    episode, which is how it runs out of turns."""
    sc = scenarios[0]                                   # id 1, reserve 120, budget 300

    # a propose whose price never reached the table ("price": null), then an
    # acceptance with nothing to accept
    row = episode(sc, "structured",
                  script=[("propose", None), ("accept-proposal", None)])
    check("structured: propose with price null, then an empty accept",
          {k: row[k] for k in ("outcome", "price", "format_errors", "unmatched_accepts")},
          dict(outcome="open", price="", format_errors=0, unmatched_accepts=1))

    # the same thing in tagged: the tag reads, the price does not
    row = episode(sc, "tagged", script=[("accept-proposal", None)])
    check("tagged: accept-proposal on turn one with nothing on the table",
          {k: row[k] for k in ("outcome", "format_errors", "unmatched_accepts", "turns")},
          dict(outcome="open", format_errors=0, unmatched_accepts=1, turns=8))


def check_unreadable_is_still_delivered(scenarios):
    """"읽지 못한 메시지는 format_errors에 세고, 메시지는 그대로 상대에게
    전달한다" -- the layer observes, it does not filter. The transcript the
    reader sees must therefore contain the unreadable message too."""
    sc = scenarios[3]                                   # id 4, opens unreadably
    seen = []
    row = episode(sc, "free", log=lambda line="": seen.append(line))
    delivered = any("What are you asking for it?" in line for line in seen)
    check("an unreadable message is still delivered", delivered, True)
    check("...and is counted once", row["format_errors"], 1)


def check_csv(scenarios):
    with tempfile.TemporaryDirectory() as tmp:
        results = Path(tmp) / "results.csv"
        row = dict.fromkeys(runner.HEADER, 0)
        row.update(run="free-1", condition="free", scenario=str(scenarios[0]["id"]),
                   outcome="deal", price=150, note="tokens=1 calls=1")
        runner.append_row(results, row)
        with results.open(encoding="utf-8", newline="") as f:
            rows = list(csv.reader(f))
        check("results.csv header", rows[0], runner.HEADER)
        check("results.csv row width", len(rows[1]), len(runner.HEADER))
        check("resume sees the written pair",
              ("free-1", str(scenarios[0]["id"])) in runner.done_pairs(results), True)
        check("resume ignores an unwritten pair",
              ("free-2", str(scenarios[0]["id"])) in runner.done_pairs(results), False)


def check_transcript():
    check("the reader is shown the conversation, last message last",
          protocol.render_transcript([("buyer", "one"), ("seller", "two")]),
          "buyer: one\nseller: two")


def main():
    scenarios = runner.json.loads((runner.HERE / "scenarios.json").read_text(encoding="utf-8"))
    print(f"turn limit {TURN_LIMIT}, {len(scenarios)} scenarios\n")
    print("episodes (three conditions x four scenarios, same moves in each)")
    check_episodes(scenarios)
    print("\njudge()")
    check_judge()
    print("\nthe reference run's protocol-level failures")
    check_reference_failures(scenarios)
    print("\nthe protocol layer observes, it does not filter")
    check_unreadable_is_still_delivered(scenarios)
    check_transcript()
    print("\nresults.csv contract")
    check_csv(scenarios)

    print()
    if failures:
        print(f"{len(failures)} check(s) failed")
        return 1
    print("all offline checks passed -- the counting is safe to spend calls on")
    return 0


if __name__ == "__main__":
    sys.exit(main())
