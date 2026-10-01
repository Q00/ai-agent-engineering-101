import json
from contract_net import run_tasks

team = [{"name": name} for name in ["A", "B", "C"]]
tasks = [{"id": 1, "desc": "Local counting test", "gold": "A"}]


def check(label, replies, expected):
    def fake_bid(contractor, task):
        return replies[contractor["name"]]

    result = run_tasks(tasks, team, fake_bid, log=lambda message: None)
    for field, value in expected.items():
        assert result[field] == value, (label, field, result)
    print(f"PASS: {label}")


def reply(bid, confidence):
    return json.dumps({
        "bid": bid,
        "confidence": confidence,
        "reason": "Simulated reply for local testing only",
    })


check(
    "Three bids; tie goes to A",
    {name: reply(True, 95) for name in ["A", "B", "C"]},
    {"messages": 7, "correct": 1, "misawards": 0, "unassigned": 0},
)

check(
    "Everyone declines",
    {name: reply(False, 0) for name in ["A", "B", "C"]},
    {"messages": 3, "correct": 0, "misawards": 0, "unassigned": 1},
)

check(
    "Invalid A reply; C wins incorrectly",
    {"A": "not JSON", "B": reply(False, 0), "C": reply(True, 99)},
    {
        "messages": 5,
        "correct": 0,
        "misawards": 1,
        "unassigned": 0,
        "parse_failures": 1,
    },
)

print("All local counting tests passed. No API calls made.")