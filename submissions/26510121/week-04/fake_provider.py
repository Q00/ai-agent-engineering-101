"""A deterministic stand-in for the model, used only to check that the metric
counting is right before spending live calls on it.

It is not a simulation of the experiment and its numbers are never reported.
It exists because `outcome`, `correct`, `violation`, `turns`, `format_errors`
and `reader_calls` are the whole result of the lab, and a counting bug would
be invisible in a live log -- especially the failures the reference run says
to expect, which are the ones scripted here:

  id 1  a clean deal, and -- in `free` only -- a reader that misreads the
        seller's counter-offer as an acceptance, so the same moves end one
        turn earlier and at a different price
  id 2  the seller accepts 200 with a reserve of 245: a violation
  id 3  both sides hold their limits and the buyer walks: no deal, and
        correct, because no zone of agreement existed
  id 4  an unreadable opening message, then talk until the turn limit

The two protocol-level failures from the reference run -- a propose whose
price never got recorded, and an accept-proposal with nothing on the table --
are driven by `verify_offline.py` through the `script` argument.
"""
import json

# (performative, price) per message, buyer first, alternating. When a script
# runs out the speaker keeps rejecting, which is how an episode reaches the
# turn limit and ends `open`. Keyed by the `id` in scenarios.json.
SCRIPTS = {
    1: [("propose", 150), ("propose", 200), ("accept-proposal", None)],
    2: [("propose", 200), ("accept-proposal", None)],
    3: [("propose", 350), ("propose", 420), ("refuse", None)],
    4: [("__malformed__", None), ("propose", 880), ("reject-proposal", None)],
}

# which reader call gets it wrong, per scenario. Only `free` puts a reader on
# every message, so only `free` can show this.
MISREAD_AT = {1: 2}

PROSE = {
    "propose": "I could go to {price} for that.",
    "accept-proposal": "That works for me, let us do it.",
    "reject-proposal": "That is not something I can work with.",
    "refuse": "I will leave it there, thanks.",
    "__malformed__": "What are you asking for it?",
}


def _render(act, price, condition):
    """The same move, written the way each condition requires."""
    if act == "__malformed__":
        # unreadable on purpose in every condition: no tag, not JSON, and not
        # one of the four acts
        return PROSE[act]
    if condition == "structured":
        return json.dumps({"performative": act, "content": {"price": price}})
    prose = (PROSE[act].format(price=price) if price is not None
             else "I could go a little further on that.")
    return prose if condition == "free" else f"({act}) {prose}"


def make_fake_ask(scenario, condition, script=None):
    """An `ask` with the same signature as `chat.ask`, for one episode.

    State is per-episode because this is called once per episode: the message
    counter, the reader counter, and the map from emitted text back to the
    move it stood for. The reader is given whatever the real reader is given
    -- the whole transcript in `free`, one message in `tagged` -- so it finds
    the move by taking the last emitted text that appears in what it was
    shown, exactly as a real reader is told to label the last message.
    """
    sid = scenario["id"]
    moves = list(script if script is not None else SCRIPTS.get(sid, []))
    state = {"sent": 0, "read": 0}
    emitted = {}

    def _last_move_in(seen):
        best, where = None, -1
        for text, move in emitted.items():
            at = seen.rfind(text)
            if at > where:
                best, where = move, at
        return best or ("__malformed__", None)

    def ask(system, messages, meter, role=None):
        meter.add(80, 25)
        if role == "reader":
            state["read"] += 1
            act, price = _last_move_in(messages[-1]["content"])
            if condition == "free" and state["read"] == MISREAD_AT.get(sid):
                # the finding the lab is looking for: the force was read out
                # of context, and read wrong
                return json.dumps({"performative": "accept-proposal", "price": None})
            if act == "__malformed__":
                return json.dumps({"performative": "unclear", "price": None})
            return json.dumps({"performative": act, "price": price})

        i = state["sent"]
        state["sent"] += 1
        act, price = moves[i] if i < len(moves) else ("reject-proposal", None)
        text = _render(act, price, condition)
        emitted[text] = (act, price)
        return text

    return ask
