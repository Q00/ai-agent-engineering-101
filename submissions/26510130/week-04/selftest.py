"""Offline self-test. No CLI call, no network, writes nothing to results.csv.

Stubs acl.call_model so the three readers, the episode loop, the ending
conditions and the scoring can be checked without spending a model call. It
tests the plumbing; every row in results.csv comes from run_neg.py against the
real CLI.

Usage: python selftest.py
"""
import sys

import acl
import negotiate

FAIL = []


def check(label, got, want):
    ok = got == want
    print(f"  {'ok  ' if ok else 'FAIL'}  {label}: {got!r}" + ("" if ok else f"  (want {want!r})"))
    if not ok:
        FAIL.append(label)


quiet = lambda *_: None

# ------------------------------------------------------------------ helpers
print("price parsing")
check("plain int", acl.as_price(45000), 45000)
check("comma string", acl.as_price("45,000"), 45000)
check("with currency", acl.as_price("45000 KRW"), 45000)
check("null", acl.as_price(None), None)
check("bool is not a price", acl.as_price(True), None)

print("\nstructured reader (no model call)")
m = acl.Meter()
check("valid object", acl.read_structured('{"performative":"propose","content":{"price":40000}}', m, quiet),
      ("propose", 40000, True))
check("code fenced", acl.read_structured('```json\n{"performative":"refuse","content":{"price":null}}\n```', m, quiet),
      ("refuse", None, True))
check("prose instead of json", acl.read_structured("I think 40000 is fair.", m, quiet), (None, None, False))
check("unknown performative", acl.read_structured('{"performative":"cfp","content":{}}', m, quiet), (None, None, False))
check("structured spends no reader call", m.reader_calls, 0)

print("\ntagged reader")
m = acl.Meter()
check("tag + price in text", acl.read_tagged("(propose) I can go to 40000 for this.", m, quiet),
      ("propose", 40000, True))
check("accept tag", acl.read_tagged("(accept-proposal) Deal.", m, quiet),
      ("accept-proposal", None, True))
check("missing tag", acl.read_tagged("How about 40000?", m, quiet), (None, None, False))
check("tag price found without a reader call", m.reader_calls, 0)

# ------------------------------------------------------------- episode loop
SCEN_OK = {"id": "X1", "item": "a thing", "reserve": 300, "budget": 500}
SCEN_NO = {"id": "X2", "item": "a thing", "reserve": 900, "budget": 400}


def script(replies):
    """Serve canned agent replies in order; readers are the real ones."""
    it = iter(replies)

    def stub(system, user, meter, reader=False):
        if reader:
            meter.reader_calls += 1
            return '{"performative":"propose","price":null}'
        meter.agent_calls += 1
        return next(it)
    return stub


real = acl.call_model
try:
    print("\nepisode: deal")
    acl.call_model = script([
        '{"performative":"propose","content":{"price":350}}',      # buyer
        '{"performative":"propose","content":{"price":450}}',      # seller
        '{"performative":"accept-proposal","content":{"price":null}}',  # buyer accepts 450
    ])
    m = acl.Meter()
    ep = negotiate.run_episode(SCEN_OK, "structured", m, quiet)
    check("outcome", ep.outcome, "deal")
    check("price is the other side's last proposal", ep.price, 450)
    check("turns", ep.turns, 3)
    check("scored correct", negotiate.score(ep, SCEN_OK), (1, 0))

    print("\nepisode: refuse when no deal was possible")
    acl.call_model = script([
        '{"performative":"propose","content":{"price":400}}',
        '{"performative":"refuse","content":{"price":null}}',
    ])
    ep = negotiate.run_episode(SCEN_NO, "structured", acl.Meter(), quiet)
    check("outcome", ep.outcome, "no_deal")
    check("refusing an impossible deal is correct", negotiate.score(ep, SCEN_NO), (1, 0))

    print("\nepisode: violation -- deal below the seller's reserve")
    acl.call_model = script([
        '{"performative":"propose","content":{"price":100}}',      # buyer, under reserve 300
        '{"performative":"accept-proposal","content":{"price":null}}',  # seller accepts
    ])
    ep = negotiate.run_episode(SCEN_OK, "structured", acl.Meter(), quiet)
    check("outcome", ep.outcome, "deal")
    check("price", ep.price, 100)
    check("counted as violation, not correct", negotiate.score(ep, SCEN_OK), (0, 1))

    print("\nepisode: turn limit -> open")
    acl.call_model = script(['{"performative":"propose","content":{"price":%d}}' % p
                             for p in (310, 490, 320, 480, 330, 470)])
    ep = negotiate.run_episode(SCEN_OK, "structured", acl.Meter(), quiet)
    check("outcome", ep.outcome, "open")
    check("turns == MAX_TURNS", ep.turns, acl.MAX_TURNS)
    check("open is never scored correct", negotiate.score(ep, SCEN_OK), (0, 0))

    print("\nepisode: unreadable messages are counted, not fatal")
    acl.call_model = script(["I am not going to use your format.",
                             "Neither am I, frankly.",
                             '{"performative":"refuse","content":{"price":null}}'])
    ep = negotiate.run_episode(SCEN_NO, "structured", acl.Meter(), quiet)
    check("format_errors", ep.format_errors, 2)
    check("episode still reached an outcome", ep.outcome, "no_deal")

    print("\nepisode: accept with no standing proposal is not a deal")
    acl.call_model = script(['{"performative":"accept-proposal","content":{"price":null}}',
                             '{"performative":"refuse","content":{"price":null}}'])
    ep = negotiate.run_episode(SCEN_NO, "structured", acl.Meter(), quiet)
    check("outcome", ep.outcome, "no_deal")
    check("the bare accept was counted as a format error", ep.format_errors, 1)

    print("\nreader spend per condition")
    # The stub reader always answers `propose`, so a `free` episode never ends
    # early and runs the full turn limit -- which is the point: free pays one
    # reader call per message, tagged and structured pay none.
    MSG = {"structured": '{"performative":"propose","content":{"price":350}}',
           "tagged": "(propose) I offer 350.",
           "free": "How about 350 for it?"}
    for cond, want in (("structured", 0), ("tagged", 0), ("free", acl.MAX_TURNS)):
        acl.call_model = script([MSG[cond]] * acl.MAX_TURNS)
        m = acl.Meter()
        ep = negotiate.run_episode(SCEN_NO, cond, m, quiet)
        check(f"{cond}: reader_calls over {ep.turns} messages", m.reader_calls, want)
finally:
    acl.call_model = real

print("\nformat paragraphs differ per condition")
check("three distinct format paragraphs", len(set(acl.FORMAT.values())), 3)
check("role prompt differs only by the format paragraph",
      negotiate.system_for("buyer", SCEN_OK, "free").replace(acl.FORMAT["free"], "")
      == negotiate.system_for("buyer", SCEN_OK, "structured").replace(acl.FORMAT["structured"], ""),
      True)
check("the seller's reserve never reaches the buyer",
      str(SCEN_OK["reserve"]) in negotiate.system_for("buyer", SCEN_OK, "free"), False)
check("the buyer's budget never reaches the seller",
      str(SCEN_OK["budget"]) in negotiate.system_for("seller", SCEN_OK, "free"), False)

print("\nFAILED:", FAIL if FAIL else "none")
sys.exit(1 if FAIL else 0)
