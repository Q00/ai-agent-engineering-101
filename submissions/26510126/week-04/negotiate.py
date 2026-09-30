"""One episode: two agents, one scenario, one message format.

The loop is small and the judgements in it are the ones that decide what the
results mean, so each is written down where it is made rather than left to
the reader of the CSV.

The turn limit is on messages, not on rounds. Eight messages is four chances
each, which is what the reference run used.
"""

import prompts
import protocol

TURN_LIMIT = 8


class Episode:
    """The record of one negotiation, whatever way it ended."""

    def __init__(self, scenario: dict, condition: str):
        self.scenario = scenario
        self.condition = condition
        self.outcome = "open"
        self.price = None
        self.turns = 0
        self.format_errors = 0
        self.reader_calls = 0
        # Extension 1 only. Messages the reader was allowed to decline to
        # label. Zero by construction in the first run, where it could not.
        self.unclear_reads = 0
        self.note = ""

    @property
    def deal_possible(self) -> int:
        return 1 if self.scenario["reserve"] <= self.scenario["budget"] else 0

    @property
    def violation(self) -> int:
        """A deal struck outside a private limit.

        Only a priced deal can violate a limit. An unpriced one cannot be
        checked, and counting it as clean would flatter the condition that
        produced it, so it is left at 0 and the note says why.
        """
        if self.outcome != "deal" or self.price is None:
            return 0
        s = self.scenario
        return 1 if (self.price < s["reserve"] or self.price > s["budget"]) else 0

    @property
    def correct(self) -> int:
        """A deal exactly when one was possible, at a price inside both limits.

        `open` is never correct. The agents did not conclude anything, and an
        episode that runs out of turns on an impossible scenario has not
        worked out that the scenario is impossible; it has only run out of
        turns. Counting it with the clean walk-aways would let a condition
        earn credit for stalling.
        """
        s = self.scenario
        if self.outcome == "deal":
            if self.price is None:
                return 0
            return 1 if self.deal_possible and s["reserve"] <= self.price <= s["budget"] else 0
        if self.outcome == "no_deal":
            return 1 if not self.deal_possible else 0
        return 0


def run_episode(scenario: dict, condition: str, meter, chat, log,
                vocab: int = 4, abstain: bool = False) -> Episode:
    """Buyer opens. Each message goes to the reader, then to the other agent.

    The defaults are the first run. vocab=6 restores query-ref and cfp,
    abstain=True lets the free reader answer `unclear`.
    """
    ep = Episode(scenario, condition)
    acts = protocol.ACTS_6 if vocab == 6 else protocol.ACTS

    systems = {role: prompts.system_prompt(role, scenario, condition, vocab)
               for role in ("buyer", "seller")}
    # Each agent's own view: its messages are assistant turns, the other
    # side's are user turns. The two lists are never shared.
    views = {"buyer": [], "seller": []}
    # The shared transcript, which only the free reader is allowed to see.
    transcript = []
    # The last price each side put on the table as a proposal. An
    # accept-proposal is priced from the *other* side's entry.
    last_propose = {"buyer": None, "seller": None}

    speaker = "buyer"
    while ep.turns < TURN_LIMIT:
        other = "seller" if speaker == "buyer" else "buyer"

        # The opening message has no incoming turn to answer, so the agent is
        # handed the one instruction that is not in its system prompt.
        view = views[speaker]
        if not view:
            view = [{"role": "user", "content":
                     "Open the negotiation." if speaker == "buyer"
                     else "The buyer is waiting for your reply."}]

        text = (chat(systems[speaker], view, meter, kind="agent") or "").strip()
        ep.turns += 1
        transcript.append({"who": speaker, "text": text})
        log(f"    [{ep.turns}] {speaker}: {text}")

        reading = protocol.read(condition, text, transcript, meter, chat,
                                acts, abstain)
        ep.reader_calls += reading.reader_calls
        if reading.unclear:
            ep.unclear_reads += 1
            log(f"        no label ({reading.how})")
        elif not reading.ok:
            ep.format_errors += 1
            log(f"        unreadable ({reading.how})")
        else:
            log(f"        read as {reading.performative}"
                + (f" @ {reading.price}" if reading.price is not None else "")
                + f"  [{reading.how}]")

        # The message is delivered whatever the protocol layer made of it.
        views[speaker].append({"role": "assistant", "content": text})
        views[other].append({"role": "user", "content": text})

        if reading.ok and reading.performative == "propose" and reading.price is not None:
            last_propose[speaker] = reading.price

        if reading.ok and reading.performative == "accept-proposal":
            ep.outcome = "deal"
            ep.price = last_propose[other]
            if ep.price is None:
                ep.note = ("accept-proposal with no priced proposal from the other "
                           "side on record; the deal has no price to check")
                log(f"        DEAL, but no price on record for {other}")
            else:
                log(f"        DEAL at {ep.price} (last {other} proposal)")
            break

        if reading.ok and reading.performative == "refuse":
            ep.outcome = "no_deal"
            log("        NO DEAL, walked away")
            break

        speaker = other

    if ep.outcome == "open":
        log(f"        OPEN, hit the {TURN_LIMIT}-message limit")

    s = scenario
    log(f"    result: outcome={ep.outcome} price={ep.price} "
        f"correct={ep.correct} violation={ep.violation} turns={ep.turns} "
        f"format_errors={ep.format_errors} reader_calls={ep.reader_calls} "
        f"unclear_reads={ep.unclear_reads} "
        f"(reserve={s['reserve']} budget={s['budget']})")
    return ep
