"""One trade from negotiation to record, and its refund, on mock_payment.py.

    python trade.py trade                       # negotiate, seal, route, settle, record
    python trade.py trade --qty 3               # over auto_limit: asks a person y/n
    python trade.py refund --contract <id>      # opposite entries, state refunded
    python trade.py status

Everything goes to one append-only file, logs/trade-record.jsonl. Each line carries
the hash of the line before it. Balances are never stored as a fact: the ledger is
rebuilt from the "ledger" lines and summed. verify.py replays the file.
"""
import argparse
import hashlib
import json
import os
import sys
import time

import contract
import mock_payment

HERE = os.path.dirname(os.path.abspath(__file__))
RECORD = os.path.join(HERE, "logs", "trade-record.jsonl")
MANDATE = os.path.join(HERE, "mandate.json")
EVENT_SCHEMA = "meshkit.transaction-event/v1"
ACCOUNTS = ("user", "seller", "platform")
GENESIS = "0" * 64

# The trade as a state machine. Any move not listed here is refused.
TRANSITIONS = {
    None: {"created"},
    "created": {"paid", "cancelled"},
    "paid": {"settled", "refunded"},
    "settled": {"refunded"},
    "refunded": set(),
    "cancelled": set(),
}


class IllegalTransition(Exception):
    pass


def line_hash(line):
    return hashlib.sha256(mock_payment.canonical({k: v for k, v in line.items() if k != "hash"})).hexdigest()


class Record:
    """The append-only trade record. Lines are only ever added at the end."""

    def __init__(self, path=RECORD):
        self.path = path

    def read(self):
        if not os.path.exists(self.path):
            return []
        with open(self.path, encoding="utf-8") as f:
            return [json.loads(l) for l in f if l.strip()]

    def append(self, trade_id, kind, data, now=None):
        lines = self.read()
        line = {"seq": len(lines) + 1, "ts": time.time() if now is None else now, "trade": trade_id,
                "type": kind, "data": data, "prev": lines[-1]["hash"] if lines else GENESIS}
        line["hash"] = line_hash(line)
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(line, ensure_ascii=False, sort_keys=True) + "\n")
            f.flush()
            os.fsync(f.fileno())
        return line


def state_of(lines, trade_id):
    states = [l["data"]["to"] for l in lines if l["trade"] == trade_id and l["type"] == "state"]
    return states[-1] if states else None


def move(record, trade_id, to, why):
    current = state_of(record.read(), trade_id)
    if to not in TRANSITIONS[current]:
        raise IllegalTransition(f"{trade_id}: {current} -> {to} is not allowed")
    record.append(trade_id, "state", {"from": current, "to": to, "why": why})
    return to


def ledger_from(lines):
    """The ledger, rebuilt from the record. The record is the only source."""
    ledger = mock_payment.Ledger()
    for l in lines:
        if l["type"] == "ledger":
            ledger.entries.extend(l["data"]["entries"])
    return ledger


def balances(ledger):
    return {a: ledger.balance(a) for a in ACCOUNTS}


def post(record, ledger, trade_id, post_entries, extra=None):
    """Run a posting on the ledger and append what it added as one "ledger" line."""
    start = len(ledger.entries)
    post_entries()
    b = balances(ledger)
    data = {"entries": ledger.entries[start:], "balances": b, "sum": sum(b.values())}
    data.update(extra or {})
    return record.append(trade_id, "ledger", data)["data"]


def new_contract_id():
    return "ctr-" + time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()) + "-" + os.urandom(2).hex()


def run_trade(record, mandate, negotiation, contract_id=None, confirm=None):
    """Negotiation result in, settled (or cancelled) trade out. `confirm` is asked
    only on the "confirm" route and must answer "y" for the payment to go ahead."""
    trade_id = contract_id or new_contract_id()
    lines = record.read()
    if any(l["trade"] == trade_id for l in lines):
        raise ValueError(f"contract id {trade_id} is already in the record")

    record.append(trade_id, "negotiation", negotiation)
    if negotiation["agreement"] is None:
        return {"contract_id": trade_id, "route": None, "state": "no_deal"}

    wrapper = contract.seal(negotiation, trade_id, int(time.time()))
    record.append(trade_id, "contract", wrapper)
    move(record, trade_id, "created", "contract sealed")

    route = mock_payment.route_payment(wrapper, mandate)
    human = None
    if route == "confirm":
        question = (f"{wrapper['body']['offer']['total']} credits to {wrapper['body']['parties']['seller']} "
                    f"is outside the mandate's automatic range. Pay? [y/n] ")
        answer = (confirm(question) if confirm else "").strip().lower()
        human = {"asked": question, "answer": answer or None, "asked_at": time.time()}
    record.append(trade_id, "routing", {"decision": route, "mandate": mandate, "human": human,
                                        "amount": wrapper["body"]["offer"]["total"],
                                        "seller": wrapper["body"]["parties"]["seller"]})
    if route == "reject" or (route == "confirm" and human["answer"] != "y"):
        why = "mandate blocks this seller" if route == "reject" else "the person did not approve"
        return {"contract_id": trade_id, "route": route, "state": move(record, trade_id, "cancelled", why)}

    move(record, trade_id, "paid", f"payment approved by route={route}")
    ledger = ledger_from(record.read())
    receipt = {}
    post(record, ledger, trade_id, lambda: receipt.update(mock_payment.settle_and_receipt(ledger, wrapper)))
    record.append(trade_id, "event", receipt)
    return {"contract_id": trade_id, "route": route, "event": receipt,
            "state": move(record, trade_id, "settled", "settlement event appended")}


def refund(record, trade_id):
    """Refund a settled trade: the opposite entries under the same txn id, a
    "refunded" event, and the state move. Nothing already written is touched."""
    lines = record.read()
    state = state_of(lines, trade_id)
    if "refunded" not in TRANSITIONS.get(state, set()):
        raise IllegalTransition(f"{trade_id}: {state} -> refunded is not allowed")
    mine = [l for l in lines if l["trade"] == trade_id]
    wrapper = next(l["data"] for l in mine if l["type"] == "contract")
    settled = next(l for l in mine if l["type"] == "event" and l["data"]["event"] == "settled")
    amount, fee = settled["data"]["amount"], settled["data"]["fee"]

    first_posting = next(l["seq"] for l in mine if l["type"] == "ledger")
    before = balances(ledger_from([l for l in lines if l["seq"] < first_posting]))
    ledger = ledger_from(lines)
    posted = post(record, ledger, trade_id,
                  lambda: ledger.post(trade_id, {"user": amount, "seller": -(amount - fee), "platform": -fee}),
                  extra={"refund_of_seq": settled["seq"]})
    after = posted["balances"]
    check = {"user_before_trade": before["user"], "user_after_refund": after["user"],
             "seller_before_trade": before["seller"], "seller_after_refund": after["seller"],
             "match": before["user"] == after["user"] and before["seller"] == after["seller"]}

    event = {"schema": EVENT_SCHEMA, "event": "refunded", "contract_id": trade_id,
             "contract_hash": wrapper["hash"], "amount": amount, "fee": fee,
             "refunds_event_tag": settled["data"]["platform_auth_tag"], "refunded_at": time.time()}
    event["platform_auth_tag"] = mock_payment.auth_tag(event)
    record.append(trade_id, "event", event)
    record.append(trade_id, "refund_check", check)
    return {"contract_id": trade_id, "event": event, "check": check,
            "state": move(record, trade_id, "refunded", "refund entries appended")}


def show(record, trade_id):
    """Print one trade's lines in the order they were recorded."""
    for l in record.read():
        if l["trade"] != trade_id:
            continue
        d = l["data"]
        if l["type"] == "negotiation":
            for m in d["messages"]:
                print(f"  [negotiation] turn {m['turn']} {m['from']}: {m['act']} {m.get('unit_price', '')}")
            print(f"  [agreement] {d['agreement']['text'] if d['agreement'] else 'none'}")
        elif l["type"] == "contract":
            print(f"  [contract] {d['body']['schema']} id={d['body']['contract_id']} hash={d['hash']}")
        elif l["type"] == "routing":
            human = f", person answered {d['human']['answer']}" if d["human"] else ""
            print(f"  [routing] {d['decision']} (amount {d['amount']}, auto_limit {d['mandate']['auto_limit']}{human})")
        elif l["type"] == "ledger":
            for e in d["entries"]:
                print(f"  [ledger] txn={e['txn']} {e['acct']:<8} {e['delta']:+d}")
            print(f"  [balances] {d['balances']} sum={d['sum']}")
        elif l["type"] == "event":
            print(f"  [event] {d['schema']} {d['event']} contract_id={d['contract_id']} "
                  f"contract_hash={d['contract_hash'][:16]}... tag={d['platform_auth_tag'][:16]}...")
        elif l["type"] == "refund_check":
            print(f"  [check] user {d['user_before_trade']} -> {d['user_after_refund']}, "
                  f"seller {d['seller_before_trade']} -> {d['seller_after_refund']}, match={d['match']}")
        elif l["type"] == "state":
            print(f"  [state] {d['from']} -> {d['to']} ({d['why']})")


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--record", default=RECORD)
    p.add_argument("--mandate", default=MANDATE)
    sub = p.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser("trade")
    t.add_argument("--item", default="우유 190mL x 24")
    t.add_argument("--qty", type=int, default=1)
    t.add_argument("--list-price", type=int, default=12000)
    t.add_argument("--floor", type=int, default=9000)
    t.add_argument("--budget", type=int, default=10000)
    r = sub.add_parser("refund")
    r.add_argument("--contract", required=True)
    sub.add_parser("status")
    a = p.parse_args(argv)
    record = Record(a.record)

    if a.cmd == "trade":
        with open(a.mandate, encoding="utf-8") as f:
            mandate = json.load(f)
        started = time.time()
        n = contract.negotiate(a.item, a.qty, a.list_price, a.floor, a.budget)
        out = run_trade(record, mandate, n, confirm=input)
        show(record, out["contract_id"])
        print(f"trade {out['contract_id']}: route={out['route']} state={out['state']} "
              f"({time.time() - started:.3f} s)")
    elif a.cmd == "refund":
        started = time.time()
        out = refund(record, a.contract)
        show(record, a.contract)
        print(f"refund {a.contract}: state={out['state']} ({time.time() - started:.3f} s)")
    else:
        lines = record.read()
        for trade_id in dict.fromkeys(l["trade"] for l in lines):
            print(f"{trade_id}: {state_of(lines, trade_id) or 'no_deal'}")
        b = balances(ledger_from(lines))
        print(f"balances {b} sum={sum(b.values())}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
