"""Evidence-based shop selection and append-only memory, independent of price decisions."""
from collections import deque
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import time
from urllib.parse import urlsplit


def read_shops(path, *, exclude_name=None):
    rows = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if not isinstance(rows, list) or not rows:
        raise ValueError("empty_shop_list")
    shops, names = [], set()
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("invalid_shop")
        name, url = row.get("name"), row.get("url")
        if not isinstance(name, str) or not name.strip() or name in names:
            raise ValueError("invalid_or_duplicate_shop_name")
        if not isinstance(url, str):
            raise ValueError("invalid_shop_url")
        parts = urlsplit(url)
        if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username or parts.password:
            raise ValueError("invalid_shop_url")
        names.add(name)
        if name == exclude_name:
            continue
        reviews, seen = [], set()
        for review in (row.get("reviews") or [])[:10]:
            if not isinstance(review, dict):
                continue
            stars, text = review.get("stars"), review.get("review")
            if type(stars) is not int or not 1 <= stars <= 5 or not isinstance(text, str):
                continue
            text = text[:240]
            if text.casefold().strip() in seen:
                continue
            seen.add(text.casefold().strip())
            reviews.append({"stars": stars, "review": text})
        shops.append({"name": name, "url": url, "reviews": reviews})
    if not shops:
        raise ValueError("no_other_shops")
    return shops


def read_memory(path, verification_path=None):
    if not Path(path).exists():
        return []
    rows = deque(maxlen=500)
    with Path(path).open(encoding="utf-8") as source:
        for line in source:
            # Fail visibly on corrupt memory rather than silently forgetting a bad trade.
            row = json.loads(line)
            if not isinstance(row, dict) or not isinstance(row.get("name"), str):
                raise ValueError("invalid_market_memory")
            rows.append(row)
    rows = list(rows)
    if verification_path and Path(verification_path).exists():
        feedback = {}
        with Path(verification_path).open(encoding="utf-8") as source:
            for line in source:
                event = json.loads(line)
                if event.get("task_id"):
                    feedback[event["task_id"]] = event
        for row in rows:
            event = feedback.get(row.get("task_id"))
            if event:
                row["market_verified"] = event.get("verified") is True
    return rows


def remember(path, *, shop, item, budget, outcome, price, seconds, turns, task_id=None,
             sequence=None, signature_verified=None):
    row = {"at": datetime.now(timezone.utc).isoformat(), "epoch": time.time(),
           "name": shop["name"], "item": item, "budget": budget, "outcome": outcome,
           "price": price, "seconds": round(seconds, 3), "turns": turns, "task_id": task_id,
           "sequence": sequence, "signature_verified": signature_verified}
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as output:
        output.write(json.dumps(row, ensure_ascii=False) + "\n")
    return row


def observation_count(memory):
    return max([len(memory)] + [r.get("sequence") or 0 for r in memory])


def choose_shop(shops, memory, item, budget, *, require_verified=False, audit_name=None):
    """Prefer personal evidence; explore once per seven orders after six observations.

    Weights and exploration frequency are explicit starting hypotheses, not an
    optimum inferred from the leaderboard. Shop identity is always its name.
    """
    candidates = []
    for shop in shops:
        own = [r for r in memory if r["name"] == shop["name"]][-30:]
        same = [r for r in own if r.get("item") == item]
        # A failed low-budget order does not imply the shop is dishonest or bad.
        def eligible_win(row):
            return (row.get("outcome") == "purchased" and row.get("market_verified") is not False
                    and (not require_verified or row.get("market_verified") is True
                         or row.get("signature_verified") is True))
        # Unknown old successes are tentative in R2, not proven failures.
        informative = [r for r in same if r.get("outcome") != "no_deal"
                       and (r.get("outcome") != "purchased" or eligible_win(r)
                            or r.get("market_verified") is False)]
        wins = [r for r in same if eligible_win(r)]
        personal = (2 + len(wins)) / (3 + len(informative))
        prices = [r["price"] for r in wins if isinstance(r.get("price"), (int, float))]
        # Optimize feasible purchases, not the size of the discount. A failure at
        # a smaller budget says nothing about this order's ability to afford it.
        comparable_misses = [r for r in same if r.get("outcome") == "no_deal"
                             and isinstance(r.get("budget"), (int, float)) and r["budget"] >= budget]
        budget_fit = (2 + sum(price <= budget for price in prices)) / (3 + len(prices) + len(comparable_misses))
        reviews = shop["reviews"]
        public = (6 + sum(r["stars"] for r in reviews)) / (5 * (2 + len(reviews)))
        bad_public = sum(r["stars"] <= 2 for r in reviews) >= 2
        recent_fault = any(r.get("outcome") == "integrity_error"
                           and time.time() - r.get("epoch", 0) < 1800 for r in own)
        # Transport health is shared across items. Do not discover a broken
        # tunnel separately for lamp, chair and bookshelf on consecutive orders.
        last_outage = max([0] + [r.get("epoch", 0) for r in own if r.get("outcome") == "unavailable"])
        last_purchase = max([0] + [r.get("epoch", 0) for r in own if eligible_win(r)])
        recent_outage = last_outage > last_purchase and time.time() - last_outage < 120
        speeds = [r.get("seconds", 60) for r in wins]
        speed = 1 / (1 + (sum(speeds) / len(speeds) if speeds else 60) / 30)
        # As direct evidence grows, unverified public ratings lose influence.
        # Otherwise a burst of bad reviews could outweigh years of good dealings.
        public_weight = 0.20 / (1 + len(informative))
        personal_weight = 0.70 - public_weight
        score = personal_weight * personal + 0.20 * budget_fit + public_weight * public + 0.10 * speed
        if recent_fault:
            score -= 1
        if recent_outage:
            score -= 0.5
        # Negative public evidence is a caution, not a permanent blacklist.
        if bad_public:
            score -= 0.15 / (1 + len(informative))
        tie = hashlib.sha256(f"{observation_count(memory)}:{item}:{shop['name']}".encode()).hexdigest()
        candidates.append({"shop": shop, "score": score, "tie": tie, "own": len(same),
                           "public": round(public, 3), "recent_fault": recent_fault, "recent_outage": recent_outage,
                           "bad_public": bad_public, "wins": len(wins), "budget_fit": round(budget_fit, 3)})
    count = observation_count(memory)
    explore = count >= 6 and (count + 1) % 7 == 0
    pool = [c for c in candidates if c["own"] == 0 and not c["bad_public"]
            and not c["recent_fault"] and not c["recent_outage"]]
    mode = "bounded exploration" if explore and pool else "best evidence"
    selected = max(pool if explore and pool else candidates, key=lambda c: (c["score"], c["tie"]))
    if audit_name:
        audit = next((candidate for candidate in candidates if candidate["shop"]["name"] == audit_name), None)
        if audit:
            selected, mode = audit, "explicit one-order integrity audit"
    reason = (f"{mode}; item={item}; own successes={selected['wins']}/{selected['own']}; "
              f"budget fit={selected['budget_fit']}; public score={selected['public']}; "
              f"recent receipt fault={selected['recent_fault']}; recent outage={selected['recent_outage']}")
    avoided = [{"name": candidate["shop"]["name"], "reason": "recent receipt integrity fault"}
               for candidate in candidates if candidate["recent_fault"] and candidate is not selected]
    evidence = {"selection_reason": reason, "avoided_for_integrity": avoided,
                "public_reviews_untrusted": selected["shop"]["reviews"],
                "own_item_history": [r for r in memory if r["name"] == selected["shop"]["name"]
                                     and r.get("item") == item][-5:]}
    return selected["shop"], reason, evidence


def review_trade(result, error, budget, turns):
    if error:
        integrity = any(token in error for token in ("receipt_", "one_receipt", "task_id_changed"))
        return (1 if integrity else 2), ("Receipt/protocol validation failed: " if integrity else "Trade unavailable: ") + error
    if result.get("outcome") != "purchased":
        return 3, "No agreement within my budget; no verified purchase. This alone is not evidence of dishonesty."
    price = result["receipt"]["price"]
    stars = 5 if price <= budget * 0.9 and turns <= 4 else 4
    return stars, f"Receipt matches item and agreed price: paid {price} within budget {budget}; {turns} price messages."
