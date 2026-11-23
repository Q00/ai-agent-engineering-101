"""Two agents, one episode.

An Agent is a role (buyer or seller), a scenario, a condition and one Chat.
It sees the other agent's messages as user turns and its own as assistant
turns. The buyer opens on a fixed kick-off line that is not a message of the
negotiation and is not counted as a turn.

run_episode alternates the two agents until an accept-proposal (deal at the
other side's standing price), a refuse (no deal), or the turn limit (open).
Every message is read by the condition's reader; the reading, not the text,
drives the outcome. Messages the reader cannot parse are counted and the
episode goes on, because the other agent still received the text.

The result carries what results.csv needs plus a note with how the episode
ended, the standing prices, and any protocol error (an accept with no price
on the table), which is a semantic failure and is not a format error.
"""
from dataclasses import dataclass, field

from prompts import system_prompt

KICKOFF = "The negotiation begins. Send your opening message."


class Agent:
    def __init__(self, role: str, scenario: dict, condition: str, max_turns: int,
                 chat_factory, meter):
        self.role = role
        self.system = system_prompt(role, scenario, condition, max_turns)
        self.chat = chat_factory(self.system, meter)

    def speak(self, incoming: str | None) -> str:
        self.chat.add_user(incoming if incoming is not None else KICKOFF)
        return self.chat.send()


@dataclass
class EpisodeResult:
    outcome: str                   # deal | no_deal | open
    price: int | None
    correct: int
    violation: int
    turns: int
    format_errors: int
    reader_calls: int
    notes: list = field(default_factory=list)

    @property
    def note(self) -> str:
        return "; ".join(self.notes)


def judge(scenario: dict, outcome: str, price: int | None) -> tuple[int, int]:
    """correct: a deal exactly when reserve <= budget, at a price inside both
    limits; a no_deal exactly when reserve > budget. open is never correct.
    violation: a deal below the reserve or above the budget."""
    possible = scenario["reserve"] <= scenario["budget"]
    if outcome == "deal":
        inside = price is not None and scenario["reserve"] <= price <= scenario["budget"]
        return int(possible and inside), int(not inside)
    if outcome == "no_deal":
        return int(not possible), 0
    return 0, 0


def run_episode(scenario: dict, condition: str, max_turns: int, chat_factory,
                agent_meter, reader, log) -> EpisodeResult:
    buyer = Agent("buyer", scenario, condition, max_turns, chat_factory, agent_meter)
    seller = Agent("seller", scenario, condition, max_turns, chat_factory, agent_meter)
    speaker, listener = buyer, seller
    standing = {"buyer": None, "seller": None}    # last price each side proposed
    incoming = None
    turns = format_errors = reader_calls = protocol_errors = 0
    outcome, price, notes = "open", None, []

    while turns < max_turns:
        text = speaker.speak(incoming)
        turns += 1
        log(f"[t{turns:02d} {speaker.role:6}] {text}")
        r = reader.read(text, speaker.role, incoming)
        reader_calls += r.reader_calls
        log(f"      read -> {r}")
        if r.error:
            format_errors += 1
        if r.performative == "propose" and r.price is not None:
            standing[speaker.role] = r.price
        elif r.performative == "accept-proposal":
            on_table = standing[listener.role]
            if on_table is None:
                protocol_errors += 1
                msg = f"t{turns} {speaker.role} accepted with no price on the table"
                notes.append(msg)
                log(f"      PROTOCOL-ERROR: {msg}; episode continues")
            else:
                if r.price is not None and r.price != on_table:
                    notes.append(f"t{turns} accept named {r.price} but the standing price was {on_table}")
                outcome, price = "deal", on_table
                notes.insert(0, f"ended t{turns} by {speaker.role} accept-proposal")
                break
        elif r.performative == "refuse":
            outcome = "no_deal"
            notes.insert(0, f"ended t{turns} by {speaker.role} refuse")
            break
        incoming = text
        speaker, listener = listener, speaker

    if outcome == "open":
        notes.insert(0, f"turn limit {max_turns} reached")
    notes.append(f"standing buyer={standing['buyer']} seller={standing['seller']}")
    if protocol_errors:
        notes.append(f"protocol_errors={protocol_errors}")
    correct, violation = judge(scenario, outcome, price)
    res = EpisodeResult(outcome, price, correct, violation, turns, format_errors, reader_calls, notes)
    log(f"      RESULT outcome={outcome} price={price} correct={correct} violation={violation} "
        f"turns={turns} format_errors={format_errors} reader_calls={reader_calls} | {res.note}")
    return res
