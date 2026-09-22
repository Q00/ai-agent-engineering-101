"""Offline checks, no model calls.

Everything here is about the parts of the harness that decide what a number
in results.csv means. The runs themselves cannot be tested, but the layer
that turns a message into a row can, and getting it wrong would be invisible
in the results and fatal to them.

  python test_offline.py
"""

import sys

import model
import negotiate
import prompts
import protocol


class Fail(AssertionError):
    pass


CHECKS = []


def check(name):
    def deco(fn):
        CHECKS.append((name, fn))
        return fn
    return deco


def eq(got, want, what=""):
    if got != want:
        raise Fail(f"{what}: got {got!r}, wanted {want!r}")


def _no_chat(*a, **k):
    raise AssertionError("structured must not call the model")


# --- the structured parser ----------------------------------------------

@check("structured: a bare object reads with no model call")
def _():
    r = protocol.read_structured('{"performative": "propose", "content": {"price": 120}}')
    eq((r.ok, r.performative, r.price, r.reader_calls), (True, "propose", 120, 0))


@check("structured: an object inside a code fence still reads")
def _():
    r = protocol.read_structured('```json\n{"performative": "refuse", '
                                 '"content": {"price": null}}\n```')
    eq((r.ok, r.performative), (True, "refuse"))


@check("structured: prose after the object is ignored, and the log says so")
def _():
    r = protocol.read_structured('{"performative": "reject-proposal", '
                                 '"content": {"price": null}}\nBut I need $50.')
    eq(r.ok, True)
    eq(r.performative, "reject-proposal")
    if "embedded" not in r.how:
        raise Fail(f"how should record the embedding, got {r.how!r}")


@check("structured: a propose with no price is a format error, not a silent zero")
def _():
    r = protocol.read_structured('{"performative": "propose", "content": {"price": null}}')
    eq((r.ok, r.price), (False, None))


@check("structured: an invented performative is a format error")
def _():
    r = protocol.read_structured('{"performative": "cfp", "content": {"price": 10}}')
    eq(r.ok, False)


@check("structured: plain prose is a format error")
def _():
    eq(protocol.read_structured("I can go to 120.").ok, False)


# --- the tagged parser ---------------------------------------------------

@check("tagged: a non-propose tag costs no reader call")
def _():
    r = protocol.read_tagged("(refuse) too far apart.", model.Meter(), _no_chat)
    eq((r.ok, r.performative, r.reader_calls), (True, "refuse", 0))


@check("tagged: a propose costs exactly one reader call")
def _():
    calls = []

    def chat(system, messages, meter, kind="agent"):
        calls.append(kind)
        meter.add(0, 0, kind)
        return '{"price": 45}'

    m = model.Meter()
    r = protocol.read_tagged("(propose) let me counter at 45", m, chat)
    eq((r.ok, r.price, r.reader_calls), (True, 45, 1))
    eq((calls, m.reader_calls, m.agent_calls), (["reader"], 1, 0))


@check("tagged: a message with no tag is a format error")
def _():
    eq(protocol.read_tagged("I can go to 120.", model.Meter(), _no_chat).ok, False)


@check("tagged: a tag outside the four acts is a format error")
def _():
    eq(protocol.read_tagged("(query-ref) what are you asking?",
                            model.Meter(), _no_chat).ok, False)


# --- the free reader -----------------------------------------------------

@check("free: the reader sees the whole transcript and labels the last message")
def _():
    seen = {}

    def chat(system, messages, meter, kind="agent"):
        seen["text"] = messages[-1]["content"]
        meter.add(0, 0, kind)
        return '{"performative": "propose", "price": 120}'

    history = [{"who": "buyer", "text": "what is your asking price?"},
               {"who": "seller", "text": "I can go to 120 for the bicycle."}]
    r = protocol.read_free(history, model.Meter(), chat)
    eq((r.ok, r.performative, r.price, r.reader_calls), (True, "propose", 120, 1))
    if "what is your asking price?" not in seen["text"]:
        raise Fail("the reader was not given the earlier turns")


@check("free: a reader that answers with something else is a format error")
def _():
    def chat(system, messages, meter, kind="agent"):
        meter.add(0, 0, kind)
        return "It looks like a proposal to me."

    eq(protocol.read_free([{"who": "buyer", "text": "hi"}], model.Meter(), chat).ok, False)


# --- what a row means ----------------------------------------------------

def _ep(scenario, outcome, price, turns=2):
    ep = negotiate.Episode(scenario, "free")
    ep.outcome, ep.price, ep.turns = outcome, price, turns
    return ep


POSSIBLE = {"id": "p", "item": "x", "reserve": 60, "budget": 140}
IMPOSSIBLE = {"id": "i", "item": "x", "reserve": 130, "budget": 120}


@check("a deal inside both limits on a possible scenario is correct")
def _():
    ep = _ep(POSSIBLE, "deal", 100)
    eq((ep.correct, ep.violation, ep.deal_possible), (1, 0, 1))


@check("a deal below the reserve is a violation and not correct")
def _():
    ep = _ep(POSSIBLE, "deal", 55)
    eq((ep.correct, ep.violation), (0, 1))


@check("a deal above the budget is a violation and not correct")
def _():
    ep = _ep(POSSIBLE, "deal", 150)
    eq((ep.correct, ep.violation), (0, 1))


@check("any deal on an impossible scenario is a violation")
def _():
    ep = _ep(IMPOSSIBLE, "deal", 125)
    eq((ep.correct, ep.violation), (0, 1))


@check("walking away is correct only when no deal was possible")
def _():
    eq(_ep(IMPOSSIBLE, "no_deal", None).correct, 1)
    eq(_ep(POSSIBLE, "no_deal", None).correct, 0)


@check("running out of turns is never correct, on either kind of scenario")
def _():
    eq(_ep(IMPOSSIBLE, "open", None, turns=8).correct, 0)
    eq(_ep(POSSIBLE, "open", None, turns=8).correct, 0)


@check("an unpriced deal is neither correct nor a countable violation")
def _():
    ep = _ep(POSSIBLE, "deal", None)
    eq((ep.correct, ep.violation), (0, 0))


# --- the loop ------------------------------------------------------------

@check("the loop stops at the turn limit and reports open")
def _():
    def chat(system, messages, meter, kind="agent"):
        meter.add(0, 0, kind)
        if "You label messages" in system:
            return '{"performative": "reject-proposal", "price": null}'
        return "no thanks"

    ep = negotiate.run_episode(POSSIBLE, "free", model.Meter(), chat, lambda *a: None)
    eq((ep.outcome, ep.turns), ("open", negotiate.TURN_LIMIT))


@check("an accept is priced from the other side's last proposal, not its own")
def _():
    script = ['{"performative": "propose", "content": {"price": 70}}',
              '{"performative": "propose", "content": {"price": 110}}',
              '{"performative": "accept-proposal", "content": {"price": null}}']
    it = iter(script)

    def chat(system, messages, meter, kind="agent"):
        meter.add(0, 0, kind)
        return next(it)

    ep = negotiate.run_episode(POSSIBLE, "structured", model.Meter(), chat, lambda *a: None)
    eq((ep.outcome, ep.price, ep.turns), ("deal", 110, 3))
    eq((ep.correct, ep.violation), (1, 0))


@check("an accept with nothing priced behind it is recorded, not discarded")
def _():
    script = ['{"performative": "reject-proposal", "content": {"price": null}}',
              '{"performative": "accept-proposal", "content": {"price": null}}']
    it = iter(script)

    def chat(system, messages, meter, kind="agent"):
        meter.add(0, 0, kind)
        return next(it)

    ep = negotiate.run_episode(POSSIBLE, "structured", model.Meter(), chat, lambda *a: None)
    eq((ep.outcome, ep.price, ep.correct, ep.violation), ("deal", None, 0, 0))
    if not ep.note:
        raise Fail("the note has to say why the deal has no price")


@check("an unreadable message is counted and still delivered to the other agent")
def _():
    seen = []

    def chat(system, messages, meter, kind="agent"):
        meter.add(0, 0, kind)
        seen.append(list(messages))
        if len(seen) == 1:
            return "I can go to 90."           # prose in the structured condition
        return '{"performative": "refuse", "content": {"price": null}}'

    ep = negotiate.run_episode(POSSIBLE, "structured", model.Meter(), chat, lambda *a: None)
    eq((ep.format_errors, ep.outcome), (1, "no_deal"))
    if not any(m["content"] == "I can go to 90." for m in seen[1]):
        raise Fail("the unreadable message was not passed on to the seller")


@check("structured spends no reader calls at all")
def _():
    m = model.Meter()

    def chat(system, messages, meter, kind="agent"):
        meter.add(0, 0, kind)
        return '{"performative": "propose", "content": {"price": 80}}'

    ep = negotiate.run_episode(POSSIBLE, "structured", m, chat, lambda *a: None)
    eq((ep.reader_calls, m.reader_calls), (0, 0))


# --- extension 1: the reader may decline ---------------------------------

@check("ext1: the first run's reader prompt is untouched by the extensions")
def _():
    eq(prompts.reader_prompt(), prompts.READER)
    eq(prompts.reader_prompt(vocab=4, abstain=False), prompts.READER)
    if "whatever the message says" not in prompts.READER:
        raise Fail("the first run's forcing clause is gone from READER")
    if "whatever the message says" in prompts.READER_ABSTAIN:
        raise Fail("the abstaining reader must not keep the forcing clause")


@check("ext1: `unclear` is a reading, not a format error")
def _():
    def chat(system, messages, meter, kind="agent"):
        meter.add(0, 0, kind)
        return '{"performative": "unclear", "price": null}'

    r = protocol.read_free([{"who": "buyer", "text": "what are you asking?"}],
                           model.Meter(), chat, abstain=True)
    eq((r.ok, r.unclear, r.performative, r.reader_calls), (True, True, None, 1))


@check("ext1: without --abstain, `unclear` is still an invalid label")
def _():
    def chat(system, messages, meter, kind="agent"):
        meter.add(0, 0, kind)
        return '{"performative": "unclear", "price": null}'

    r = protocol.read_free([{"who": "buyer", "text": "hi"}], model.Meter(), chat)
    eq((r.ok, r.unclear), (False, False))


@check("ext1: an abstention neither ends the episode nor moves the price")
def _():
    def chat(system, messages, meter, kind="agent"):
        meter.add(0, 0, kind)
        if "You label messages" in system:
            return '{"performative": "unclear", "price": null}'
        return "what are you asking for it?"

    ep = negotiate.run_episode(POSSIBLE, "free", model.Meter(), chat,
                               lambda *a: None, abstain=True)
    eq((ep.outcome, ep.turns), ("open", negotiate.TURN_LIMIT))
    eq((ep.unclear_reads, ep.format_errors), (negotiate.TURN_LIMIT, 0))


# --- extension 2: six acts ------------------------------------------------

@check("ext2: query-ref and cfp parse only when the vocabulary has them")
def _():
    msg = '{"performative": "query-ref", "content": {"price": null}}'
    eq(protocol.read_structured(msg).ok, False)
    eq(protocol.read_structured(msg, protocol.ACTS_6).ok, True)


@check("ext2: a (cfp) tag costs no reader call and is legal at six acts")
def _():
    r = protocol.read_tagged("(cfp) what would you take for it?",
                             model.Meter(), _no_chat, protocol.ACTS_6)
    eq((r.ok, r.performative, r.reader_calls), (True, "cfp", 0))
    eq(protocol.read_tagged("(cfp) what would you take for it?",
                            model.Meter(), _no_chat).ok, False)


@check("ext2: asking a question does not end an episode or set a price")
def _():
    script = ['{"performative": "query-ref", "content": {"price": null}}',
              '{"performative": "propose", "content": {"price": 100}}',
              '{"performative": "accept-proposal", "content": {"price": null}}']
    it = iter(script)

    def chat(system, messages, meter, kind="agent"):
        meter.add(0, 0, kind)
        return next(it)

    ep = negotiate.run_episode(POSSIBLE, "structured", model.Meter(), chat,
                               lambda *a: None, vocab=6)
    eq((ep.outcome, ep.price, ep.turns, ep.format_errors), ("deal", 100, 3, 0))


@check("ext2: the six-act role prompt reaches the agents, the four-act one does not")
def _():
    six = prompts.system_prompt("buyer", POSSIBLE, "tagged", vocab=6)
    four = prompts.system_prompt("buyer", POSSIBLE, "tagged", vocab=4)
    for token in ("query-ref", "cfp", "Exactly six acts"):
        if token not in six:
            raise Fail(f"{token!r} missing from the six-act prompt")
        if token in four:
            raise Fail(f"{token!r} leaked into the four-act prompt")
    if "Exactly four acts" not in four:
        raise Fail("the four-act prompt changed")


def main() -> int:
    bad = 0
    for name, fn in CHECKS:
        try:
            fn()
        except AssertionError as exc:
            bad += 1
            print(f"FAIL  {name}\n        {exc}")
        else:
            print(f"ok    {name}")
    print()
    print(f"{len(CHECKS) - bad}/{len(CHECKS)} passed")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
