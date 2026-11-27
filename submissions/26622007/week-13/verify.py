"""Replay logs/trade-record.jsonl and check every trade in it.

    python verify.py [record.jsonl] [--mandate mandate.json] [--head <hash of the last line>]

For someone who was not in the trade. It reads the record file only and does not
import trade.py: the chain, the state rules and the expected amounts are worked out
again here. From mock_payment.py it uses the hash, the HMAC tag, the routing gate
and the fee rate; from contract.py the wrapper check. Routing is checked against the
published mandate.json, not against the copy inside the record. A chain cut off at
the end still links up, so give --head (printed in the README) to rule that out.
Exit code 0 means no problem.
"""
import argparse
import hashlib
import json
import os
import sys

import contract
import mock_payment

EVENT_SCHEMA = "meshkit.transaction-event/v1"
ACCOUNTS = ("user", "seller", "platform")
ALLOWED = {None: {"created"}, "created": {"paid", "cancelled"}, "paid": {"settled"},
           "settled": {"refunded"}, "refunded": set(), "cancelled": set()}
# What may be recorded for a trade while it is in each state. Each kind appears at
# most once per state, in the order of TradeCheck.FULL: one payment posting and one
# settled event while paid; one refund posting, one refunded event and one refund
# check while settled.
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

    def __init__(self, trade_id, problems, mandate=None):
        self.id, self.problems, self.mandate = trade_id, problems, mandate
        self.state, self.kinds = None, []
        self.negotiation = self.wrapper = self.routing = self.settled = self.before = None
        self.refund_posted = self.refunded = self.refund_checked = False
        # What other trades moved after this trade's payment posting and before its
        # refund posting: the same lines trade.refund sets aside.
        self.others = dict.fromkeys(ACCOUNTS, 0)

    def see_other(self, entries):
        if self.before is not None and not self.refund_posted:
            for e in entries:
                self.others[e["acct"]] = self.others.get(e["acct"], 0) + e["delta"]

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
        if d["to"] == "settled" and (self.before is None or self.settled is None):
            self.bad("settled without a payment posting and a settlement event")
        if d["to"] == "refunded" and not (self.refund_posted and self.refunded and self.refund_checked):
            self.bad("refunded without its own refund posting, refunded event and refund check")
        if d["to"] == "cancelled":
            r = self.routing or {}
            if r.get("decision") == "auto" or (r.get("decision") == "confirm" and (r["human"] or {}).get("answer") == "y"):
                self.bad("cancelled although the routing decision approved the payment")
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
        if any(offer[k] != a.get(k) for k in ("item", "qty", "unit_price", "total", "currency")):
            self.bad("contract offer differs from the negotiated agreement")
        text = (f"{body['parties']['buyer']} buys {offer['qty']} x {offer['item']} from {body['parties']['seller']} "
                f"at {offer['unit_price']} credits each, {offer['total']} credits in total.")
        if (a.get("buyer"), a.get("seller"), a.get("text")) != (body["parties"]["buyer"], body["parties"]["seller"], text):
            self.bad("the agreement text or parties differ from the contract")
        if offer["total"] != offer["unit_price"] * offer["qty"]:
            self.bad("offer total is not unit price times quantity")
        if type(offer["qty"]) is not int or type(offer["unit_price"]) is not int or offer["qty"] < 1 or offer["unit_price"] < 1:
            self.bad("quantity and unit price must be whole numbers of 1 or more")
        last = self.negotiation["messages"][-1]
        if (last.get("act"), last.get("unit_price")) != ("accept", offer["unit_price"]):
            self.bad("the negotiation does not end with an accept at the contract price")

    def on_routing(self, line, running):
        self.routing = d = line["data"]
        mandate = d["mandate"] if self.mandate is None else self.mandate
        if d["mandate"] != mandate:
            self.bad("routing used a mandate that is not the published mandate")
        if d["decision"] != mock_payment.route_payment(self.wrapper, mandate):
            self.bad(f"routing decision {d['decision']} is not what the mandate gives")
        offer, parties = self.wrapper["body"]["offer"], self.wrapper["body"]["parties"]
        if (d.get("amount"), d.get("seller")) != (offer["total"], parties["seller"]):
            self.bad("routing amount or seller differs from the contract")
        if d["decision"] != "confirm" and d.get("human") is not None:
            self.bad("a person's answer on a route that does not ask")

    def on_ledger(self, line, running):
        d = line["data"]
        if (self.before if self.state == "paid" else self.refund_posted):
            self.bad(f"a second {'payment' if self.state == 'paid' else 'refund'} posting")
        _, _, pay = self.pay()
        want = pay if self.state == "paid" else {a: -v for a, v in pay.items()}
        moves = moves_of(line, self.id)
        if any(type(e["delta"]) is not int for e in d["entries"]):
            self.bad("ledger amounts must be whole numbers")
        if moves != want:
            self.bad(f"ledger entries {moves} are not the expected {want}")
        if self.state == "paid" and self.before is None:
            self.before = {a: running.get(a, 0) - (moves or {}).get(a, 0) for a in ACCOUNTS}
        elif self.state == "settled":
            self.refund_posted = True
        if sum(e["delta"] for e in d["entries"]) != 0 or d["sum"] != 0:
            self.bad("ledger entries do not sum to 0")
        if d["balances"] != {a: running.get(a, 0) for a in ACCOUNTS}:
            self.bad("recorded balances differ from the sum of the entries")

    def on_event(self, line, running):
        d = line["data"]
        want = "settled" if self.state == "paid" else "refunded"
        if (self.settled is not None) if want == "settled" else self.refunded:
            self.bad(f"a second {want} event")
        if (self.before is None) if want == "settled" else not self.refund_posted:
            self.bad(f"a {want} event before its ledger posting")
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
            if self.settled is None:
                self.settled = d
            return
        self.refunded = True
        if d.get("refunds_event_tag") != self.settled.get("platform_auth_tag"):
            self.bad("refund event does not point at the settlement event")

    def on_refund_check(self, line, running):
        d, want = line["data"], {}
        if self.refund_checked or not self.refunded:
            self.bad("a refund check that does not follow exactly one refund posting and refunded event")
        self.refund_checked = True
        for acct in ("user", "seller"):
            want[f"{acct}_before_trade"] = self.before[acct]
            want[f"{acct}_other_trades_since"] = self.others[acct]
            want[f"{acct}_after_refund"] = running.get(acct, 0)
        want["match"] = all(self.before[a] + self.others[a] == running.get(a, 0) for a in ("user", "seller"))
        # The first refund on record was written before "other_trades_since" existed; absent means 0.
        got = dict({"user_other_trades_since": 0, "seller_other_trades_since": 0}, **d)
        if got != want or not want["match"]:
            self.bad(f"refund check {d} differs from the replayed {want}")

    def finish(self):
        if self.state is None and (self.negotiation or {}).get("agreement") is not None:
            self.bad("an agreement was reached but no contract follows")
        if self.state not in self.FULL:
            self.bad(f"the trade stops at {self.state}")
        elif self.kinds != self.FULL[self.state]:
            self.bad(f"a {self.state} trade should hold {self.FULL[self.state]}, the record holds {self.kinds}")
        return self.state or "no_deal"


def read_lines(path, problems):
    lines = []
    with open(path, encoding="utf-8") as f:
        for n, raw in enumerate(f, 1):
            if not raw.strip():
                continue
            try:
                line = json.loads(raw)
            except ValueError:
                problems.append(f"line {n}: not JSON")
                continue
            if not isinstance(line, dict) or not isinstance(line.get("trade"), str) or not isinstance(line.get("data"), dict):
                problems.append(f"line {n}: not a record line")
                continue
            lines.append(line)
    return lines


def verify(path, mandate=None, head=None):
    """{"problems": [...], "trades": {id: state}, "balances": {...}, "lines": n, "head": hash}.
    `mandate` is the published mandate every routing line must have used; `head` is
    the published hash of the last line."""
    problems = []
    lines = read_lines(path, problems)
    if not lines:
        problems.append("the record holds no lines")
    check_chain(lines, problems)
    last = lines[-1].get("hash") if lines else None
    if head is not None and head != last:
        problems.append(f"record head is {last}, not the published head {head}; lines were cut off or added")

    running, checks, broken = {}, {}, set()
    for l in lines:
        t = l["trade"]
        if t not in checks:
            checks[t] = TradeCheck(t, problems, mandate)
        if t in broken:
            continue
        try:
            checks[t].feed(l, running)
            if l["type"] == "ledger":
                for other in checks.values():
                    if other is not checks[t]:
                        other.see_other(l["data"]["entries"])
        except (KeyError, TypeError, IndexError, AttributeError, ValueError) as e:
            problems.append(f"{t}: line {l.get('seq')} is malformed ({type(e).__name__}: {e})")
            broken.add(t)
    trades = {t: "unreadable" if t in broken else c.finish() for t, c in checks.items()}
    if sum(running.values()) != 0:
        problems.append(f"all entries sum to {sum(running.values())}, not 0")
    return {"problems": problems, "trades": trades, "lines": len(lines), "head": last,
            "balances": {a: running.get(a, 0) for a in ACCOUNTS}}


def main(argv=None):
    here = os.path.dirname(os.path.abspath(__file__))
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("record", nargs="?", default=os.path.join(here, "logs", "trade-record.jsonl"))
    p.add_argument("--mandate", default=os.path.join(here, "mandate.json"))
    p.add_argument("--head", help="the published hash of the record's last line")
    a = p.parse_args(argv)
    try:
        with open(a.mandate, encoding="utf-8") as f:
            mandate = json.load(f)
        report = verify(a.record, mandate, a.head)
    except (OSError, ValueError) as e:
        print(f"FAILED: cannot read the record or the mandate ({e})")
        return 1
    print(f"record {os.path.relpath(a.record)}: {report['lines']} lines, head {report['head']}")
    for t, s in report["trades"].items():
        print(f"  {t}: {s}")
    b = report["balances"]
    print(f"balances {b} sum={sum(b.values())}")
    for problem in report["problems"]:
        print(f"PROBLEM {problem}")
    if not a.head:
        print("note: no --head given, so lines cut off the end would not be noticed")
    print("VERIFIED: every trade replays by the rules" if not report["problems"]
          else f"FAILED: {len(report['problems'])} problem(s)")
    return 0 if not report["problems"] else 1


if __name__ == "__main__":
    sys.exit(main())
