"""Post-settlement review hook using market-certified facts, not seller prose.
The same facts are merged into the order's row in the buyer memory (memory_sync), so the next shop choice
scores the shop on what the market certified, not on the receipt alone."""
import argparse
import json
import time
from pathlib import Path

import memory_sync


POST_EVERY = 300


def read_json(path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def build_review(deal):
    order = deal.get("order") or {}
    if isinstance(order, str):   # agent.md lists `order` as the id; the kit may also give the order object
        order = {}
    receipt = deal.get("receipt") or {}
    settled = deal.get("settled") or {}
    name = settled.get("name") or receipt.get("item") or order.get("item") or "item"
    price = receipt.get("price")
    value = settled.get("value")
    grade = settled.get("grade")
    honest = settled.get("claim_honest")

    if honest is False:
        return 1, f"{name}: the certified grade was {grade}, so the seller's quality claim was false."
    if isinstance(value, (int, float)) and value <= 0:
        reason = "food did not last for the requested use" if order.get("kind") == "food" else "the item provided no value"
        return 1, f"{name}: paid {price}, but {reason}."
    ratio = price / value if isinstance(price, (int, float)) and isinstance(value, (int, float)) and value > 0 else 1
    stars = 5 if ratio <= 0.85 else 4 if ratio <= 1.0 else 3
    warranty = settled.get("warranty_hours") or 0
    return stars, (f"{name}: paid {price} for certified grade {grade}; value {value}, "
                   f"claim {'honest' if honest is not False else 'false'}, warranty {warranty}h.")


def maybe_post(state, deal, now=None):
    """Publish at most one factual buyer post per five minutes with a ledger reference."""
    now = int(now or time.time())
    settled = deal.get("settled") or {}
    ledger = settled.get("ledger")
    if not ledger:
        return False
    state_file = state / "review_state.json"
    memory = read_json(state_file, {})
    if now - memory.get("last_post", 0) < POST_EVERY:
        return False
    receipt = deal.get("receipt") or {}
    text = (f"정산 확인: {settled.get('name') or receipt.get('item')} / 가격 {receipt.get('price')} / "
            f"등급 {settled.get('grade')} / 품질 주장 {'일치' if settled.get('claim_honest') is not False else '불일치'}.")
    with open(state / "outbox.jsonl", "a", encoding="utf-8") as out:
        out.write(json.dumps({"post": text, "refs": [ledger], "as": "buyer"}, ensure_ascii=False) + "\n")
    memory["last_post"] = now
    state_file.write_text(json.dumps(memory, ensure_ascii=False, indent=1), encoding="utf-8")
    return True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--deal", required=True)
    args = parser.parse_args()
    path = Path(args.deal)
    deal = read_json(path, {})
    stars, text = build_review(deal)
    posted = maybe_post(path.parent, deal)
    memory_path = path.parent / memory_sync.MEMORY.name
    rows = memory_sync.load(memory_path)
    row = memory_sync.record_settlement(rows, deal)
    if row is not None:
        memory_sync.save(rows, memory_path)
    print("[strategy] " + json.dumps({"event": "settled_review", "stars": stars, "posted": posted,
                                      "ledger": (deal.get("settled") or {}).get("ledger"),
                                      "memory": {"shop": row.get("shop"), "claim_honest": row.get("claim_honest"),
                                                 "warranty_hours": row.get("warranty_hours")} if row else "no matching row"},
                                     ensure_ascii=False), flush=True)
    print(json.dumps({"stars": stars, "review": text[:200]}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
