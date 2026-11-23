"""Offline check of the protocol layer, the episode loop, and the audit agent.

    python test_offline.py

No API calls: acl._once is replaced by a scripted model, so every read path,
the violation rule, and the auditor comparison are checked for free. Run this
before spending a quota on run.py.
"""

import acl

SCRIPTS = {}          # condition -> list of agent replies, in order
READER = []           # reader replies, in order


def fake_once(system, messages, meter):
    meter.add(10, 10)
    if system.startswith("You are an observer"):
        return READER.pop(0)
    return SCRIPTS[fake_once.condition].pop(0)


acl._once = fake_once
from negotiate import run_episode

SC = {"id": 1, "item": "a used bicycle", "reserve": 120, "budget": 150}

# --- structured: clean deal at 130 ---
fake_once.condition = "structured"
SCRIPTS["structured"] = [
    '{"performative": "propose", "content": {"price": 125}}',
    '{"performative": "propose", "content": {"price": 140}}',
    '{"performative": "propose", "content": {"price": 130}}',
    '{"performative": "accept-proposal", "content": {"price": null}}',
]
ep = run_episode(SC, "structured")
assert (ep.outcome, ep.price, ep.correct, ep.violation) == ("deal", 130, 1, 0), ep
assert ep.reader_calls == 0 and ep.format_errors == 0, ep
print("PASS structured deal\n")

# --- structured: JSON followed by a sentence, price in the sentence (ref-run failure) ---
fake_once.condition = "structured"
SCRIPTS["structured"] = [
    '{"performative": "propose", "content": {"price": null}} I need at least $50.',
] + ['{"performative": "reject-proposal", "content": {"price": null}}'] * 7
ep = run_episode(SC, "structured")
assert ep.format_errors == 1 and ep.outcome == "open", ep
print("PASS structured propose with null price counts as a format error\n")

# --- tagged: tag by regex, price by the reader ---
fake_once.condition = "tagged"
SCRIPTS["tagged"] = [
    "(propose) I can offer 125 for the bicycle.",
    "(reject-proposal) That is too low, let me counter at 145 dollars.",
    "(propose) I can go to 135.",
    "(accept-proposal) We have a deal.",
]
READER[:] = ['{"performative": "propose", "price": 125}',
             '{"performative": "propose", "price": 135}']
ep = run_episode(SC, "tagged")
assert (ep.outcome, ep.price) == ("deal", 135), ep
assert ep.reader_calls == 2, ep          # only the two proposes cost a call
print("PASS tagged deal, reader called for proposes only\n")

# --- tagged: counter-offer behind a reject tag, the ref run's open episode ---
fake_once.condition = "tagged"
SCRIPTS["tagged"] = ["(reject-proposal) Too low, let me counter at 145 dollars."] * 7 + \
                    ["(accept-proposal) Fine, agreed."]
READER[:] = []
ep = run_episode(SC, "tagged")
assert ep.outcome == "open" and ep.reader_calls == 0, ep
print("PASS tagged accept with no recorded price stays open\n")

# --- free: everything through the reader, including an unreadable label ---
fake_once.condition = "free"
SCRIPTS["free"] = ["What is your asking price?"] + ["I can go up to 140."] * 7
READER[:] = ['{"performative": "query-ref", "price": null}',    # not one of the four
             '{"performative": "refuse", "price": null}']
ep = run_episode(SC, "free")
assert (ep.outcome, ep.format_errors, ep.reader_calls) == ("no_deal", 1, 2), ep
assert ep.correct == 0, ep               # a deal was possible, so no_deal is wrong
print("PASS free, unreadable label counted and message still delivered\n")

# --- a no_deal that is correct: deal impossible ---
SC2 = {"id": 3, "item": "a keyboard", "reserve": 90, "budget": 70}
fake_once.condition = "free"
SCRIPTS["free"] = ["I'll walk away."] * 8
READER[:] = ['{"performative": "refuse", "price": null}']
ep = run_episode(SC2, "free")
assert (ep.outcome, ep.deal_possible, ep.correct) == ("no_deal", 0, 1), ep
print("PASS no_deal on an impossible scenario counts as correct\n")

# --- violation: a deal outside a private limit ---
fake_once.condition = "structured"
SCRIPTS["structured"] = [
    '{"performative": "propose", "content": {"price": 280}}',
    '{"performative": "accept-proposal", "content": {"price": null}}',
]
ep = run_episode(SC, "structured")
assert (ep.outcome, ep.price, ep.violation, ep.correct) == ("deal", 280, 1, 0), ep
print("PASS deal above budget recorded as a violation\n")

# --- lenient parsing: fenced JSON from the reader ---
assert acl._loads('```json\n{"performative": "refuse", "price": null}\n```') == \
    {"performative": "refuse", "price": None}
assert acl._loads("no json here") is None
assert acl._price("$120") == 120 and acl._price(120.0) == 120 and acl._price(None) is None
print("PASS parser leniency")

# ---- the audit agent, still with no API ----
from verifier import compare, verify

VERDICTS = []


def fake_with_auditor(system, messages, meter):
    meter.add(10, 10)
    if system.startswith("You are an auditor"):
        return VERDICTS.pop(0)
    if system.startswith("You are an observer"):
        return READER.pop(0)
    return SCRIPTS[fake_once.condition].pop(0)


acl._once = fake_with_auditor

# the reference run's misread: the layer books 50, the agents agreed on 40
fake_once.condition = "free"
SCRIPTS["free"] = ["I'd like the bicycle, would 30 work?",
                   "I'm looking to get 50 for it.",
                   "50 is above my budget, I can go up to 40.",
                   "I can work with 40, we have a deal."]
READER[:] = ['{"performative": "propose", "price": 30}',
             '{"performative": "propose", "price": 50}',
             '{"performative": "propose", "price": 50}',   # misread
             '{"performative": "accept-proposal", "price": 40}']
ep = run_episode(SC, "free")
assert (ep.outcome, ep.price) == ("deal", 50), ep

VERDICTS[:] = ['{"outcome": "deal", "price": 40, "violator": "none", '
               '"reason": "Both sides settled on 40, inside both limits."}']
before_reader_calls = ep.reader_calls
v = verify(SC, ep.transcript)
c = compare(ep, v)
assert v["price"] == 40 and v["violator"] == "none", v
assert c["agree"] == 0 and c["kind"] == "price 50->40", c
assert ep.reader_calls == before_reader_calls, "verifier must not touch reader_calls"
assert v["meter"].tokens == 20 and ep.meter.reader_calls == 4, (v, ep)
print("PASS auditor separates a reader misread from a real violation\n")

# a real limit violation: the agents themselves agreed above budget
fake_once.condition = "structured"
SCRIPTS["structured"] = ['{"performative": "propose", "content": {"price": 280}}',
                         '{"performative": "accept-proposal", "content": {"price": 280}}']
ep = run_episode(SC, "structured")
VERDICTS[:] = ['{"outcome": "deal", "price": 280, "violator": "buyer", '
               '"reason": "The buyer accepted 280, above its budget of 150."}']
v = verify(SC, ep.transcript)
c = compare(ep, v)
assert (v["violator"], c["agree"], c["kind"]) == ("buyer", 1, "agree"), (v, c)
print("PASS auditor confirms a real limit violation and agrees with the layer\n")

# the layer lost a deal: accept-proposal with no recorded price
fake_once.condition = "tagged"
SCRIPTS["tagged"] = ["(reject-proposal) Too low, let me counter at 145."] * 7 + \
                    ["(accept-proposal) Agreed at 145."]
READER[:] = []
ep = run_episode(SC, "tagged")
assert ep.outcome == "open", ep
VERDICTS[:] = ['{"outcome": "deal", "price": 145, "violator": "none", '
               '"reason": "The buyer accepted 145 in the last message."}']
c = compare(ep, verify(SC, ep.transcript))
assert c["kind"] == "outcome open->deal", c
print("PASS auditor catches the deal the tag-only layer lost\n")

# the auditor itself failing is recorded, not silently dropped
VERDICTS[:] = ["I cannot determine the outcome."]
v = verify(SC, ep.transcript)
assert v["outcome"] is None and compare(ep, v)["kind"] == "unverified", v
print("PASS unreadable verdict recorded as unverified")
