"""Cross-check one trade test run, message by message, from both sides.

    py verify_trades.py            # reads logs/ written by the last trade test

For every negotiation it pairs what the kit saw (logs/trade-test.log) with what
our agent did (logs/seller-under-test.log, logs/buyer-under-test-*.log) and checks:

  1. flow       every kit message reached our agent and got exactly the reply our agent decided
  2. harness    every decision came out of a Claude call through the ReAct loop, not from a guard
  3. limits     no ask or sale below the reserve; no offer or purchase above the budget
  4. strategy   the moves match the strategy in the prompts (reported, not a failure)
  5. protocol   taskId continuity, A2A-Version on every request, an error for a finished task

Exit code 1 if any flow, harness or limit check fails.
"""
import ast
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
LOGS = os.path.join(HERE, "logs")
INVENTORY = json.load(open(os.path.join(HERE, "inventory.json"), encoding="utf-8"))
OPENING_SHARE = 0.6

problems, notes = [], []


def last_number(text):
    nums = re.findall(r"\d+(?:\.\d+)?", text or "")
    return float(nums[-1]) if nums else None


def read(name):
    with open(os.path.join(LOGS, name), encoding="utf-8", errors="replace") as f:
        return f.read().splitlines()


def check(ok, where, what, bucket=problems):
    if not ok:
        bucket.append(f"{where}: {what}")
    return ok


# ---------------------------------------------------------------- our agent's side

def decisions_in(lines, start_marker=None):
    """Split an agent log into decision blocks: model calls, ReAct result, guard, decision."""
    blocks, cur = [], None
    for line in lines:
        m = re.match(r"=== task (\w+) (\w+): buyer said (.*)$", line)
        if m:
            cur = {"task": m.group(1), "said": ast.literal_eval(m.group(3)), "calls": 0, "react": False,
                   "guard": None, "decision": None}
            blocks.append(cur)
            continue
        if start_marker and line.startswith("<- "):  # buyer log: a seller reply opens a decision block
            cur = {"reply": line, "calls": 0, "react": False, "guard": None, "decision": None}
            blocks.append(cur)
            continue
        if cur is None:
            continue
        if re.match(r"\[model [\d.]+s exit=0\]", line):
            cur["calls"] += 1
        elif line.startswith("[react] final"):
            cur["react"] = True
        elif line.startswith("GUARD:"):
            cur["guard"] = line
        elif line.startswith("decision: "):
            cur["decision"] = ast.literal_eval(line[len("decision: "):])
    return blocks


# ---------------------------------------------------------------- the kit's side

def kit_sections(lines):
    sections, cur = [], None
    for line in lines:
        if line.startswith("### "):
            cur = {"title": line[4:], "lines": []}
            sections.append(cur)
        elif cur is not None:
            cur["lines"].append(line.strip())
    return sections


def kit_exchanges(section):
    """[(text sent, taskId or None, state, reply text, receipt price)] from one kit-buyer section."""
    out = []
    for line in section["lines"]:
        m = re.match(r'-> SendMessage "(.*)"(?: taskId=(\w+))?$', line)
        if m:
            out.append({"sent": m.group(1), "task": m.group(2), "state": None, "reply": "", "receipt": None})
            continue
        m = re.match(r"<- (TASK_STATE_\w+) \| (.*)$", line)
        if m and out:
            out[-1]["state"], out[-1]["reply"] = m.group(1), m.group(2)
            continue
        m = re.match(r"<- error (-?\d+)", line)
        if m and out:
            out[-1]["state"] = f"error {m.group(1)}"
            continue
        m = re.match(r"artifact receipt (\[.*\])$", line)
        if m and out:
            out[-1]["receipt"] = json.loads(m.group(1))[0]["price"]
    task = next((e["task"] for e in out if e["task"]), None)
    for e in out:
        e["task"] = e["task"] or task
    return out


# ---------------------------------------------------------------- seller tests

def verify_seller(sections, blocks):
    print("\n== seller (kit buyer -> our seller -> Claude)")
    by_task = {}
    for b in blocks:
        by_task.setdefault(b["task"], []).append(b)
    for s in sections:
        title = s["title"]
        if title.startswith("seller_terminal"):
            ex = kit_exchanges(s)
            ok = check(ex and ex[0]["state"] == "error -32004", title, f"expected error -32004, got {ex}")
            print(f"  {'ok ' if ok else 'BAD'} {title}: {ex[0]['state'] if ex else 'nothing'}")
            continue
        if not title.startswith(("seller_deal", "seller_holds_reserve")):
            continue
        item = re.search(r": (\w+), kit budget", title).group(1)
        reserve, list_price = INVENTORY[item]["reserve"], INVENTORY[item]["list_price"]
        middle = round((list_price + reserve) / 2)
        ex = kit_exchanges(s)
        ours = by_task.get(ex[0]["task"], []) if ex else []
        where = title.split(":")[0]
        check(len(ex) == len(ours), where, f"kit sent {len(ex)} messages, our seller decided {len(ours)} times")
        asks, offers, deviations = [], [], []
        for i, (e, b) in enumerate(zip(ex, ours)):
            d, offer = b["decision"] or {}, last_number(e["sent"]) if i else None
            check(e["sent"] == b["said"], where, f"message {i}: kit sent {e['sent']!r}, seller got {b['said']!r}")
            check(b["calls"] >= 1 and b["react"] and not b["guard"], where,
                  f"message {i}: not a clean Claude decision (calls {b['calls']}, react {b['react']}, guard {b['guard']})")
            if e["state"] == "TASK_STATE_INPUT_REQUIRED":
                ask = last_number(e["reply"])
                asks.append(ask)
                check(d.get("action") == "counter" and ask == d.get("price"), where,
                      f"message {i}: kit read ask {ask}, seller decided {d}")
                check(ask >= reserve, where, f"LIMIT: asked {ask} below the reserve {reserve}")
            elif e["state"] == "TASK_STATE_COMPLETED":
                check(d.get("action") == "accept" and e["receipt"] == offer, where,
                      f"message {i}: completed with receipt {e['receipt']} after offer {offer}, decision {d}")
                check(e["receipt"] is not None and e["receipt"] >= reserve, where,
                      f"LIMIT: sold at {e['receipt']} below the reserve {reserve}")
            if offer is not None:
                offers.append(offer)
                want = "accept" if offer >= reserve else "counter"
                if d.get("action") != want:
                    deviations.append(f"offer {offer:g}: {d.get('action')} (strategy: {want})")
        if asks and asks[0] != list_price:
            deviations.append(f"first ask {asks[0]:g} (strategy: list price {list_price})")
        later = asks[1:]
        if later.count(middle) > 1 or any(a not in (middle, reserve) for a in later):
            deviations.append(f"counters {later} (strategy: middle {middle} once, then reserve {reserve})")
        for dv in deviations:
            notes.append(f"{where}: {dv}")
        end = ex[-1]["state"] if ex else "none"
        receipt = ex[-1]["receipt"] if ex else None
        print(f"  {where:<24} {item:<9} asks {[int(a) for a in asks]} offers {[int(o) for o in offers]} "
              f"-> {end.replace('TASK_STATE_', '')}{f' at {receipt:g}' if receipt else ''} | "
              f"Claude calls {sum(b['calls'] for b in ours)} | strategy {'kept' if not deviations else 'deviated'}")


# ---------------------------------------------------------------- buyer tests

def verify_buyer(sections):
    print("\n== buyer (our buyer -> Claude, against the kit seller)")
    for s in sections:
        title = s["title"]
        m = re.match(r"(buyer_\w+) #(\d+): .*--item (\w+) --budget (\d+)", title)
        if not m:
            continue
        name, run, item, budget = m.group(1), m.group(2), m.group(3), float(m.group(4))
        where = f"{name} #{run}"
        lines = read(f"buyer-under-test-{name}-{run}.log")
        sent = [(mm.group(1), mm.group(2)) for mm in (re.match(r"-> '(.*)'(?: taskId=(\w+))?$", l) for l in lines) if mm]
        offers = [float(t) for t, _ in sent[1:]]
        blocks = decisions_in(lines, start_marker=True)
        decided = [b for b in blocks if b["decision"]]
        saw = next((l for l in s["lines"] if l.startswith("kit seller saw:")), "")
        kit_offers = [float(x) for x in re.findall(r"\d+", re.search(r"offers \[(.*?)\]", saw).group(1))]
        deals = [float(x) for x in re.findall(r"\d+(?:\.\d+)?", re.search(r"deals \[(.*?)\]", saw).group(1))]
        bad_version = int(re.search(r"without A2A-Version 1.0: (\d+)", saw).group(1))
        continued = int(re.search(r"continued (\d+)", saw).group(1))

        check(offers == kit_offers, where, f"buyer sent {offers}, kit seller received {kit_offers}")
        for b in decided:
            check(b["calls"] >= 1 and b["react"] and not b["guard"], where,
                  f"not a clean Claude decision: calls {b['calls']}, react {b['react']}, guard {b['guard']}")
        sent_by_decision = [float(b["decision"]["price"]) for b in decided if b["decision"].get("action") == "offer"]
        check(sent_by_decision == offers, where, f"decisions {sent_by_decision} vs offers sent {offers}")
        check(all(o <= budget for o in offers), where, f"LIMIT: offered over the budget {budget:g}: {offers}")
        check(all(p <= budget for p in deals), where, f"LIMIT: bought over the budget {budget:g}: {deals}")
        if name == "buyer_deal":
            check(len(deals) == 1, where, f"expected exactly one deal, got {deals}")
        else:
            check(not deals, where, f"expected no deal, got {deals}")
        check(bad_version == 0, where, f"{bad_version} request(s) without A2A-Version 1.0")
        check(continued == len(offers) and all(t for _, t in sent[1:]), where,
              f"follow-ups without taskId (kit counted {continued} continued of {len(offers)})")

        asks = [last_number(b["reply"]) for b in decided]
        want, deviations = [], []
        for i, ask in enumerate(asks):
            if i == 0:
                want.append(("offer", round(budget * OPENING_SHARE)))
            elif ask is not None and ask <= budget:
                want.append(("offer", ask))
            elif budget not in offers[:i]:
                want.append(("offer", budget))
            else:
                want.append(("walk_away", None))
        for b, w in zip(decided, want):
            got = (b["decision"]["action"], b["decision"].get("price"))
            if got[0] != w[0] or (w[1] is not None and float(got[1]) != float(w[1])):
                deviations.append(f"got {got}, strategy {w}")
        for dv in deviations:
            notes.append(f"{where}: {dv}")
        walked = any("walked away (CancelTask)" in l for l in lines)
        print(f"  {where:<22} {item:<9} budget {budget:g} offers {[int(o) for o in offers]} "
              f"-> {'deal ' + str(deals) if deals else ('walked away' if walked else 'no deal')} | "
              f"Claude calls {sum(b['calls'] for b in blocks)} | strategy {'kept' if not deviations else 'deviated'}")


def main():
    kit = read("trade-test.log")
    sections = kit_sections(kit)
    verify_seller(sections, decisions_in(read("seller-under-test.log")))
    verify_buyer(sections)
    guards = sum(1 for f in os.listdir(LOGS) if f.endswith(".log")
                 for l in read(f) if l.startswith("GUARD:"))
    print(f"\nGUARD overrides in this run: {guards}")
    print(f"strategy deviations: {len(notes)}" + "".join(f"\n  - {n}" for n in notes))
    print(f"problems (flow / harness / limits / protocol): {len(problems)}" + "".join(f"\n  - {p}" for p in problems))
    sys.exit(1 if problems else 0)


if __name__ == "__main__":
    main()
