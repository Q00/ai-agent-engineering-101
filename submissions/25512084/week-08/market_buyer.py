import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
import statistics
import uuid

import httpx


MEMORY_FILE = Path(".market/buyer-memory.json")
CATALOG_FILE = Path(".market/catalog.json")
FORUM_STATE_FILE = Path(".market/buyer-forum-state.json")
PAYMENTS_FILE = Path(".market/payments.jsonl")
OUTBOX_FILE = Path(".market/outbox.jsonl")
FORUM_POST_INTERVAL = 330
HEADERS = {
    "A2A-Version": "1.0",
    "Content-Type": "application/json",
}

# We already have hundreds of deals.
# Round 3 priority is surplus, not deal volume.
MAX_SHOPS_TO_PROBE = 4
MAX_OFFERS_PER_SHOP = 2


def load_memory():
    if not MEMORY_FILE.exists():
        return []

    try:
        data = json.loads(MEMORY_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def save_memory(memory):
    MEMORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    MEMORY_FILE.write_text(
        json.dumps(memory, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
def load_catalog():
    if not CATALOG_FILE.exists():
        return {}

    try:
        return json.loads(
            CATALOG_FILE.read_text(encoding="utf-8")
        )
    except Exception:
        return {}
def maybe_post_verified_deal():
    import json
    import time

    if not PAYMENTS_FILE.exists() or not CATALOG_FILE.exists():
        return

    # Load persistent forum state.
    state = {}
    if FORUM_STATE_FILE.exists():
        try:
            state = json.loads(
                FORUM_STATE_FILE.read_text(encoding="utf-8")
            )
        except Exception:
            state = {}

    now = time.time()
    last_post_ts = float(state.get("last_post_ts", 0) or 0)
    last_ledger = state.get("last_ledger")

    # Do not generate posts more often than our own safe interval.
    if now - last_post_ts < FORUM_POST_INTERVAL:
        return

    # Find the newest actually-paid buyer purchase.
    payment = None
    try:
        lines = PAYMENTS_FILE.read_text(
            encoding="utf-8"
        ).splitlines()

        for line in reversed(lines):
            try:
                row = json.loads(line)
            except Exception:
                continue

            if row.get("paid") is True and row.get("ledger"):
                payment = row
                break
    except Exception:
        return

    if not payment:
        return

    ledger = payment.get("ledger")

    # Never post the same settled purchase twice.
    if ledger == last_ledger:
        return

    sku = str(payment.get("item") or "")
    try:
        price = int(float(payment.get("price")))
    except (TypeError, ValueError):
        return

    product = load_catalog().get(sku, {})

    try:
        list_price = int(float(product.get("list_price", 0)))
    except (TypeError, ValueError):
        return

    if list_price <= 0 or price <= 0:
        return

    surplus = list_price - price

    # Only share genuinely useful positive-value purchases.
    if surplus <= 0:
        return

    discount_pct = 100.0 * surplus / list_price

    # Keep forum intelligence selective rather than posting weak deals.
    if discount_pct < 10.0:
        return

    name = str(product.get("name") or sku)
    category = str(product.get("category") or "market purchase")
    short_category = category.split(">")[-1].strip() or category

    post = (
        f"[Verified buyer deal] {short_category}\n"
        f"{name} ({sku})\n"
        f"Paid {price:,} vs list {list_price:,} — "
        f"{discount_pct:.1f}% below list "
        f"(+{surplus:,} nominal buyer surplus). "
        f"Ledger #{ledger}."
    )

    message = {
        "post": post,
        "refs": [ledger],
        "as": "buyer",
    }

    try:
        OUTBOX_FILE.parent.mkdir(parents=True, exist_ok=True)

        with OUTBOX_FILE.open(
            "a",
            encoding="utf-8",
        ) as f:
            f.write(
                json.dumps(
                    message,
                    ensure_ascii=False,
                )
                + "\n"
            )

        FORUM_STATE_FILE.write_text(
            json.dumps(
                {
                    "last_post_ts": now,
                    "last_ledger": ledger,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

        print(
            f"[forum-plan] queued verified buyer post "
            f"for ledger #{ledger}"
        )

    except Exception as exc:
        print(f"[forum-plan] failed: {exc}")

def receipt_matches_order(receipt, requested_item, acceptable):
    if not isinstance(receipt, dict):
        return False

    sku = receipt.get("item")
    if not sku:
        return False

    if acceptable:
        return sku in acceptable

    return sku == requested_item

def remember(memory, **entry):
    entry["time"] = datetime.now(timezone.utc).isoformat()
    memory.append(entry)

    # Prevent the memory file from growing forever.
    if len(memory) > 5000:
        memory = memory[-5000:]

    save_memory(memory)


def percentile(values, q):
    values = sorted(float(v) for v in values)
    if not values:
        return None

    if len(values) == 1:
        return values[0]

    pos = (len(values) - 1) * q
    lower = int(pos)
    upper = min(lower + 1, len(values) - 1)
    weight = pos - lower

    return values[lower] * (1 - weight) + values[upper] * weight
def remove_suspicious_quotes(probes):
    if len(probes) < 2:
        return probes

    quotes = sorted(
        float(p["quote"])
        for p in probes
        if p.get("quote") is not None
    )

    if len(quotes) < 2:
        return probes

    median_quote = statistics.median(quotes)

    # Values like 1, 2, 24, 102 are usually numbers accidentally
    # parsed from seller text rather than actual prices.
    low_limit = max(100, median_quote * 0.25)

    cleaned = [
        p for p in probes
        if float(p["quote"]) >= low_limit
    ]

    removed = [
        p for p in probes
        if float(p["quote"]) < low_limit
    ]

    for p in removed:
        print(
            f"[quote-filter] ignoring suspicious ask "
            f"{p['quote']} from {p['shop']['name']} "
            f"(market median={median_quote:.0f})"
        )

    return cleaned if cleaned else probes

def review_score(shop):
    reviews = shop.get("reviews", [])

    stars = [
        float(r.get("stars", 0))
        for r in reviews
        if isinstance(r, dict) and r.get("stars") is not None
    ]

    if not stars:
        return 3.5

    # Recent reviews matter more than very old reputation.
    return statistics.mean(stars[:10])


def personal_shop_stats(shop_name, memory):
    trades = [
        x for x in memory
        if x.get("shop") == shop_name
    ]

    if not trades:
        return {
            "attempts": 0,
            "deals": 0,
            "failures": 0,
        }

    deals = sum(x.get("result") == "deal" for x in trades)
    failures = sum(
        x.get("result") in {
            "failed",
            "unreachable",
            "bad_response",
        }
        for x in trades
    )

    return {
        "attempts": len(trades),
        "deals": deals,
        "failures": failures,
    }


def shop_score(shop, memory):
    score = review_score(shop)

    stats = personal_shop_stats(shop["name"], memory)

    if stats["attempts"]:
        success_rate = stats["deals"] / stats["attempts"]
        score += 0.8 * success_rate

    # Strongly penalize shops that repeatedly failed for us.
    score -= min(1.5, stats["failures"] * 0.25)

    return score


def candidate_shops(shops, memory):
    # In Round 3, shops.json contains "offers":
    # only SKUs from this shop that are acceptable for the current order.
    has_offer_metadata = any(
        "offers" in shop
        for shop in shops
    )

    if has_offer_metadata:
        eligible = [
            shop
            for shop in shops
            if shop.get("offers")
        ]
    else:
        # Compatibility with older rounds/tests.
        eligible = shops

    print(
        f"[shop-filter] compatible shops="
        f"{len(eligible)}/{len(shops)}"
    )

    ranked = sorted(
        eligible,
        key=lambda s: shop_score(s, memory),
        reverse=True,
    )

    return ranked[:MAX_SHOPS_TO_PROBE]


def fetch_card(base_url):
    card_url = base_url.rstrip("/") + "/.well-known/agent-card.json"

    response = httpx.get(
        card_url,
        headers={"A2A-Version": "1.0"},
        timeout=8,
    )
    response.raise_for_status()

    return response.json()


def send_message(url, text, task_id=None, context_id=None):
    message = {
        "role": "ROLE_USER",
        "messageId": str(uuid.uuid4()),
        "parts": [{"text": str(text)}],
    }

    if task_id:
        message["taskId"] = task_id

    if context_id:
        message["contextId"] = context_id

    payload = {
        "jsonrpc": "2.0",
        "id": str(uuid.uuid4()),
        "method": "SendMessage",
        "params": {"message": message},
    }

    response = httpx.post(
        url,
        headers=HEADERS,
        json=payload,
        timeout=20,
    )
    response.raise_for_status()

    data = response.json()

    if "error" in data:
        raise RuntimeError(str(data["error"]))

    return data["result"]["task"]


def state_of(task):
    return str(
        task.get("status", {}).get("state", "")
    ).lower()


def task_text(task):
    message = task.get("status", {}).get("message") or {}

    texts = []

    for part in message.get("parts", []):
        if isinstance(part, dict) and "text" in part:
            texts.append(str(part["text"]))

    return " ".join(texts)


def last_number(text):
    import re

    text = str(text or "").replace(",", "")

    # Prefer numbers that are explicitly attached to price language.
    patterns = [
        r"(?i)(?:asking|ask|price|counteroffer|counter|offer)\D{0,20}(\d+(?:\.\d+)?)",
        r"(?i)(\d+(?:\.\d+)?)\s*(?:credits?|won|krw)",
    ]

    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            try:
                return int(float(match.group(1)))
            except ValueError:
                pass

    # Fallback: ignore tiny numbers such as grade counts,
    # warranty hours, quantities, etc. Prefer the largest plausible price.
    values = []

    for raw in re.findall(r"(?<![\w.])\d+(?:\.\d+)?", text):
        try:
            value = int(float(raw))
        except ValueError:
            continue

        if value >= 100:
            values.append(value)

    if not values:
        return None

    return max(values)

def receipt_from_task(task):
    for artifact in task.get("artifacts", []):
        for part in artifact.get("parts", []):
            data = part.get("data")

            if isinstance(data, dict):
                return data

    return None


def historical_prices(item, memory):
    prices = []

    for trade in memory:
        if (
            trade.get("item") == item
            and trade.get("result") == "deal"
            and trade.get("price") is not None
        ):
            try:
                prices.append(float(trade["price"]))
            except (TypeError, ValueError):
                pass

    return prices


def build_max_pay(item, budget, quotes, memory):
    """
    Current-market pricing guard.

    Historical category prices are NOT used as a hard ceiling because
    one category can contain products with very different list prices.

    We compare current shops instead:
    - if the cheapest quote is already clearly below the market median,
      we may accept up to that cheapest quote;
    - otherwise we require about a 10% discount from the cheapest ask.

    The exact SKU list_price is checked separately before checkout.
    """

    quotes = sorted(float(q) for q in quotes)

    best_quote = quotes[0]
    median_quote = percentile(quotes, 0.50)

    if len(quotes) >= 2 and best_quote <= median_quote * 0.85:
        # Clearly the best current offer.
        target = best_quote
        source = (
            f"best_quote={best_quote:.0f}, "
            f"market_median={median_quote:.0f}, "
            f"best already >=15% below median"
        )
    else:
        # No obvious bargain: negotiate below the cheapest ask.
        target = best_quote * 0.97
        source = (
            f"best_quote={best_quote:.0f}, "
            f"market_median={median_quote:.0f}, "
            f"target=97% of best ask"
        )

    max_pay = min(
        int(budget),
        int(target),
    )

    return max(1, max_pay), source


def probe_shop(shop, item, memory):
    base_url = shop["url"].rstrip("/")
    a2a_url = base_url + "/"

    try:
        fetch_card(base_url)

        acceptable_skus = [
            str(o.get("sku"))
            for o in shop.get("offers", [])
            if o.get("sku")
        ]

        if acceptable_skus:
            sku_text = ", ".join(acceptable_skus[:20])
            request_text = (
                f"I want to buy {item}. "
                f"Quote your best price for ONE of these exact acceptable SKUs: "
                f"{sku_text}. Do not offer any other SKU."
            )
        else:
            request_text = (
                f"I want to buy {item}. "
                f"Please quote your best price for an item in exactly this category."
            )

        task = send_message(
            a2a_url,
            request_text,
        )

    
    except Exception as exc:
        print(
            f"[probe] {shop['name']}: unreachable "
            f"({type(exc).__name__})"
        )

        remember(
            memory,
            shop=shop["name"],
            item=item,
            price=None,
            result="unreachable",
        )

        return None

    state = state_of(task)

    # Some sellers may immediately complete a deal.
    if "completed" in state:
        receipt = receipt_from_task(task)

        if receipt and receipt.get("price") is not None:
            try:
                quote = int(float(receipt["price"]))
            except (TypeError, ValueError):
                quote = None
        else:
            quote = None

        return {
            "shop": shop,
            "url": a2a_url,
            "task": task,
            "quote": quote,
            "completed": True,
        }

    if (
        "failed" in state
        or "rejected" in state
        or "canceled" in state
    ):
        remember(
            memory,
            shop=shop["name"],
            item=item,
            price=None,
            result="failed",
        )
        return None

    if (
        "input_required" not in state
        and "input-required" not in state
    ):
        print(
            f"[probe] {shop['name']}: unexpected state {state}"
        )
        return None

    quote = last_number(task_text(task))

    if quote is None:
        print(
            f"[probe] {shop['name']}: no usable price"
        )
        remember(
            memory,
            shop=shop["name"],
            item=item,
            price=None,
            result="bad_response",
        )
        return None

    print(
        f"[probe] {shop['name']}: ask={quote}, "
        f"reviews={review_score(shop):.2f}"
    )

    return {
        "shop": shop,
        "url": a2a_url,
        "task": task,
        "quote": quote,
        "completed": False,
    }


def negotiate(candidate, max_pay, item, budget, benchmark, memory):
    shop = candidate["shop"]
    task = candidate["task"]
    quote = candidate["quote"]

    if quote is None:
        return None

    # Seller completed immediately.
    if candidate["completed"]:
        receipt = receipt_from_task(task)

        if not receipt:
            return None

        try:
            price = int(float(receipt.get("price")))
        except (TypeError, ValueError):
            return None

        if price <= max_pay and price <= budget:
            return task

        print(
            f"[reject] {shop['name']}: immediate price "
            f"{price} > max_pay {max_pay}"
        )
        return None

    # Start well below our maximum.
    first_offer = max(
        1,
        int(max_pay * 0.85),
    )

    offers = [first_offer, max_pay]

    previous_offer = None

    for offer in offers[:MAX_OFFERS_PER_SHOP]:
        offer = min(
            int(offer),
            int(max_pay),
            int(budget),
        )

        if previous_offer is not None and offer <= previous_offer:
            continue

        print(
            f"[offer] {shop['name']}: {offer} "
            f"(ask={quote}, max_pay={max_pay}, "
            f"benchmark={benchmark})"
        )

        try:
            task = send_message(
                candidate["url"],
                offer,
                task_id=task.get("id"),
                context_id=task.get("contextId"),
            )
        except Exception as exc:
            print(
                f"[trade] {shop['name']}: failed "
                f"({type(exc).__name__})"
            )
            return None

        state = state_of(task)

        if "completed" in state:
            receipt = receipt_from_task(task)

            if not receipt:
                return None

            try:
                price = int(float(receipt.get("price")))
            except (TypeError, ValueError):
                return None

            if price > budget or price > max_pay:
                print(
                    f"[reject] {shop['name']}: receipt price "
                    f"{price} exceeds guard {max_pay}"
                )
                return None

            return task

        if (
            "failed" in state
            or "rejected" in state
            or "canceled" in state
        ):
            return None

        seller_counter = last_number(task_text(task))

        if seller_counter is not None:
            print(
                f"[counter] {shop['name']}: {seller_counter}"
            )

            # A counter already inside our guard can be accepted
            # immediately rather than wasting more rounds.
            if seller_counter <= max_pay:
                try:
                    task = send_message(
                        candidate["url"],
                        seller_counter,
                        task_id=task.get("id"),
                        context_id=task.get("contextId"),
                    )
                except Exception:
                    return None

                if "completed" in state_of(task):
                    receipt = receipt_from_task(task)

                    if receipt:
                        try:
                            price = int(
                                float(receipt.get("price"))
                            )
                        except (TypeError, ValueError):
                            return None

                        if price <= max_pay and price <= budget:
                            return task

                return None

        previous_offer = offer

    return None


def stars_for_price(price, benchmark):
    if not benchmark or benchmark <= 0:
        return 5

    ratio = price / benchmark

    if ratio <= 0.90:
        return 5
    if ratio <= 1.00:
        return 4
    return 3


def output_no_deal(item, budget, memory, reason):
    print(
        f"[walk] {item}: no acceptable deal. {reason}"
    )

    # No-deal reviews are not counted in Round 3, but the kit still
    # expects a valid final JSON object.
    print(json.dumps({
        "shop": "",
        "receipt": None,
        "stars": 3,
        "review": (
            f"No purchase for {item}: offers did not meet "
            f"the buyer's value threshold."
        ),
    }, ensure_ascii=False))


def main():
    maybe_post_verified_deal()
    ap = argparse.ArgumentParser()
    ap.add_argument("--shops", required=True)
    ap.add_argument("--item", required=True)
    ap.add_argument("--budget", required=True, type=int)
    ap.add_argument("--order", required=True)
    args = ap.parse_args()

    item = args.item
    budget = args.budget
    with open(args.order, "r", encoding="utf-8") as f:
        order = json.load(f)

    acceptable = order.get("acceptable") or []
    use_within_hours = order.get("use_within_hours")

    memory = load_memory()

    with open(args.shops, encoding="utf-8") as f:
        shops = json.load(f)

    if not shops:
        raise RuntimeError("no shops available")

    candidates = candidate_shops(
        shops,
        memory,
    )

    print(
        f"[strategy] selective-surplus buyer | "
        f"item={item} budget={budget} "
        f"probe={len(candidates)}"
    )

    probes = []

    for shop in candidates:
        probe = probe_shop(
            shop,
            item,
            memory,
        )

        if (
            probe is not None
            and probe.get("quote") is not None
            and probe["quote"] <= budget
        ):
            probes.append(probe)
    probes = remove_suspicious_quotes(probes)

    if not probes:
        output_no_deal(
            item,
            budget,
            memory,
            "No reachable quote within mandate cap.",
        )
        return

    quote_values = [
        p["quote"]
        for p in probes
    ]

    max_pay, source = build_max_pay(
        item,
        budget,
        quote_values,
        memory,
    )

    market_benchmark = percentile(
        quote_values,
        0.50,
    )

    print(
        f"[guard] quotes={sorted(quote_values)} "
        f"max_pay={max_pay} | {source}"
    )

    # Cheapest quote first. Reputation breaks close ties.
    probes.sort(
        key=lambda p: (
            p["quote"],
            -shop_score(p["shop"], memory),
        )
    )

    for candidate in probes:
        # Do not waste time on an ask wildly above our ceiling.
        if candidate["quote"] > max_pay * 1.35:
            print(
                f"[skip] {candidate['shop']['name']}: "
                f"ask={candidate['quote']} too far above "
                f"max_pay={max_pay}"
            )
            continue

        task = negotiate(
            candidate,
            max_pay=max_pay,
            item=item,
            budget=budget,
            benchmark=market_benchmark,
            memory=memory,
        )

        if task is None:
            continue

        receipt = receipt_from_task(task)

        if receipt is None:
            continue
        if not receipt_matches_order(receipt, item, acceptable):
            print(
                f"[reject] {candidate['shop']['name']}: "
                f"receipt SKU {receipt.get('item')} does not match "
                f"requested category {item}"
            )

            remember(
                memory,
                shop=candidate["shop"]["name"],
                item=item,
                price=None,
                result="bad_response",
                reason="item_mismatch",
                receipt_item=receipt.get("item"),
            )

            continue

        try:
            price = int(float(receipt["price"]))
        except (KeyError, TypeError, ValueError):
            continue
        product = load_catalog().get(
            str(receipt.get("item")),
            {},
        )

        try:
            list_price = int(product.get("list_price", 0))
        except (TypeError, ValueError):
            list_price = 0

        if list_price <= 0:
            print(
                f"[reject] {candidate['shop']['name']}: "
                f"no valid list_price for SKU {receipt.get('item')}"
            )
            continue

        value_ceiling = int(list_price * 0.75)

        if price > value_ceiling:
            print(
                f"[reject] {candidate['shop']['name']}: "
                f"price={price} > 75% list_price={value_ceiling} "
                f"(list={list_price})"
            )

            remember(
                memory,
                shop=candidate["shop"]["name"],
                item=item,
                price=None,
                result="bad_value",
                receipt_item=receipt.get("item"),
                offered_price=price,
                list_price=list_price,
            )

            continue

        print(
            f"[value] SKU={receipt.get('item')} "
            f"price={price} list={list_price} "
            f"nominal_surplus={list_price - price}"
        )

        if price > budget or price > max_pay:
            continue

        stars = stars_for_price(
            price,
            market_benchmark,
        )

        remember(
            memory,
            shop=candidate["shop"]["name"],
            item=item,
            quote=candidate["quote"],
            price=price,
            budget=budget,
            max_pay=max_pay,
            result="deal",
        )

        print(
            f"[deal] {candidate['shop']['name']}: "
            f"price={price}, max_pay={max_pay}, stars={stars}"
        )

        if use_within_hours is not None:
            accept = {
                "cert": True,
                "min_hours_left": float(use_within_hours),
            }
        else:
            accept = {
                "warranty_hours": 6,
            }

        print(json.dumps({
            "shop": candidate["shop"]["url"],
            "receipt": receipt,
            "stars": stars,
            "review": (
                f"Verified negotiation for {item}; "
                f"closed at {price} after comparing "
                f"multiple market offers."
            ),
            "accept": accept,
        }, ensure_ascii=False))

        return

    remember(
        memory,
        shop=None,
        item=item,
        price=None,
        budget=budget,
        max_pay=max_pay,
        result="walk",
    )

    output_no_deal(
        item,
        budget,
        memory,
        f"Best acceptable ceiling was {max_pay}.",
    )


if __name__ == "__main__":
    main()
