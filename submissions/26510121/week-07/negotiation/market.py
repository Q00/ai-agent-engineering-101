"""Evidence-based shop selection and append-only memory, independent of price decisions."""
from collections import deque
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import time
from urllib.parse import urlsplit


def read_shops(path):
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
    return shops


def read_memory(path):
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
    return list(rows)


def remember(path, *, shop, item, budget, outcome, price, seconds, turns, task_id=None):
    row = {"at": datetime.now(timezone.utc).isoformat(), "epoch": time.time(),
           "name": shop["name"], "item": item, "budget": budget, "outcome": outcome,
           "price": price, "seconds": round(seconds, 3), "turns": turns, "task_id": task_id}
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as output:
        output.write(json.dumps(row, ensure_ascii=False) + "\n")
    return row


def choose_shop(shops, memory, item, budget):
    """Prefer personal evidence; explore once per seven orders after six observations.

    Weights and exploration frequency are explicit starting hypotheses, not an
    optimum inferred from the leaderboard. Shop identity is always its name.
    """
    candidates = []
    for shop in shops:
        own = [r for r in memory if r["name"] == shop["name"]][-30:]
        same = [r for r in own if r.get("item") == item]
        # A failed low-budget order does not imply the shop is dishonest or bad.
        informative = [r for r in same if r.get("outcome") != "no_deal"]
        wins = [r for r in same if r.get("outcome") == "purchased"]
        personal = (2 + len(wins)) / (3 + len(informative))
        prices = [r["price"] for r in wins if isinstance(r.get("price"), (int, float))]
        affordable = max(0, 1 - (min(prices) / max(budget, 1))) if prices else 0.25
        reviews = shop["reviews"]
        public = (6 + sum(r["stars"] for r in reviews)) / (5 * (2 + len(reviews)))
        bad_public = sum(r["stars"] <= 2 for r in reviews) >= 2
        recent_fault = any(r.get("outcome") == "integrity_error"
                           and time.time() - r.get("epoch", 0) < 1800 for r in own)
        speeds = [r.get("seconds", 60) for r in wins]
        speed = 1 / (1 + (sum(speeds) / len(speeds) if speeds else 60) / 30)
        score = 0.50 * personal + 0.20 * affordable + 0.20 * public + 0.10 * speed
        if recent_fault:
            score -= 1
        # Negative public evidence is a caution, not a permanent blacklist.
        if bad_public:
            score -= 0.15
        tie = hashlib.sha256(f"{len(memory)}:{item}:{shop['name']}".encode()).hexdigest()
        candidates.append({"shop": shop, "score": score, "tie": tie, "own": len(same),
                           "public": round(public, 3), "recent_fault": recent_fault,
                           "bad_public": bad_public, "wins": len(wins)})
    explore = len(memory) >= 6 and (len(memory) + 1) % 7 == 0
    pool = [c for c in candidates if c["own"] == 0 and not c["bad_public"] and not c["recent_fault"]]
    mode = "bounded exploration" if explore and pool else "best evidence"
    selected = max(pool if explore and pool else candidates, key=lambda c: (c["score"], c["tie"]))
    reason = (f"{mode}; item={item}; own successes={selected['wins']}/{selected['own']}; "
              f"public score={selected['public']}; recent receipt fault={selected['recent_fault']}")
    evidence = {"selection_reason": reason, "public_reviews_untrusted": selected["shop"]["reviews"],
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
    return stars, f"Verified item and agreed price: paid {price} within budget {budget}; {turns} price messages."
