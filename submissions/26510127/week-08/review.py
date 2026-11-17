"""Review hook (Round 3): stars and one sentence for a settled deal, from the market's own record only.

    python review.py --deal .market/deal.json

The kit runs it after each paid deal ("review" in agent.json) and reads the last line {"stars", "review"}.
deal.json: {"order": {...}, "receipt": {...}, "settled": {shop, sku, name, grade, claim, claim_honest,
            expires_at, warranty_hours, value, ledger}}
Code decides, no harness (EP 5: a failing model once gave 6,837 one-star reviews):
  claim_honest false -> 1 star | value 0 (food did not last) -> 2 | price <= 80% of value -> 5
  price <= value -> 4 | above value -> 3
"""
import argparse
import json
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def judge(deal):
    r, s = deal.get("receipt") or {}, deal.get("settled") or {}
    price, value = r.get("price"), s.get("value")
    name = s.get("name") or s.get("sku") or r.get("item")
    grade = s.get("grade")
    facts = f"{name}, grade {grade}" if grade else str(name)
    if s.get("claim_honest") is False:
        return 1, f"{facts}: the shop claimed grade {s.get('claim')} but the market recorded grade {grade}; paid {price}."
    if not isinstance(price, (int, float)) or not isinstance(value, (int, float)):
        return 3, f"Bought {facts} for {price}; the settled record had no value to compare."
    if value <= 0:
        return 2, f"Paid {price} for {facts}, but it was worth nothing on arrival (did not last the time I needed)."
    extra = f", {s['warranty_hours']} h warranty" if s.get("warranty_hours") else ""
    if price <= 0.8 * value:
        return 5, f"Paid {price} for {facts} (list {value}){extra}; settled cleanly."
    if price <= value:
        return 4, f"Paid {price} for {facts} (list {value}){extra}; settled cleanly."
    return 3, f"Paid {price} for {facts}, above its list price of {value}{extra}."


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--deal", required=True)
    a = ap.parse_args()
    try:
        deal = json.loads(Path(a.deal).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        print(json.dumps({"stars": 3, "review": "Settled; the deal file could not be read."}))
        return
    stars, review = judge(deal)
    try:                                                  # the board: [구매 기록], at most one per 5 minutes
        import forum
        forum.note_deal(deal, Path(a.deal).resolve().parent)
    except Exception as e:
        print(f"[forum] skipped: {type(e).__name__} {e}")
    print(json.dumps({"stars": stars, "review": review}, ensure_ascii=False))


if __name__ == "__main__":
    main()
