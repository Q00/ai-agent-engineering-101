"""The review hook: stars and a review from what the market settled, not from what the shop said.

    python review.py --deal .market/deal.json     # the kit runs this after each paid deal

The settled receipt says what we really got (grade, whether the shop's grade claim was true, what it is worth
to the order, the warranty). That goes into buyer_memory.json for the next pick, and decides the stars:
  1  the shop's grade claim was false
  2  the item is worth nothing to the order (food that will not last)
  3  paid more than 95% of the list price
  4  paid 80-95% of list
  5  paid at most 80% of list
The review is facts only, so other buyers can check it against the ledger entry it cites.
"""
import argparse
import json
import sys

from market_buyer import MEMORY, load_memory, save_memory


def judge(price, s):
    value = s.get("value")
    lp = value or 0
    if s.get("claim") and s.get("claim_honest") is False:
        return 1, f"Claimed grade {s['claim']}, the market's record says {s.get('grade')}"
    if value == 0:
        return 2, f"Paid {price} for an item that is worth nothing to the order (grade {s.get('grade')})"
    share = price / lp if lp else 1
    stars = 5 if share <= 0.8 else 4 if share <= 0.95 else 3
    return stars, f"Paid {price} for grade {s.get('grade')}, {share:.0%} of list price"


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser()
    ap.add_argument("--deal", required=True)
    ap.add_argument("--memory", default=MEMORY)
    a = ap.parse_args()
    deal = json.load(open(a.deal, encoding="utf-8"))
    s = deal.get("settled") or {}
    price = (deal.get("receipt") or {}).get("price") or 0
    stars, text = judge(price, s)
    extra = []
    if s.get("warranty_hours"):
        extra.append(f"{s['warranty_hours']:g} h warranty")
    if s.get("claim_honest"):
        extra.append("grade claim true")
    review = f"{text}" + (f"; {', '.join(extra)}" if extra else "") + f" (ledger #{s.get('ledger')})."

    mem = load_memory(a.memory)
    t = next((t for t in reversed(mem["trades"]) if t.get("order") == deal.get("order")), None)
    if t is not None:
        t.update(paid=True, grade=s.get("grade"), claim=s.get("claim"), claim_honest=s.get("claim_honest"),
                 value=s.get("value"), ledger=s.get("ledger"), warranty_hours=s.get("warranty_hours"),
                 stars=stars)
        save_memory(a.memory, mem)
    print(json.dumps({"stars": stars, "review": review}, ensure_ascii=False))


if __name__ == "__main__":
    main()
