"""Read-only: what the seller model decided, from its logged prompts and answers, beside actual signed deals.

Prompts hold the quote, the floor and the transcript. This shows how the model opened, conceded and accepted, so a coded
rule can be chosen from evidence. It cannot show what a buyer would have done with a different answer.
"""

from collections import Counter, defaultdict
import json
import os
from pathlib import Path
import re
from statistics import median

ROOT = Path(__file__).resolve().parent
PROMPT_TERMS = re.compile(r"The list price is (\d+)\. Your lowest acceptable price is (\d+)\.")
ASK_LINE = re.compile(r"(?:asking|Counteroffer:) (\d+)\.")
STEPS = (0, .25, .5, .75, 1, 1.01)


def parse_call(prompt, answer):
    terms = PROMPT_TERMS.search(prompt)
    if not terms or "Conversation so far:" not in prompt:
        return None
    convo = prompt[prompt.index("Conversation so far:"):]
    convo = convo[:convo.index("Decide your next move")] if "Decide your next move" in convo else convo
    offers, asks = [], []
    first = next((line[6:].strip() for line in convo.splitlines() if line.startswith("buyer:")), "")
    for line in convo.splitlines():
        if line.startswith("buyer:"):
            text = line[6:].strip().rstrip(".!")
            if re.fullmatch(r"[1-9]\d*", text):
                offers.append(int(text))
            else:
                match = re.fullmatch(r"(?:I offer|I can pay|I accept|I agree to pay)\s+([1-9]\d*).*", text, re.I)
                if match:
                    offers.append(int(match[1]))
        else:  # The notice comes first, so the ask can be on the line after "you:".
            found = ASK_LINE.search(line)
            if found:
                asks.append(int(found[1]))
    return {"quote": int(terms[1]), "floor": int(terms[2]), "offers": offers, "asks": asks, "first": first,
            "action": (answer or {}).get("action"), "price": (answer or {}).get("price")}


def load_calls(root, since="20261103"):
    folder = Path(root) / "logs/harness"
    calls = []
    for name in sorted(os.listdir(folder)):
        if name < since:
            continue
        path = folder / name
        try:
            prompt = (path / "request.txt").read_text(encoding="utf-8")
        except OSError:
            continue
        if "inventory sales agent" not in prompt[:120]:
            continue
        try:
            answer = json.loads((path / "result.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            answer = None
        call = parse_call(prompt, answer)
        if call:
            call["name"] = name
            calls.append(call)
    return calls


def bucket(value, edges):
    for edge in edges:
        if value < edge:
            return f"<{edge}"
    return f">={edges[-1]}"


def spread(values):
    if not values:
        return None
    ordered = sorted(values)
    return {"n": len(ordered), "p10": round(ordered[int(len(ordered) * .1)], 4), "median": round(median(ordered), 4),
            "p90": round(ordered[int(len(ordered) * .9)], 4)}


def summarize(calls):
    opening = [c for c in calls if not c["offers"]]
    follow = [c for c in calls if c["offers"] and c["asks"]]
    report = {"calls": len(calls), "opening_calls": len(opening), "followup_calls": len(follow),
              "other_calls": len(calls) - len(opening) - len(follow),
              "opening_actions": dict(Counter(c["action"] for c in opening)),
              "opening_price_per_quote": spread([c["price"] / c["quote"] for c in opening if c["action"] == "ask" and c["price"]]),
              "opening_equal_to_quote": sum(c["action"] == "ask" and c["price"] == c["quote"] for c in opening)}
    by_offer = defaultdict(Counter)
    by_gap = defaultdict(Counter)
    concession, above_floor, by_turn = [], [], defaultdict(list)
    for c in follow:
        offer, ask, floor = c["offers"][-1], c["asks"][-1], c["floor"]
        band = bucket(offer / floor, (.8, .9, .95, 1, 1.01, 1.03, 1.05, 1.1, 1.2))
        by_offer[band][c["action"]] += 1
        room = ask - floor
        if room > 0 and offer >= floor:
            by_gap[bucket((ask - offer) / room, (.1, .25, .5, .75, 1.0001))][c["action"]] += 1
        if c["action"] == "ask" and c["price"] and ask > max(offer, floor):
            share = (ask - c["price"]) / (ask - max(offer, floor))
            concession.append(share)
            by_turn[len(c["asks"])].append(share)
            above_floor.append((c["price"] - floor) / floor)
    report["followup_actions_by_offer_per_floor"] = {k: dict(v) for k, v in sorted(by_offer.items())}
    report["followup_actions_by_share_of_room_still_open"] = {k: dict(v) for k, v in sorted(by_gap.items())}
    report["concession_share_of_gap_to_higher_of_offer_and_floor"] = spread(concession)
    report["concession_share_by_prior_asks"] = {k: spread(v) for k, v in sorted(by_turn.items())}
    report["accept_margin_over_floor"] = spread([(c["price"] - c["floor"]) / c["floor"] for c in follow if c["action"] == "accept" and c["price"]])
    report["counter_price_below_floor"] = sum(c["action"] == "ask" and (c["price"] or 0) < c["floor"] for c in calls)
    report["accept_below_floor_blocked_by_code"] = sum(c["action"] == "accept" and c["offers"] and c["offers"][-1] < c["floor"] for c in calls)
    return report


def deals(root):
    """Signed offers joined to pricing logs by task: actual price against that task's quote, floor and cost."""
    root = Path(root)
    pricing = defaultdict(list)
    for path in (root / "logs/pricing").iterdir():
        try:
            row = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        pricing[row.get("task")].append(row)
    rows = []
    for path in (root / ".market/signed-offers").glob("*.json"):
        offer = json.loads(path.read_text(encoding="utf-8"))
        steps = sorted(pricing.get(offer.get("task"), []), key=lambda r: r["at"])
        if not steps:
            continue
        last = steps[-1]
        rows.append({"price": offer["price"], "cost": offer.get("cost", last.get("cost")), "quote": last["quote"], "floor": last["floor"],
                     "steps": len(steps), "discount_stage": last.get("discount_stage", 0)})
    return rows


def summarize_deals(rows):
    if not rows:
        return None
    def per(key, top, bottom):
        return spread([top(r) / bottom(r) for r in rows if bottom(r)])
    return {"signed_offers": len(rows), "price_per_quote": per("q", lambda r: r["price"], lambda r: r["quote"]),
            "price_per_floor": per("f", lambda r: r["price"], lambda r: r["floor"]),
            "price_per_cost": per("c", lambda r: r["price"], lambda r: r["cost"]),
            "profit_per_deal_before_fees": spread([r["price"] - r["cost"] for r in rows]),
            "at_quote": sum(r["price"] >= r["quote"] for r in rows), "at_floor": sum(r["price"] == r["floor"] for r in rows),
            "pricing_steps": dict(sorted(Counter(min(r["steps"], 7) for r in rows).items())),
            "in_clearance": sum(bool(r["discount_stage"]) for r in rows),
            "total_margin": sum(r["price"] - r["cost"] for r in rows)}


def main():
    print(json.dumps({"model_decisions": summarize(load_calls(ROOT)), "signed_deals": summarize_deals(deals(ROOT))}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
