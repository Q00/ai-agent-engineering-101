"""A trade's process and result as one record: an A2A artifact on the seller side,
a line in trades/<side>.jsonl on both sides.

The record has no top-level "price" key on purpose: the trade test takes the
first data Part with a numeric "price" as the receipt.
"""
import json
import os
import time

HERE = os.path.dirname(os.path.abspath(__file__))
TRADES = os.path.join(HERE, "trades")


def build(side, deal, outcome, final_price=None, counterpart=""):
    return {
        "side": side,
        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
        "item": deal["item"],
        "counterpart": counterpart,
        "outcome": outcome,              # completed / rejected / canceled / failed / open
        "final_price": final_price,
        "turns": [{"from": h["from"], "price": h.get("price"), "text": h.get("text", "")}
                  for h in deal["history"]],
        "guards": list(deal.get("guards", [])),   # times the last line of defense overrode the brain
    }


def to_text(rec):
    lines = [f"{rec['item']}: {rec['outcome']}"
             + (f" at {rec['final_price']}" if rec["final_price"] is not None else "")]
    for i, t in enumerate(rec["turns"], 1):
        lines.append(f"{i}. {t['from']}: {t['price'] if t['price'] is not None else '-'} | {t['text']}")
    for g in rec["guards"]:
        lines.append(f"GUARD turn {g['turn']}: {g['reason']}")
    return "\n".join(lines)


def save(rec):
    os.makedirs(TRADES, exist_ok=True)
    with open(os.path.join(TRADES, f"{rec['side']}.jsonl"), "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
