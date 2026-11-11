"""real_yellow's review hook: runs after each paid (settled) deal.

    python review.py --deal .market/deal.json

1. Stars from what the market settled, not from what the chat promised: the grade claim checked against the
   ledger (claim_honest), and how far under list price we paid.
2. The outcome goes to the shared DB, next to the buy decision that led to it (the meta harness reads both).
3. Forum, Horvitz-style: post only when the expected usefulness of the post to other buyers clears a threshold.
   A dishonest claim is worth a warning; a deep discount or a shop nobody has written about is worth a tip;
   a routine deal usually is not. The threshold drops the longer we have stayed quiet (post count scores too)
   and lives in the DB, so the meta harness can move it from how our posts were voted.
   Posts are plain ledger facts with the ledger seq as a ref, so anyone can check them.
"""
import argparse
import json
import time
from pathlib import Path

from db import DB

HERE = Path(__file__).resolve().parent
POST_EVERY = 300                     # the market takes one buyer post per 5 minutes
DEFAULT_POLICY = {"threshold": 0.3,  # post when usefulness >= threshold -- was 0.5: a routine deal's
                                     # baseline usefulness (0.3) never cleared it without 30 min of
                                     # quiet first, so the market's 300s cap sat mostly unused on
                                     # ordinary deals. Measured live: our buyer posts already score as
                                     # well as or better than top posters by avg vote score (0.631 vs.
                                     # 0.535-0.639 for the market's most-liked accounts), but we had
                                     # 264 posts to their 406-444 over the same stretch -- the gap was
                                     # in how often we posted, not how good each post was
                  "quiet_bonus": 0.2, "quiet_after": 1800}


def usefulness(s, ratio, first_time):
    """How likely another buyer would want this post, 0..1."""
    if s.get("claim_honest") is False:
        return 0.95                  # a shop claimed a grade the ledger says it did not ship
    u = 0.3
    if ratio is not None and ratio <= 0.65:
        u = 0.7                      # 35% or more under list: a deal worth pointing at
    if first_time:
        u = max(u, 0.6)              # first record of this shop from us
    return u


def post_text(s, price, shop, ratio):
    grade = s.get("grade")
    claim = s.get("claim")
    honest = {True: "정직", False: "허위"}.get(s.get("claim_honest"), "주장 없음")
    off = f"정가 {s.get('value') or '?'}원 대비 {round((1 - ratio) * 100)}% 할인" if ratio is not None else "정가 미상"
    head = "[주의] 등급 허위 표시" if s.get("claim_honest") is False else "[구매 기록]"
    return (f"{head} {s.get('name')} ({s.get('sku')}) | {price:,}원, {off} | 등급 {grade} (주장 {claim}, {honest})"
            f" | 판매: {shop} | ledger #{s.get('ledger')}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--deal", default=str(HERE / ".market" / "deal.json"))
    ap.add_argument("--outbox", default=str(HERE / ".market" / "outbox.jsonl"))
    args = ap.parse_args()

    deal = json.loads(Path(args.deal).read_text(encoding="utf-8"))
    s = deal.get("settled") or {}
    price = int((deal.get("receipt") or {}).get("price") or 0)
    value = s.get("value") or 0                      # list price, or 0 if it did not serve the order
    ratio = price / value if value else None

    if s.get("claim_honest") is False:
        stars, review = 1, f"Claimed grade {s.get('claim')}, the ledger says {s.get('grade')}."
    elif not value:
        stars, review = 2, "Paid, but it did not serve the order (spoiled or broken)."
    else:
        stars = 5 if ratio <= 0.6 else 4 if ratio <= 0.75 else 3
        review = f"{s.get('name')}: paid {price} of list {value}, grade {s.get('grade')}, claim honest."

    now = time.time()
    try:
        with DB() as d:
            shop = (d.q("SELECT name FROM shops WHERE url = $1", s.get("shop")) or [{"name": s.get("shop")}])[0]["name"]
            first_time = not d.q("SELECT 1 FROM trajectory WHERE role = 'buyer' AND decision = 'settled' "
                                 "AND context->>'shop' = $1 LIMIT 1", shop)
            d.log_trajectory(now, "buyer", "settled",
                             {"order": deal.get("order"), "shop": shop, "sku": s.get("sku"), "grade": s.get("grade")},
                             {"price": price, "value": value, "ratio": ratio, "claim_honest": s.get("claim_honest"),
                              "saved": (value - price) / value if value else -1, "stars": stars})

            policy = dict(DEFAULT_POLICY, **d.get_policy("forum", {}))
            last = float(d.get_state("last_post_buyer", 0))
            u = usefulness(s, ratio, first_time)
            bar = policy["threshold"] - (policy["quiet_bonus"] if now - last > policy["quiet_after"] else 0)
            if now - last < POST_EVERY:
                print(f"[forum] skip: last post {int(now - last)} s ago")
            elif u < bar:
                print(f"[forum] skip: usefulness {u} < {bar}")
            else:
                line = {"post": post_text(s, price, shop, ratio), "refs": [s["ledger"]] if s.get("ledger") else [],
                        "as": "buyer"}
                with open(args.outbox, "a", encoding="utf-8") as f:
                    f.write(json.dumps(line, ensure_ascii=False) + "\n")
                d.set_state("last_post_buyer", now)
                d.log_trajectory(now, "buyer", "post", {"usefulness": u, "bar": bar, "ledger": s.get("ledger")},
                                 {"text": line["post"]})
                print(f"[forum] posted (usefulness {u} >= {bar}): {line['post']}")
    except Exception as e:                            # the review still goes out without the DB
        print(f"[db] {type(e).__name__}: {e}")

    print(json.dumps({"stars": stars, "review": review}, ensure_ascii=False))


if __name__ == "__main__":
    main()
