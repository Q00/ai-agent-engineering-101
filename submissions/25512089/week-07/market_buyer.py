"""Week 07 market buyer: reputation-aware shop choice, persistent memory, A2A trade.

Requires the original Week 06 buyer.py and harness.py in the same directory.
The final stdout line is always the JSON result expected by market_kit.py.
"""
from __future__ import annotations

import argparse
import json
import os
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
        shops.append({'name': name, 'url': url, 'reviews': reviews if isinstance(reviews, list) else []})
    if not shops:
        raise ValueError('No valid shops in shop list')
    return shops


def load_memory() -> list[dict[str, Any]]:
    if not MEMORY_PATH.exists():
        return []
    try:
        data = json.loads(MEMORY_PATH.read_text(encoding='utf-8'))
        return data[-MAX_MEMORY_ENTRIES:] if isinstance(data, list) else []
    except (OSError, ValueError, TypeError):
        print('[MEMORY] Could not read history; proceeding with empty memory', flush=True)
        return []


def save_memory(entries: list[dict[str, Any]]) -> None:
    MEMORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = MEMORY_PATH.with_name(f'{MEMORY_PATH.name}.{os.getpid()}.tmp')
    tmp.write_text(json.dumps(entries[-MAX_MEMORY_ENTRIES:], ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(tmp, MEMORY_PATH)


def reputation_summary(shop: dict[str, Any], memory: list[dict[str, Any]]) -> dict[str, Any]:
    name = shop['name']
    reviews = [r for r in shop['reviews'][:10] if isinstance(r, dict)]
    stars = [float(r['stars']) for r in reviews if isinstance(r.get('stars'), (int, float)) and not isinstance(r.get('stars'), bool) and 1 <= r['stars'] <= 5]
    low_stars = sum(s <= 2 for s in stars)
    own = [entry for entry in memory if entry.get('shop') == name]
    deals = sum(entry.get('result') == 'deal' for entry in own)
    # Only receipt integrity failures count as evidence of dishonest execution.
    # Local API limits (429) and network errors are NOT seller misconduct.
    incidents = sum(entry.get('result') in ('invalid_receipt', 'over_budget', 'receipt_mismatch') for entry in own)
    service_failures = sum(entry.get('result') in ('error', 'model_unavailable') for entry in own)
    no_deals = sum(entry.get('result') == 'no_deal' for entry in own)
    # Public reviews are useful, but can be forged until Round 2.
    score = (sum(stars) / len(stars) - 3.0) * 2.0 if stars else 0.0
    score -= low_stars * 5.0
    score += min(deals, 5) * 0.5
    score -= incidents * 7.0 + min(no_deals, 5) * 0.5
    return {
        'name': name,
        'review_count': len(reviews),
        'avg_stars': round(sum(stars) / len(stars), 2) if stars else None,
        'low_stars': low_stars,
        'review_samples': [str(r.get('review', ''))[:110] for r in reviews[:3]],
        'past_deals': deals,
        'past_no_deals': no_deals,
        'past_incidents': incidents,
        'past_service_failures': service_failures,
        'risk_score': round(score, 2),
    }


def choose_shop(
    shops: list[dict[str, Any]], memory: list[dict[str, Any]], item: str, budget: int
) -> tuple[dict[str, Any], str]:
    summaries = [reputation_summary(shop, memory) for shop in shops]
    for s in summaries:
        print('[REPUTATION] ' + json.dumps(s, ensure_ascii=False), flush=True)
    # Never prefer a shop with clearly bad reviews if a non-flagged alternative exists.
    # This guard makes the behavior reliable even if the model is inconsistent.
    safe = [s for s in summaries if s['low_stars'] == 0 and s['past_incidents'] == 0]
    eligible = safe if safe else summaries
    eligible.sort(key=lambda s: (-s['risk_score'], s['name']))
    eligible = eligible[:12]  # compact context: no URLs or unbounded raw reviews
    allowed = {s['name'] for s in eligible}
    best = eligible[0]

    system = (
        'You are the shop-selection decision maker for a buyer in an A2A market. '
        'Choose exactly one shop from the eligible shortlist. Evaluate public reputation '
        '(ratings and review texts) and this buyer\'s own stored trading experience. '
        'Avoid bad reviews, receipt inconsistencies, and repeated failures. '
        'Be cautious: reviews may be fake. Never invent a shop. '
        'Respond ONLY with JSON {"name":"EXACT SHOP NAME","reason":"SHORT EXPLANATION"}.'
    )
    prompt = json.dumps(
        {'item': item, 'budget': budget, 'eligible_shops': eligible}, ensure_ascii=False
    )
    try:
        selection = _call_json(system, prompt, label='market_shop_selection')
        chosen_name = selection.get('name')
        if chosen_name not in allowed:
            raise ValueError(f'Model chose unlisted/ineligible shop: {chosen_name!r}')
        reason = str(selection.get('reason', ''))[:240].strip()
        chosen = next(s for s in summaries if s['name'] == chosen_name)
        # Preserve strong risk preference even if model neglects ratings.
        if best['risk_score'] - chosen['risk_score'] >= 6:
            chosen_name = best['name']
            reason = f'Safety override: stronger reputation/own trade history (score {best["risk_score"]}).'
    except Exception as exc:
        chosen_name = best['name']
        reason = f'Fallback to highest reputation score due to {type(exc).__name__}.'
        print(f'[CHOOSE WARNING] {type(exc).__name__}: {exc}', file=sys.stderr, flush=True)

    shop = next(s for s in shops if s['name'] == chosen_name)
    print(f'[CHOOSE] name={shop["name"]!r} url={shop["url"]!r} reason={reason}', flush=True)
    return shop, reason


def receipt_data(task: dict[str, Any]) -> dict[str, Any] | None:
    for artifact in task.get('artifacts', []):
        if not isinstance(artifact, dict):
            continue
        for part in artifact.get('parts', []):
            if not isinstance(part, dict):
                continue
            data = part.get('data')
            if isinstance(data, dict) and isinstance(data.get('price'), (int, float)) and not isinstance(data['price'], bool):
                return data.copy()  # unchanged receipt for the kit's signature verification
    return None



def _model_quota_error(exc: Exception) -> bool:
    """Recognize a local LLM-quota outage, not a seller failure."""
    message = str(exc).lower()
    return any(fragment in message for fragment in (
        '429', 'cooldown', 'rate limit', 'rate-limit', 'quota',
    ))


def _fallback_buyer_turn(*, seller_price: int, budget: int) -> dict[str, Any]:
    """Make an offer without the model only when the model is rate-limited.

    The cheapest instantly acceptable price is the current seller ask if it
    fits the budget. Otherwise bid the maximum affordable amount and let the
    seller decide whether that price can be accepted. Repeated rounds are
    bounded by the existing MAX_TURNS and TIME_LIMIT_SECONDS guards.
    """
    offer = max(0, min(int(seller_price), int(budget)))
    return {'action': 'offer', 'price': offer}


def negotiate(shop: dict[str, Any], item: str, budget: int) -> tuple[dict[str, Any] | None, str, str]:
    start = time.monotonic()
    history: list[str] = []
    last_offer: int | None = None
    with httpx.Client(timeout=45.0) as client:
        card = fetch_agent_card(client, shop['url'])
        endpoint = get_jsonrpc_endpoint(card)
        task = get_task(send_message(client, endpoint, f'I want to buy a {item}'))
        task_id = str(task['id'])
        history.append(f'buyer requested {item}')

        for turn in range(1, MAX_TURNS + 2):
            if time.monotonic() - start > TIME_LIMIT_SECONDS:
                return None, 'no_deal', 'Negotiation timed out.'
            state = task_state(task)
            print(f'[MARKET BUYER] shop={shop["name"]} task={task_id} state={state}', flush=True)
            if state == 'TASK_STATE_COMPLETED':
                receipt = receipt_data(task)
                if receipt is None:
                    return None, 'invalid_receipt', 'Seller completed without a numeric receipt.'
                price = receipt['price']
                if not isinstance(price, (int, float)) or price < 0 or price > budget:
                    return None, 'over_budget', f'Seller receipt price {price} is outside budget {budget}.'
                if receipt.get('item') is not None and str(receipt.get('item')).lower() != item.lower():
                    return None, 'invalid_receipt', f'Seller receipt item {receipt.get("item")!r} mismatches {item!r}.'
                if last_offer is not None and abs(float(price) - last_offer) > 0.00001:
                    return receipt, 'receipt_mismatch', f'Agreed offer {last_offer}; receipt says {price}.'
                return receipt, 'deal', f'Successful purchase for {price} within budget {budget}.'
            if state in ('TASK_STATE_REJECTED', 'TASK_STATE_CANCELED', 'TASK_STATE_FAILED'):
                return None, 'no_deal', f'Negotiation finished with {state}.'
            if state != 'TASK_STATE_INPUT_REQUIRED':
                return None, 'no_deal', f'Unexpected A2A task state {state}.'
            if turn > MAX_TURNS:
                return None, 'no_deal', 'Reached maximum negotiation turns.'
            seller_text = status_message_text(task)
            ask = extract_last_price(seller_text)
            history.append(f'seller asked {ask}')
            try:
                decision = buyer_turn(
                    item=item, budget=budget, seller_price=ask, turn_number=turn, history=list(history)
                )
            except RuntimeError as exc:
                if not _model_quota_error(exc):
                    raise
                decision = _fallback_buyer_turn(seller_price=ask, budget=budget)
                print(
                    '[BUYER FALLBACK TURN] Groq rate-limited; '
                    f'task={task_id} seller_ask={ask} budget={budget} '
                    f'offer={decision["price"]}',
                    flush=True,
                )
            print('[MARKET BUYER HARNESS] ' + json.dumps(decision), flush=True)
            if str(decision.get('action')).lower() == 'walk':
                return None, 'no_deal', 'Buyer decided to walk away.'
            if str(decision.get('action')).lower() != 'offer':
                return None, 'error', f'Invalid model action: {decision!r}'
            last_offer = max(0, min(budget, int(decision['price'])))
            history.append(f'buyer offered {last_offer}')
            task = get_task(send_message(client, endpoint, str(last_offer), task_id=task_id))
            if str(task.get('id')) != task_id:
                return None, 'invalid_receipt', 'Seller changed task ID during negotiation.'
    return None, 'no_deal', 'Negotiation ended.'


def make_review(result: str, detail: str, receipt: dict[str, Any] | None) -> tuple[int, str]:
    if result == 'deal':
        price = receipt.get('price') if receipt else 'unknown'
        return 5, f'Completed A2A trade at price {price}; receipt agrees with negotiation.'
    if result == 'model_unavailable':
        return 3, 'Buyer model unavailable (own rate limit); no seller fault established.'
    if result == 'receipt_mismatch':
        return 1, detail[:250]
    if result in ('over_budget', 'invalid_receipt', 'error'):
        return 1, detail[:250]
    return 2, detail[:250]


def run_market_buyer(shops_path: str, item: str, budget: int) -> dict[str, Any]:
    shops = load_shops(shops_path)
    memory = load_memory()
    shop, reason = choose_shop(shops, memory, item, budget)
    receipt: dict[str, Any] | None = None
    result = 'error'
    detail = 'Unknown error.'
    try:
        receipt, result, detail = negotiate(shop, item, budget)
    except Exception as exc:
        raw_error = str(exc)
        # Buyer-owned model quota must never be mislabeled as a dishonest seller.
        if 'rate limit' in raw_error.lower() or '429' in raw_error or 'cooldown' in raw_error.lower():
            result = 'model_unavailable'
            detail = 'Buyer model rate limited; cannot evaluate this shop fairly.'
        else:
            detail = f'Failed to trade: {type(exc).__name__}: {exc}'[:250]
        print(f'[TRADE ERROR] {detail}', file=sys.stderr, flush=True)
    stars, review = make_review(result, detail, receipt)
    record = {
        'shop': shop['name'],  # stable identity even when shop URL changes
        'item': item,
        'budget': budget,
        'price': receipt.get('price') if receipt else None,
        'result': result,
        'stars': stars,
        'review': review,
        'picked_because': reason,
        'timestamp_utc': int(time.time()),
    }
    try:
        save_memory(memory + [record])
    except OSError as exc:
        print(f'[MEMORY WARNING] could not persist history: {exc}', file=sys.stderr, flush=True)
    print('[MEMORY] ' + json.dumps(record, ensure_ascii=False), flush=True)
    return {'shop': shop['url'], 'receipt': receipt, 'stars': stars, 'review': review}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--shops', required=True)
    parser.add_argument('--item', required=True)
    parser.add_argument('--budget', required=True, type=int)
    args = parser.parse_args()
    if args.budget < 0:
        raise SystemExit('Budget must be nonnegative')
    # The kit consumes the final stdout line. Do not print after it.
    try:
        result = run_market_buyer(args.shops, args.item, args.budget)
    except Exception as exc:
        print(f'[MARKET BUYER ERROR] {type(exc).__name__}: {exc}', file=sys.stderr, flush=True)
        result = {'shop': '', 'receipt': None, 'stars': 1, 'review': 'Buyer could not find a usable shop.'}
    print(json.dumps(result, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
