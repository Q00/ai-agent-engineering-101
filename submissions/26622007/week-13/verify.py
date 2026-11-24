"""Replay logs/trade-record.jsonl and check every trade in it.

    python verify.py [record.jsonl]

For someone who was not in the trade. It reads the record file only and does not
import trade.py: the chain, the state rules and the expected amounts are worked out
again here. From mock_payment.py it uses the hash, the HMAC tag, the routing gate
and the fee rate; from contract.py the wrapper check. Exit code 0 means no problem.
"""
import hashlib
import json
import os
import sys

import contract
import mock_payment

EVENT_SCHEMA = "meshkit.transaction-event/v1"
ACCOUNTS = ("user", "seller", "platform")
ALLOWED = {None: {"created"}, "created": {"paid", "cancelled"}, "paid": {"settled", "refunded"},
           "settled": {"refunded"}, "refunded": set(), "cancelled": set()}
# What may be recorded for a trade while it is in each state.
MAY_APPEAR = {None: {"negotiation", "contract"}, "created": {"routing"}, "paid": {"ledger", "event"},
              "settled": {"ledger", "event", "refund_check"}, "refunded": set(), "cancelled": set()}


def tag_ok(event):
    body = {k: v for k, v in event.items() if k != "platform_auth_tag"}
    return mock_payment.auth_tag(body) == event.get("platform_auth_tag")


def moves_of(ledger_line, trade_id):
    """{account: delta} of one posting, or None if it is not three entries of this txn."""
    entries = ledger_line["data"]["entries"]
    if len(entries) != 3 or {e["acct"] for e in entries} != set(ACCOUNTS) or any(e["txn"] != trade_id for e in entries):
        return None
    return {e["acct"]: e["delta"] for e in entries}


def check_chain(lines, problems):
    prev = "0" * 64
    for i, l in enumerate(lines):
        body = {k: v for k, v in l.items() if k != "hash"}
        if l.get("seq") != i + 1:
            problems.append(f"line {i + 1}: seq is {l.get('seq')}; a line was removed or reordered")
        if l.get("prev") != prev:
            problems.append(f"line {i + 1}: prev hash does not point at the line before")
        if hashlib.sha256(mock_payment.canonical(body)).hexdigest() != l.get("hash"):
            problems.append(f"line {i + 1}: line hash does not match its content")
        prev = l.get("hash")


class TradeCheck:
    """One trade, fed its lines in record order. `running` is the balance of every
    account over the whole record so far; other trades' postings move it too."""

    FULL = {"settled": ["negotiation", "contract", "routing", "ledger", "event"],
            "refunded": ["negotiation", "contract", "routing", "ledger", "event", "ledger", "event", "refund_check"],
            "cancelled": ["negotiation", "contract", "routing"],
            None: ["negotiation"]}

    def __init__(self, trade_id, problems):
        self.id, self.problems = trade_id, problems
        self.state, self.kinds = None, []
        self.negotiation = self.wrapper = self.routing = self.settled = self.before = None

    def bad(self, msg):
        self.problems.append(f"{self.id}: {msg}")

    def pay(self):
        amount = self.wrapper["body"]["offer"]["total"]
        fee = round(amount * mock_payment.FEE_RATE)
        return amount, fee, {"user": -amount, "seller": amount - fee, "platform": fee}

    def feed(self, line, running):
        kind, d = line["type"], line["data"]
        if kind == "ledger":   # count the money first, so a refused line still moves the balances
            for e in d["entries"]:
                running[e["acct"]] = running.get(e["acct"], 0) + e["delta"]
        if kind == "state":
            return self.move(d)
        self.kinds.append(kind)
        if kind not in MAY_APPEAR.get(self.state, set()):
            return self.bad(f"a {kind} line while the trade is {self.state}")
        getattr(self, "on_" + kind)(line, running)

    def move(self, d):
        if d["from"] != self.state or d["to"] not in ALLOWED.get(self.state, set()):
            self.bad(f"state move {d['from']} -> {d['to']} is not allowed from {self.state}")
        if d["to"] == "paid":
            r = self.routing
            if r is None or r["decision"] == "reject":
                self.bad("paid without a routing decision that allows it")
            elif r["decision"] == "confirm" and (r["human"] or {}).get("answer") != "y":
                self.bad("paid on the confirm route without a person's approval")
        if d["to"] == "settled" and self.settled is None:
            self.bad("settled without a settlement event")
        self.state = d["to"]

    def on_negotiation(self, line, running):
        self.negotiation = line["data"]

    def on_contract(self, line, running):
        self.wrapper = w = line["data"]
        for p in contract.check_wrapper(w):
            self.bad(p)
        body, offer, a = w["body"], w["body"]["offer"], self.negotiation["agreement"] or {}
        if body["contract_id"] != self.id:
            self.bad("contract id differs from the trade id")
        if body["negotiation_hash"] != mock_payment.digest({"messages": self.negotiation["messages"]}):
            self.bad("contract does not point at the recorded negotiation")
        if any(offer[k] != a.get(k) for k in ("item", "qty", "unit_price", "total")):
            self.bad("contract offer differs from the negotiated agreement")
        if offer["total"] != offer["unit_price"] * offer["qty"]:
            self.bad("offer total is not unit price times quantity")
        last = self.negotiation["messages"][-1]
        if (last.get("act"), last.get("unit_price")) != ("accept", offer["unit_price"]):
            self.bad("the negotiation does not end with an accept at the contract price")

    def on_routing(self, line, running):
        self.routing = d = line["data"]
        if d["decision"] != mock_payment.route_payment(self.wrapper, d["mandate"]):
            self.bad(f"routing decision {d['decision']} is not what the mandate gives")

    def on_ledger(self, line, running):
        d = line["data"]
        _, _, pay = self.pay()
        want = pay if self.state == "paid" else {a: -v for a, v in pay.items()}
        moves = moves_of(line, self.id)
        if moves != want:
            self.bad(f"ledger entries {moves} are not the expected {want}")
        if self.state == "paid":
            self.before = {a: running.get(a, 0) - (moves or {}).get(a, 0) for a in ACCOUNTS}
        if sum(e["delta"] for e in d["entries"]) != 0 or d["sum"] != 0:
            self.bad("ledger entries do not sum to 0")
        if d["balances"] != {a: running.get(a, 0) for a in ACCOUNTS}:
            self.bad("recorded balances differ from the sum of the entries")

    def on_event(self, line, running):
        d = line["data"]
        want = "settled" if self.state == "paid" else "refunded"
        amount, fee, _ = self.pay()
        if d.get("schema") != EVENT_SCHEMA or d.get("event") != want:
            self.bad(f"expected a {want} {EVENT_SCHEMA} event")
        if not tag_ok(d):
            self.bad(f"{d.get('event')} event fails the platform tag")
        if (d.get("contract_id"), d.get("contract_hash")) != (self.id, self.wrapper["hash"]):
            self.bad("event does not point at the contract id and hash")
        if (d.get("amount"), d.get("fee")) != (amount, fee):
            self.bad("event amount or fee differs from the contract")
        if want == "settled":
            self.settled = d
        elif d.get("refunds_event_tag") != self.settled.get("platform_auth_tag"):
            self.bad("refund event does not point at the settlement event")

    def on_refund_check(self, line, running):
        d, b = line["data"], self.before
        want = {"user_before_trade": b["user"], "user_after_refund": running.get("user", 0),
                "seller_before_trade": b["seller"], "seller_after_refund": running.get("seller", 0)}
        want["match"] = (want["user_before_trade"] == want["user_after_refund"]
                         and want["seller_before_trade"] == want["seller_after_refund"])
        if d != want or not d["match"]:
            self.bad(f"refund check {d} differs from the replayed {want}")

    def finish(self):
        if self.state not in self.FULL:
            self.bad(f"the trade stops at {self.state}")
        elif self.kinds != self.FULL[self.state]:
            self.bad(f"a {self.state} trade should hold {self.FULL[self.state]}, the record holds {self.kinds}")
        return self.state or "no_deal"


def verify(path):
    """{"problems": [...], "trades": {id: state}, "balances": {...}, "lines": n}."""
    problems = []
    with open(path, encoding="utf-8") as f:
        lines = [json.loads(l) for l in f if l.strip()]
    check_chain(lines, problems)

    running, checks, broken = {}, {}, set()
    for l in lines:
        t = l.get("trade")
        check = checks.setdefault(t, TradeCheck(t, problems))
        if t in broken:
            continue
        try:
            check.feed(l, running)
        except (KeyError, TypeError, IndexError, AttributeError) as e:
            problems.append(f"{t}: line {l.get('seq')} is malformed ({type(e).__name__}: {e})")
            broken.add(t)
    trades = {t: "unreadable" if t in broken else c.finish() for t, c in checks.items()}
    if sum(running.values()) != 0:
        problems.append(f"all entries sum to {sum(running.values())}, not 0")
    return {"problems": problems, "trades": trades,
            "balances": {a: running.get(a, 0) for a in ACCOUNTS}, "lines": len(lines)}


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    path = argv[0] if argv else os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs", "trade-record.jsonl")
    report = verify(path)
    print(f"record {path}: {report['lines']} lines")
    for t, s in report["trades"].items():
        print(f"  {t}: {s}")
    b = report["balances"]
    print(f"balances {b} sum={sum(b.values())}")
    for p in report["problems"]:
        print(f"PROBLEM {p}")
    print("VERIFIED: every trade replays by the rules" if not report["problems"]
          else f"FAILED: {len(report['problems'])} problem(s)")
    return 0 if not report["problems"] else 1


if __name__ == "__main__":
    sys.exit(main())
