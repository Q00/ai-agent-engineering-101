"""Week 04 — negotiate.py: one episode (buyer opens, MAX_TURNS turns cap),
optionally with a broker between them.

correct is broader than "deal closed cleanly": a scenario where no deal is
possible (reserve > budget) is answered correctly by no_deal or open too.
See REPORT.md for why (the lecture's own reference run scored some no_deal
episodes as correct for exactly this reason).
"""
from dataclasses import dataclass, field

from agents import broker_commission, broker_mediate, system_prompt
from model import Meter, call_conversation
from protocol import read_message

MAX_TURNS = 8
DEADLOCK_REJECTS = 2  # consecutive reject-proposals before the broker steps in


@dataclass
class Episode:
    outcome: str = "open"          # deal | no_deal | open
    price: int = None
    correct: int = 0
    violation: int = 0
    turns: int = 0
    format_errors: int = 0
    reader_calls: int = 0
    broker_mediations: int = 0
    broker_vetoes: int = 0
    commission: float = 0.0
    note: str = ""


def run_episode(scenario, condition, meter, log=print, use_broker=False):
    ep = Episode()
    deal_possible = scenario["reserve"] <= scenario["budget"]

    systems = {"buyer": system_prompt("buyer", scenario, condition),
               "seller": system_prompt("seller", scenario, condition)}
    # buyer opens with nothing said yet; seed its turn so the API always
    # sees at least one message.
    history = {"buyer": [{"role": "user", "content": "Begin the negotiation with your opening move."}],
               "seller": []}
    last_price = {"buyer": None, "seller": None}  # last price EACH SIDE has proposed
    # per-role streak: how many times IN A ROW that role has rejected without
    # ever countering with its own price -- not a shared counter, since a
    # normal propose-reject-propose-reject exchange must not look like a
    # deadlock (see week-04 dev notes: the first version reset on any
    # propose from either side and so never triggered).
    reject_streak = {"buyer": 0, "seller": 0}
    role, other = "buyer", "seller"

    for _ in range(MAX_TURNS):
        ep.turns += 1
        reader_before = meter.calls
        text = call_conversation(systems[role], history[role], meter)
        history[role].append({"role": "assistant", "content": text})
        history[other].append({"role": "user", "content": text})
        log(f"  [{role}] {text}")

        msg = read_message(condition, text, meter)
        ep.reader_calls += meter.calls - reader_before

        if msg is None:
            ep.format_errors += 1
            log(f"    [protocol] unparseable, message still relayed")
            role, other = other, role
            continue

        perf, price = msg["performative"], msg["price"]
        log(f"    [protocol] performative={perf} price={price}")

        if perf == "propose":
            last_price[role] = price
            reject_streak[role] = 0
        elif perf == "reject-proposal":
            reject_streak[role] += 1
            if use_broker and reject_streak[role] >= DEADLOCK_REJECTS:
                ep.turns += 1
                mediation = broker_mediate(scenario, condition, meter)
                ep.broker_mediations += 1
                mmsg = read_message(condition, mediation, meter)
                log(f"  [broker-mediate] {mediation}")
                if mmsg and mmsg["performative"] == "propose" and mmsg["price"] is not None:
                    last_price["buyer"] = last_price["seller"] = mmsg["price"]
                    history["buyer"].append({"role": "user", "content": mediation})
                    history["seller"].append({"role": "user", "content": mediation})
                    reject_streak = {"buyer": 0, "seller": 0}
        elif perf == "accept-proposal":
            deal_price = last_price[other]
            if deal_price is None:
                ep.format_errors += 1  # accept with nothing on the table yet
            elif use_broker and (deal_price < scenario["reserve"] or deal_price > scenario["budget"]):
                ep.broker_vetoes += 1
                log(f"    [broker-veto] {deal_price} outside "
                    f"[{scenario['reserve']}, {scenario['budget']}], converted to reject")
                consecutive_rejects += 1  # treat like a reject, keep negotiating
            else:
                ep.outcome, ep.price = "deal", deal_price
                break
        elif perf == "refuse":
            ep.outcome = "no_deal"
            break

        role, other = other, role

    if ep.outcome == "deal":
        ep.violation = int(ep.price < scenario["reserve"] or ep.price > scenario["budget"])
        ep.correct = int(deal_possible and not ep.violation)
        if use_broker:
            ep.commission = broker_commission(ep.price)
    else:
        ep.correct = int(not deal_possible)

    return ep
