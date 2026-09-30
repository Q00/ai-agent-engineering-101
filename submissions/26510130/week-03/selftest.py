"""Offline self-test. No API key, no network, writes nothing to results.csv.

Replaces net.ask with a stub so the protocol, the bid parser, the message
count and the three conditions can be checked without spending a request. It
is a test of the plumbing, not a source of results -- every row in results.csv
comes from run_cn.py against a real provider.

Usage: python selftest.py
"""
import json
import sys

import net

FAIL = []


def check(label, got, want):
    ok = got == want
    print(f"  {'ok  ' if ok else 'FAIL'}  {label}: {got!r}" + ("" if ok else f" (want {want!r})"))
    if not ok:
        FAIL.append(label)


# ---------------------------------------------------------------- bid parser
print("parse_bid")
check("clean json", parse := net.parse_bid("a", '{"bid":true,"confidence":80,"reason":"r"}').confidence, 80)
check("code fence", net.parse_bid("a", '```json\n{"bid":true,"confidence":55,"reason":"r"}\n```').confidence, 55)
check("prose around json",
      net.parse_bid("a", 'Sure! {"bid": false, "confidence": 0, "reason": "not mine"} hope that helps').bid, False)
check("reasoning instead of a bid", net.parse_bid("a", "Let me think about this...").parsed, False)
check("json without bid key", net.parse_bid("a", '{"confidence": 90}').parsed, False)
check("confidence clamped high", net.parse_bid("a", '{"bid":true,"confidence":900}').confidence, 100)
check("confidence not a number", net.parse_bid("a", '{"bid":true,"confidence":"high"}').confidence, 0)
check("empty reply", net.parse_bid("a", "").parsed, False)

# ---------------------------------------------------------------- conditions
print("\nbuild()")
for cond, want_distinct in (("baseline", 3), ("homogeneous", 1), ("overconfident", 3)):
    cs = net.build(cond)
    bodies = {c.system.split("You are contractor")[-1].split(".", 1)[-1] for c in cs}
    check(f"{cond}: names", [c.name for c in cs], list(net.NAMES))
    check(f"{cond}: distinct skill prompts", len(bodies), want_distinct)
check("only overconfident has the greedy line",
      [sum("every contract" in c.system for c in net.build(k)) for k in
       ("baseline", "homogeneous", "overconfident")], [0, 0, 1])

# ---------------------------------------------------------------- full pass
print("\nrun_net with a stubbed model")
TASKS = json.loads(open("tasks.json", encoding="utf-8").read())
SKILL_OF = {"alice": "log", "bob": "math", "carol": "text"}
GOLD = {t["id"]: t["gold"] for t in TASKS}


def stub_factory(mode):
    """mode: 'honest' every contractor bids only on its own tasks;
             'greedy-bob' bob bids 95 on everything;
             'garbage' every contractor answers with prose."""
    def stub(system, user, meter):
        meter.add_call(100, 20)
        name = system.split("contractor '")[1].split("'")[0]
        tid = user.split("id: ")[1].split("\n")[0]
        if mode == "garbage":
            return "I think I could probably handle that, let me consider it."
        if mode == "greedy-bob" and name == "bob":
            return '{"bid": true, "confidence": 95, "reason": "I can do anything"}'
        mine = GOLD[tid] == name
        return json.dumps({"bid": mine, "confidence": 80 if mine else 0,
                           "reason": "mine" if mine else "not my speciality"})
    return stub


real_ask = net.ask
try:
    net.ask = stub_factory("honest")
    res, meter = net.run_net(TASKS, net.build("baseline"), log=lambda *_: None)
    check("honest: correct", res.correct, len(TASKS))
    check("honest: misawards", res.misawards, 0)
    check("honest: unassigned", res.unassigned, 0)
    # 6 tasks x (3 announcements + 3 bids + 1 award)
    check("honest: messages", meter.messages, len(TASKS) * 7)

    net.ask = stub_factory("greedy-bob")
    res, _ = net.run_net(TASKS, net.build("overconfident"), log=lambda *_: None)
    check("greedy: correct", res.correct, sum(1 for t in TASKS if t["gold"] == "bob"))
    check("greedy: misawards", res.misawards, sum(1 for t in TASKS if t["gold"] != "bob"))

    net.ask = stub_factory("garbage")
    res, meter = net.run_net(TASKS, net.build("baseline"), log=lambda *_: None)
    check("garbage: unassigned", res.unassigned, len(TASKS))
    check("garbage: unparseable", res.unparseable, len(TASKS) * 3)
    # no bid is a message and no award is sent: announcements only
    check("garbage: messages", meter.messages, len(TASKS) * 3)
finally:
    net.ask = real_ask

print("\nFAILED:", FAIL if FAIL else "none")
sys.exit(1 if FAIL else 0)
