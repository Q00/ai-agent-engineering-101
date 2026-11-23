
"""Week 08 market buyer: reputation-aware shop choice, memory, and A2A trades.

Requires buyer.py and harness.py in the same directory.
The final stdout line is the JSON result expected by market_kit.py.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

import httpx

from buyer import (
    MAX_TURNS,
    TIME_LIMIT_SECONDS,
    extract_last_price,
    fetch_agent_card,
    get_jsonrpc_endpoint,
    get_task,
    send_message,
    status_message_text,
    task_state,
)
from harness import _call_json, buyer_turn

MEMORY_PATH = Path('.market') / 'buyer-memory.json'
MAX_MEMORY_ENTRIES = 1000


def load_shops(path: str) -> list[dict[str, Any]]:
    data = json.loads(Path(path).read_text(encoding='utf-8-sig'))
    if not isinstance(data, list):
        raise ValueError('Shop list must be a JSON array')

    shops = []
    for entry in data:
        if not isinstance(entry, dict):
            continue

        name, url = entry.get('name'), entry.get('url')
        if not isinstance(name, str) or not name.strip():
            continue
        if not isinstance(url, str) or not url.startswith(('http://', 'https://')):
            continue

        reviews = entry.get('reviews', [])
        shops.append({
            'name': name,
            'url': url,
            'reviews': reviews if isinstance(reviews, list) else [],
            'offers': entry.get('offers', []) if isinstance(entry.get('offers'), list) else [],
        })

    if not shops:
        raise ValueError('No valid shops in shop list')

    return shops


def load_order(path: str | None) -> dict[str, Any]:
    if not path:
        return {}
    try:
        value = json.loads(Path(path).read_text(encoding='utf-8'))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def stocked_offers(shop: dict[str, Any], acceptable: set[str]) -> list[dict[str, Any]]:
    """Only offers with stock and an SKU permitted by the order."""
    return [o for o in shop.get('offers', [])
            if isinstance(o, dict) and isinstance(o.get('sku'), str)
            and o['sku'] in acceptable
            and isinstance(o.get('qty'), (int, float)) and not isinstance(o['qty'], bool)
            and o['qty'] > 0]


def select_offer(shop: dict[str, Any], acceptable: set[str], budget: int) -> dict[str, Any] | None:
    """Choose a permitted stocked SKU, using public list prices when available.

    The SKU's cost and quality are not disclosed in shops.json.
    """
    offers = stocked_offers(shop, acceptable)
    if not offers:
        return None
    try:
        catalog = json.loads((Path('.market') / 'catalog.json').read_text(encoding='utf-8'))
        if not isinstance(catalog, dict):
            catalog = {}
    except (OSError, ValueError):
        catalog = {}

    def price(o: dict[str, Any]) -> float:
        c = catalog.get(o['sku'], {})
        try:
            return float(c.get('list_price') or budget)
        except (TypeError, ValueError, AttributeError):
            return float(budget)

    # A reasonable candidate is close to the budget, but not so expensive
    # that a typical seller's cost makes negotiation hopeless.
    affordable = [o for o in offers if price(o) <= budget * 1.15]
    pool = affordable if affordable else offers
    return sorted(pool, key=lambda o: (-price(o) if affordable else price(o), o['sku']))[0]


def acceptance_policy(order: dict[str, Any]) -> dict[str, Any]:
    """Require a valid market-issued certificate, and useful shelf life for food."""
    if not order.get('acceptable'):
        return {}
    policy: dict[str, Any] = {'cert': True}
    if order.get('kind') == 'food':
        needed = order.get('use_within_hours') or 0
        try:
            needed = max(0.0, float(needed))
        except (TypeError, ValueError):
            needed = 0.0
        policy['min_hours_left'] = needed
        # Shelf life itself determines fitness; a grade-C food may still
        # be fine for a short consumption window, hence no fixed grade ban.
    return policy


def load_memory() -> list[dict[str, Any]]:
    if not MEMORY_PATH.exists():
        return []

    try:
        data = json.loads(MEMORY_PATH.read_text(encoding='utf-8'))
        return data[-MAX_MEMORY_ENTRIES:] if isinstance(data, list) else []
    except (OSError, ValueError, TypeError):
        print(
            '[MEMORY] Could not read history; proceeding with empty memory',
            flush=True,
        )
        return []


def save_memory(entries: list[dict[str, Any]]) -> None:
    MEMORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = MEMORY_PATH.with_name(
        f'{MEMORY_PATH.name}.{os.getpid()}.tmp'
    )
    tmp.write_text(
        json.dumps(
            entries[-MAX_MEMORY_ENTRIES:],
            ensure_ascii=False,
            indent=2,
        ),
        encoding='utf-8',
    )
    os.replace(tmp, MEMORY_PATH)


def reputation_summary(
    shop: dict[str, Any],
    memory: list[dict[str, Any]]
) -> dict[str, Any]:
    name = shop['name']
    reviews = [
        r for r in shop['reviews'][:10]
        if isinstance(r, dict)
    ]

    stars = [
        float(r['stars'])
        for r in reviews
        if isinstance(r.get('stars'), (int, float))
        and not isinstance(r.get('stars'), bool)
        and 1 <= r['stars'] <= 5
    ]

    low_stars = sum(s <= 2 for s in stars)

    own = [
        entry for entry in memory
        if entry.get('shop') == name
    ]

    deals = sum(
        entry.get('result') == 'deal'
        for entry in own
    )

    # Buyer-side model or network errors must not count
    # as seller misconduct.
    incidents = sum(
        entry.get('result') in (
            'invalid_receipt',
            'over_budget',
            'receipt_mismatch',
        )
        for entry in own
    )

    service_failures = sum(
        entry.get('result') in (
            'error',
            'model_unavailable',
        )
        for entry in own
    )

    no_deals = sum(
        entry.get('result') == 'no_deal'
        for entry in own
    )

    score = (
        (sum(stars) / len(stars) - 3.0) * 2.0
        if stars else 0.0
    )

    score -= low_stars * 5.0
    score += min(deals, 5) * 0.5
    score -= incidents * 7.0 + min(no_deals, 5) * 0.5

    return {
        'name': name,
        'review_count': len(reviews),
        'avg_stars': (
            round(sum(stars) / len(stars), 2)
            if stars else None
        ),
        'low_stars': low_stars,
        'review_samples': [
            str(r.get('review', ''))[:110]
            for r in reviews[:3]
        ],
        'past_deals': deals,
        'past_no_deals': no_deals,
        'past_incidents': incidents,
        'past_service_failures': service_failures,
        'risk_score': round(score, 2),
    }


# HISTORICAL_RISK_SELECTION_V3
# The public shop offers disclose only sku/name/qty. Real unit expiry and
# verification outcomes must never be inferred from the presence of an offer.
# Kit logs contain the *post-payment* outcome, unlike buyer-memory.json,
# whose "deal" field merely means that the A2A negotiation completed.
_SHELF_REJECTION = re.compile(
    r'not paying:\s*([0-9]+(?:\.[0-9]+)?)\s*h of shelf life left, '
    r'the policy asks for\s*([0-9]+(?:\.[0-9]+)?)',
    re.IGNORECASE,
)
_MARKET_LEDGER = re.compile(r'\[market\] ledger #\d+:')


def recent_verified_outcomes(
    path: Path | None = None,
    *,
    tail_bytes: int = 12_000_000,
) -> list[dict[str, Any]]:
    """Best-effort recent buyer outcomes, tied to the subsequent market ledger.

    Reads a bounded tail so it is cheap even on long-running markets. Missing
    logs, truncated records, and unpaired transactions are NOT positive evidence.
    This is a selection heuristic, not a substitute for ACP verification.
    """
    log_path = path if path is not None else Path('logs') / 'market-kit.log'
    try:
        with log_path.open('rb') as file:
            file.seek(0, os.SEEK_END)
            length = file.tell()
            file.seek(max(0, length - tail_bytes))
            data = file.read().decode('utf-8', errors='replace')
            if length > tail_bytes:
                data = data.split('\n', 1)[-1]  # skip a partial line
    except OSError:
        return []

    results: list[dict[str, Any]] = []
    pending: dict[str, Any] | None = None
    measured: tuple[float, float] | None = None

    for line in data.splitlines():
        if '[buyer] [MEMORY] ' in line:
            pending, measured = None, None
            try:
                record = json.loads(line.split('[MEMORY] ', 1)[1].strip())
                if (isinstance(record, dict)
                        and isinstance(record.get('shop'), str)):
                    pending = record
            except (ValueError, TypeError):
                pass
            continue

        if pending is None:
            continue

        if '[order ' in line:
            # Never associate an orphaned buyer memory with a later order.
            pending, measured = None, None
            continue

        if '[acp] not paying:' in line:
            found = _SHELF_REJECTION.search(line)
            if found:
                measured = (float(found.group(1)), float(found.group(2)))

        if not _MARKET_LEDGER.search(line):
            continue

        if 'verified True' in line:
            outcome = 'verified'
        elif 'no deal' in line:
            outcome = 'no_deal'
        elif measured is not None and 'verified False' in line:
            outcome = 'shelf_rejected'
        elif 'verified False' in line:
            outcome = 'unverified'
        else:
            # Unrelated ledger / an incomplete market record.
            pending, measured = None, None
            continue

        result = {
            'shop': pending['shop'],
            'sku': str(pending.get('requested_sku') or ''),
            'order': str(pending.get('order') or ''),
            'timestamp_utc': pending.get('timestamp_utc', 0),
            'outcome': outcome,
        }
        if outcome == 'shelf_rejected' and measured is not None:
            result['hours_left'], result['hours_required'] = measured
        results.append(result)
        pending, measured = None, None

    return results[-400:]


def _event_freshness(event: dict[str, Any], now: float) -> float:
    """Recent evidence matters more, but older observations are not discarded."""
    try:
        age_hours = max(0.0, (now - float(event.get('timestamp_utc', 0))) / 3600)
        return max(0.25, 1.0 / (1.0 + age_hours / 6.0))
    except (TypeError, ValueError, OverflowError):
        return 0.25


def _candidate_risk(
    summary: dict[str, Any],
    sku: str,
    needed_hours: float,
    verified_outcomes: list[dict[str, Any]],
    memory: list[dict[str, Any]],
    now: float,
) -> dict[str, Any]:
    """A transparent ranking score, NOT an assertion of actual shelf life.

    Public reviews contribute a prior; actual ACP results carry the strongest
    SKU-specific signal. Seller refusals lower selection priority but are not
    reported as dishonesty. Unknown/missing outcomes are not treated as wins.
    """
    name = summary['name']
    # reputation_summary's A2A 'deals' have not yet passed ACP. Remove their
    # small success bonus and replace it with confirmed market evidence below.
    base = float(summary['risk_score']) - min(summary['past_deals'], 5) * 0.5

    seen_order_ids = {
        event['order'] for event in verified_outcomes if event['order']
    }
    matched = [e for e in verified_outcomes if e.get('shop') == name]

    direct_good = direct_rejected = direct_shelf = 0.0
    shop_good = shop_bad = 0.0
    seen_direct_shelf = 0

    for event in matched:
        fresh = _event_freshness(event, now)
        same = bool(sku) and event.get('sku') == sku
        outcome = event['outcome']
        if outcome == 'verified':
            if same:
                direct_good += fresh
            else:
                shop_good += fresh
        elif outcome == 'no_deal':
            if same:
                direct_rejected += fresh
            else:
                shop_bad += fresh
        elif outcome == 'shelf_rejected':
            if same:
                observed = event.get('hours_left')
                if (needed_hours > 0 and isinstance(observed, (int, float))
                        and observed < needed_hours):
                    direct_shelf += fresh
                    seen_direct_shelf += 1
                else:
                    # A past failure against a different use-within window
                    # isn't proof this order would fail; still slight caution.
                    shop_bad += fresh * 0.25
            else:
                shop_bad += fresh * 0.25
        elif outcome == 'unverified':
            if same:
                direct_rejected += fresh * 0.7
            else:
                shop_bad += fresh * 0.3

    # Own memory complements market outcomes, but A2A deals alone are not
    # counted as verified; they cannot override an ACP shelf-life rejection.
    provisional = no_deal_memory = 0.0
    for event in memory[-1000:]:
        if event.get('shop') != name or event.get('requested_sku') != sku:
            continue
        if event.get('order') and str(event['order']) in seen_order_ids:
            continue
        fresh = _event_freshness(event, now)
        if event.get('result') == 'no_deal':
            no_deal_memory += fresh
        elif event.get('result') == 'deal':
            provisional += fresh

    # Caps prevent large shops / a long history from dominating indefinitely.
    score = (
        base
        + 3.0 * min(direct_good, 3.0)
        - 3.0 * min(direct_rejected, 3.0)
        - 10.0 * min(direct_shelf, 2.0)
        + 0.35 * min(shop_good, 5.0)
        - 0.25 * min(shop_bad, 6.0)
        + 0.15 * min(provisional, 3.0)
        - 1.2 * min(no_deal_memory, 4.0)
    )
    return {
        'score': round(score, 3),
        'confirmed_same_sku': round(direct_good, 2),
        'shelf_risk_same_sku': round(direct_shelf, 2),
        'historical_shelf_failures': seen_direct_shelf,
        'rejections_same_sku': round(direct_rejected + no_deal_memory, 2),
        'confirmed_other_skus': round(shop_good, 2),
    }


def choose_shop(
    shops: list[dict[str, Any]],
    memory: list[dict[str, Any]],
    item: str,
    budget: int,
    *,
    acceptable: set[str] | None = None,
    required_hours: float = 0.0,
) -> tuple[dict[str, Any], str]:
    """Select an available shop with the best *observed* risk-adjusted record."""
    if not shops:
        raise ValueError('No shops supplied')

    valid_skus = acceptable or set()
    candidates = [
        shop for shop in shops
        if stocked_offers(shop, valid_skus)
    ] if valid_skus else [
        shop for shop in shops
        if any(isinstance(offer, dict)
               and isinstance(offer.get('qty'), (int, float))
               and not isinstance(offer.get('qty'), bool)
               and offer['qty'] > 0
               for offer in shop.get('offers', []))
    ]
    if not candidates:
        # When the market provides no usable stock snapshot, preserve the
        # original best-effort behavior rather than pretending stock exists.
        candidates = shops

    verified_outcomes = recent_verified_outcomes()
    now = time.time()
    rankings = []
    for shop in candidates:
        summary = reputation_summary(shop, memory)
        offer = select_offer(shop, valid_skus, budget) if valid_skus else None
        sku = str(offer.get('sku') or '') if offer else ''
        features = _candidate_risk(
            summary, sku, required_hours, verified_outcomes, memory, now,
        )
        rankings.append((features['score'], summary, features, shop, sku))

    # Severe *first-hand* receipt incidents take priority over speculation in
    # public reviews. Do not permanently blacklist a shop for no-deals alone.
    trustworthy = [r for r in rankings if r[1]['past_incidents'] == 0]
    if trustworthy:
        rankings = trustworthy

    rankings.sort(key=lambda row: (-row[0], row[1]['name']))
    for score, summary, features, _shop, sku in rankings[:6]:
        print('[RISK V3] ' + json.dumps({
            'name': summary['name'], 'sku': sku, 'score': score,
            'low_stars': summary['low_stars'],
            'verified_same_sku': features['confirmed_same_sku'],
            'shelf_risk_same_sku': features['shelf_risk_same_sku'],
            'rejections_same_sku': features['rejections_same_sku'],
        }, ensure_ascii=False), flush=True)

    _score, _summary, features, shop, sku = rankings[0]
    reason = (
        'Historical risk-aware selection v3: available SKU, reputation, '
        'actual ACP settlement history and shop/SKU failure evidence; '
        'no Groq selection call.'
    )
    print(
        f'[CHOOSE] name={shop["name"]!r} url={shop["url"]!r} '
        f'sku={sku!r} score={features["score"]} reason={reason}',
        flush=True,
    )
    return shop, reason


def receipt_data(
    task: dict[str, Any]
) -> dict[str, Any] | None:
    for artifact in task.get('artifacts', []):
        if not isinstance(artifact, dict):
            continue

        for part in artifact.get('parts', []):
            if not isinstance(part, dict):
                continue

            data = part.get('data')

            if (
                isinstance(data, dict)
                and isinstance(data.get('price'), (int, float))
                and not isinstance(data['price'], bool)
            ):
                # Keep signed receipt unchanged for the kit.
                return data.copy()

    return None


def _model_quota_error(exc: Exception) -> bool:
    """Recognize buyer-owned model quota errors."""
    message = str(exc).lower()

    return any(
        fragment in message
        for fragment in (
            '429',
            'cooldown',
            'rate limit',
            'rate-limit',
            'quota',
        )
    )


def _fallback_buyer_turn(
    *,
    seller_price: int,
    budget: int
) -> dict[str, Any]:
    """Make an affordable offer when the model is rate-limited."""
    offer = max(
        0,
        min(int(seller_price), int(budget))
    )
    return {
        'action': 'offer',
        'price': offer,
    }


# BUYER_HYBRID_NEGOTIATION_V2
# Keep a small number of real LLM decisions as part of the AI-agent design,
# while doing routine bargaining deterministically to conserve Groq quota.
_BUYER_MODEL_THROTTLE_PATH = Path('.market') / 'buyer-model-throttle.json'


def _buyer_rule_decision(*, seller_price: int, budget: int, turn_number: int) -> dict[str, Any]:
    """Conservative budget-safe price offer, with optional small first counter."""
    ask = max(0, int(seller_price))
    cap = max(0, int(budget))
    if cap == 0 and ask > 0:
        return {'action': 'walk'}
    offer = min(ask, cap)
    # The default 2% first-turn counteroffer can be overridden with env var 0.
    # A second turn uses seller's current ask (within budget) to protect deal rate.
    if turn_number == 1 and ask <= cap and ask > 100:
        try:
            pct = float(os.environ.get('BUYER_NEGOTIATION_DISCOUNT_PCT', '0.02'))
        except ValueError:
            pct = 0.02
        pct = max(0.0, min(pct, 0.05))
        discount = min(5000, int(ask * pct))
        offer = max(0, ask - discount)
    return {'action': 'offer', 'price': offer}


def _buyer_model_due() -> bool:
    """Persist a throttle across the per-order Python subprocesses."""
    try:
        requested = int(os.environ.get('BUYER_MODEL_INTERVAL_SECONDS', '3600'))
    except ValueError:
        requested = 3600
    interval = max(900, requested)
    now = time.time()
    try:
        record = json.loads(_BUYER_MODEL_THROTTLE_PATH.read_text(encoding='utf-8'))
        last = float(record.get('last_attempt', 0))
    except (OSError, TypeError, ValueError):
        last = 0.0
    if now - last < interval:
        return False
    # Respect harness's own 429 cooldown state without touching it.
    try:
        cooldown = json.loads((Path('.market') / 'groq-cooldown.json').read_text(encoding='utf-8'))
        if float(cooldown.get('until', 0)) > now:
            return False
    except (OSError, TypeError, ValueError):
        pass
    _BUYER_MODEL_THROTTLE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = _BUYER_MODEL_THROTTLE_PATH.with_name(
        _BUYER_MODEL_THROTTLE_PATH.name + f'.{os.getpid()}.tmp'
    )
    try:
        tmp.write_text(json.dumps({'last_attempt': now}), encoding='utf-8')
        os.replace(tmp, _BUYER_MODEL_THROTTLE_PATH)
    finally:
        if tmp.exists():
            tmp.unlink()
    return True


def _hybrid_buyer_decision(
    *, item: str, budget: int, seller_price: int, turn_number: int,
    history: list[str], task_id: str = ''
) -> dict[str, Any]:
    """At most one model attempt per throttle interval; rules otherwise."""
    if turn_number == 1 and _buyer_model_due():
        try:
            decision = buyer_turn(
                item=item, budget=budget, seller_price=seller_price,
                turn_number=turn_number, history=history,
            )
            print(f'[BUYER HYBRID TURN] model decision task={task_id}', flush=True)
            return decision
        except RuntimeError as exc:
            if not _model_quota_error(exc):
                raise
            print(f'[BUYER HYBRID TURN] model quota unavailable: {type(exc).__name__}', flush=True)
    decision = _buyer_rule_decision(
        seller_price=seller_price, budget=budget, turn_number=turn_number
    )
    print(
        f'[BUYER RULE TURN] task={task_id} turn={turn_number} '
        f'seller_ask={seller_price} budget={budget} '
        f'action={decision["action"]} offer={decision.get("price")}',
        flush=True,
    )
    return decision


def negotiate(
    shop: dict[str, Any],
    item: str,
    budget: int,
    acceptable: set[str] | None = None,
    requested_offer: dict[str, Any] | None = None,
) -> tuple[dict[str, Any] | None, str, str]:
    start = time.monotonic()
    history: list[str] = []
    last_offer: int | None = None

    with httpx.Client(timeout=45.0) as client:
        card = fetch_agent_card(
            client,
            shop['url'],
        )

        endpoint = get_jsonrpc_endpoint(card)

        request_name = (requested_offer or {}).get('name') or item
        request_sku = (requested_offer or {}).get('sku')
        request = (f'I want to buy {request_name} ({request_sku})'
                   if request_sku else f'I want to buy a {item}')
        task = get_task(
            send_message(
                client,
                endpoint,
                request,
            )
        )

        task_id = str(task['id'])
        history.append(f'buyer requested {item}')

        for turn in range(1, MAX_TURNS + 2):
            if time.monotonic() - start > TIME_LIMIT_SECONDS:
                return (
                    None,
                    'no_deal',
                    'Negotiation timed out.',
                )

            state = task_state(task)

            print(
                f'[MARKET BUYER] shop={shop["name"]} '
                f'task={task_id} state={state}',
                flush=True,
            )

            if state == 'TASK_STATE_COMPLETED':
                receipt = receipt_data(task)

                if receipt is None:
                    return (
                        None,
                        'invalid_receipt',
                        'Seller completed without a numeric receipt.',
                    )

                price = receipt['price']

                if (
                    not isinstance(price, (int, float))
                    or price < 0
                    or price > budget
                ):
                    return (
                        None,
                        'over_budget',
                        f'Seller receipt price {price} '
                        f'is outside budget {budget}.',
                    )

                receipt_item = receipt.get('item')
                item_ok = (
                    isinstance(receipt_item, str)
                    and (receipt_item in ({item} | acceptable)
                         if acceptable is not None
                         else receipt_item.casefold() == item.casefold())
                )
                if not item_ok:
                    return (
                        None,
                        'invalid_receipt',
                        f'Seller receipt item '
                        f'{receipt.get("item")!r} '
                        f'mismatches {item!r}.',
                    )

                # A lower receipt price benefits the buyer.
                # Only a higher amount than the last offer
                # counts as a price mismatch.
                if (
                    last_offer is not None
                    and float(price) > last_offer + 0.00001
                ):
                    return (
                        receipt,
                        'receipt_mismatch',
                        (
                            f'Buyer offered {last_offer}; '
                            f'receipt says {price}, '
                            'which is higher than the buyer offer.'
                        ),
                    )

                return (
                    receipt,
                    'deal',
                    (
                        f'Successful purchase for {price} '
                        f'within budget {budget}.'
                    ),
                )

            if state in (
                'TASK_STATE_REJECTED',
                'TASK_STATE_CANCELED',
                'TASK_STATE_FAILED',
            ):
                return (
                    None,
                    'no_deal',
                    f'Negotiation finished with {state}.',
                )

            if state != 'TASK_STATE_INPUT_REQUIRED':
                return (
                    None,
                    'no_deal',
                    f'Unexpected A2A task state {state}.',
                )

            if turn > MAX_TURNS:
                return (
                    None,
                    'no_deal',
                    'Reached maximum negotiation turns.',
                )

            seller_text = status_message_text(task)
            ask = extract_last_price(seller_text)
            history.append(f'seller asked {ask}')

            # BUYER_HYBRID_NEGOTIATION_V2: one throttled LLM opportunity
            # per order; other turns use conservative rule-based negotiation.
            decision = _hybrid_buyer_decision(
                item=item,
                budget=budget,
                seller_price=ask,
                turn_number=turn,
                history=list(history),
                task_id=task_id,
            )

            print(
                '[MARKET BUYER HARNESS] '
                + json.dumps(decision),
                flush=True,
            )

            if str(decision.get('action')).lower() == 'walk':
                return (
                    None,
                    'no_deal',
                    'Buyer decided to walk away.',
                )

            if str(decision.get('action')).lower() != 'offer':
                return (
                    None,
                    'error',
                    f'Invalid model action: {decision!r}',
                )

            last_offer = max(
                0,
                min(budget, int(decision['price']))
            )

            history.append(
                f'buyer offered {last_offer}'
            )

            task = get_task(
                send_message(
                    client,
                    endpoint,
                    str(last_offer),
                    task_id=task_id,
                )
            )

            if str(task.get('id')) != task_id:
                return (
                    None,
                    'invalid_receipt',
                    'Seller changed task ID during negotiation.',
                )

    return None, 'no_deal', 'Negotiation ended.'


def make_review(
    result: str,
    detail: str,
    receipt: dict[str, Any] | None
) -> tuple[int, str]:

    if result == 'deal':
        price = (
            receipt.get('price')
            if receipt else 'unknown'
        )

        return (
            5,
            f'Completed A2A trade at price {price}; '
            'receipt agrees with negotiation.',
        )

    if result == 'receipt_mismatch':
        return 1, detail[:250]

    if result in (
        'over_budget',
        'invalid_receipt',
    ):
        return 1, detail[:250]

    if result == 'model_unavailable':
        return (
            3,
            'Buyer model unavailable; '
            'no seller fault established.',
        )

    if result == 'error':
        return (
            3,
            'Technical error during buyer execution; '
            'seller fault not established.',
        )

    if result == 'no_deal':
        return (
            3,
            'No transaction completed; '
            'no seller misconduct verified. '
            + detail[:160],
        )

    return (
        3,
        'No verified seller misconduct. '
        + detail[:160],
    )


def run_market_buyer(
    shops_path: str,
    item: str,
    budget: int,
    order_path: str | None = None,
) -> dict[str, Any]:

    shops = load_shops(shops_path)
    order = load_order(order_path)
    acceptable = set(str(x) for x in (order.get('acceptable') or []) if isinstance(x, str))
    policy = acceptance_policy(order)
    if acceptable:
        shops = [shop for shop in shops if stocked_offers(shop, acceptable)]
        if not shops:
            return {'shop': '', 'receipt': None, 'stars': 3,
                    'review': 'No shop has a permitted SKU in stock.'}
    memory = load_memory()

    shop, reason = choose_shop(
        shops,
        memory,
        item,
        budget,
        acceptable=acceptable if acceptable else None,
        required_hours=float(policy.get('min_hours_left', 0.0)),
    )

    receipt: dict[str, Any] | None = None
    result = 'error'
    detail = 'Unknown error.'

    offer = select_offer(shop, acceptable, budget) if acceptable else None
    if acceptable and not offer:
        return {'shop': '', 'receipt': None, 'stars': 3,
                'review': 'No acceptable stocked item at selected shop.'}
    try:
        receipt, result, detail = negotiate(
            shop,
            item,
            budget,
            acceptable=acceptable if acceptable else None,
            requested_offer=offer,
        )

    except Exception as exc:
        raw_error = str(exc)

        if _model_quota_error(exc):
            result = 'model_unavailable'
            detail = (
                'Buyer model rate limited; '
                'cannot evaluate this shop fairly.'
            )
        else:
            detail = (
                f'Failed to trade: '
                f'{type(exc).__name__}: {raw_error}'
            )[:250]

        print(
            f'[TRADE ERROR] {detail}',
            file=sys.stderr,
            flush=True,
        )

    stars, review = make_review(
        result,
        detail,
        receipt,
    )

    record = {
        'shop': shop['name'],
        'item': item,
        'budget': budget,
        'price': (
            receipt.get('price')
            if receipt else None
        ),
        'result': result,
        'stars': stars,
        'review': review,
        'picked_because': reason,
        'requested_sku': offer.get('sku') if offer else None,
        'order': order.get('order'),
        'timestamp_utc': int(time.time()),
    }

    try:
        save_memory(memory + [record])

    except OSError as exc:
        print(
            f'[MEMORY WARNING] '
            f'could not persist history: {exc}',
            file=sys.stderr,
            flush=True,
        )

    print(
        '[MEMORY] '
        + json.dumps(
            record,
            ensure_ascii=False,
        ),
        flush=True,
    )

    output = {
        'shop': shop['url'],
        'receipt': receipt,
        'stars': stars,
        'review': review,
    }
    if policy:
        output['accept'] = policy
    return output


def main() -> None:
    parser = argparse.ArgumentParser()

    parser.add_argument(
        '--shops',
        required=True,
    )

    parser.add_argument(
        '--item',
        required=True,
    )

    parser.add_argument(
        '--budget',
        required=True,
        type=int,
    )
    parser.add_argument('--order', default=None)

    args = parser.parse_args()

    if args.budget < 0:
        raise SystemExit(
            'Budget must be nonnegative'
        )

    # Kit consumes the final stdout JSON line.
    try:
        result = run_market_buyer(
            args.shops,
            args.item,
            args.budget,
            args.order,
        )

    except Exception as exc:
        print(
            f'[MARKET BUYER ERROR] '
            f'{type(exc).__name__}: {exc}',
            file=sys.stderr,
            flush=True,
        )

        result = {
            'shop': '',
            'receipt': None,
            'stars': 3,
            'review': (
                'Buyer failed before selecting a shop; '
                'no seller fault established.'
            ),
        }

    print(
        json.dumps(
            result,
            ensure_ascii=False,
        ),
        flush=True,
    )


if __name__ == '__main__':
    main()
