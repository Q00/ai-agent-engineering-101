"""Required Challenge 2: directory-based, concurrent, budgeted A2A shopping.

Each purchase has its own A2A taskId and private negotiation history; the
result ledger is keyed by item so an item cannot be recorded twice.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import json
from pathlib import Path
from typing import Any

from a2a_client import read_card
from buyer import purchase
from model_harness import decide

PICK_SHOP_PROMPT = """You are a shopping-list manager. From the discovered A2A
Agent Cards, select one shop that advertises a skill relevant to the current
item. Return exactly {"action":"select","shop_id":"ID"}; the ID must
be one of the candidate shop IDs. Do not make up IDs. Use seller skills,
names and descriptions for your decision. You are selecting one shop for an
item, not duplicating the purchase.
"""


def shop_catalog(directory: list[dict[str, Any]]) -> list[dict[str, Any]]:
    found = []
    for shop in directory:
        try:
            card = read_card(shop['url'])
            info = {
                'id': shop['id'], 'url': shop['url'], 'name': card.get('name'),
                'description': card.get('description'),
                'skills': [{'name': s.get('name'), 'tags': s.get('tags'),
                            'description': s.get('description')} for s in card.get('skills', [])],
            }
            found.append(info)
            print(f'[DF] discovered {shop["id"]}: {info["name"]}', flush=True)
        except Exception as exc:
            print(f'[DF] unavailable {shop["id"]}: {type(exc).__name__}: {exc}', flush=True)
    return found


def select_shop(item: str, shops: list[dict[str, Any]]) -> dict[str, Any]:
    # First use the Agent Card's published skill tags as a directory index.
    # This is discovery/filtering rather than a predetermined shop assignment.
    # The model still chooses a concrete shop from the matching candidates.
    skill_tag = f'specialist-{item}'
    matches = [s for s in shops if any(
        skill_tag in (skill.get('tags') or []) for skill in s['skills']
    )]
    candidates = matches or shops
    print(f'[DF shortlist] item={item} skill={skill_tag} candidates={[s["id"] for s in candidates]}', flush=True)
    decision = decide(PICK_SHOP_PROMPT, {'item': item, 'candidates': candidates}, ('select',))
    match = next((s for s in candidates if s['id'] == decision.get('shop_id')), None)
    if match is None:
        raise ValueError(f'LLM selected unknown shop id {decision.get("shop_id")!r}')
    print(f'[DF decision] item={item} selected={match["id"]}', flush=True)
    return match


def shop(directory: list[dict[str, Any]], shopping_list: list[dict[str, Any]], total_budget: int) -> dict[str, Any]:
    shops = shop_catalog(directory)
    if len(shops) < 2:
        raise RuntimeError('fewer than 2 reachable shops')
    ledger: dict[str, dict[str, Any]] = {}
    states: dict[str, dict[str, Any]] = {}
    jobs = []
    allocated = sum(int(i['budget']) for i in shopping_list)
    if allocated > total_budget:
        raise ValueError(f'per-item allowances {allocated} exceed total {total_budget}')
    if len({x['item'] for x in shopping_list}) != len(shopping_list):
        raise ValueError('duplicate item in shopping list')

    # Each list item gets exactly one negotiation task. The harness chooses a
    # shop using the Agent Cards; threads execute independent A2A tasks.
    for entry in shopping_list:
        item = entry['item']
        selected = select_shop(item, shops)
        jobs.append((item, int(entry['budget']), selected))
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        futures = {
            pool.submit(purchase, chosen['url'], item, budget): (item, budget, chosen)
            for item, budget, chosen in jobs
        }
        for future in concurrent.futures.as_completed(futures):
            item, budget, chosen = futures[future]
            result = future.result()
            key = f'{chosen["id"]}::{item}'
            states[key] = {
                'shop': chosen['id'], 'item': item, 'taskId': result['taskId'],
                'history': result['history'], 'outcome': result['outcome'],
                'price': result['price'],
            }
            print(f'[STATE] {json.dumps(states[key], ensure_ascii=False)}', flush=True)
            if result['outcome'] == 'deal':
                if item in ledger:
                    raise RuntimeError(f'duplicate purchase of {item}')
                if int(result['price']) > budget:
                    raise RuntimeError(f'{item} price exceeds item budget')
                ledger[item] = {'shop': chosen['id'], 'price': int(result['price'])}
    spent = sum(x['price'] for x in ledger.values())
    distinct_shops = len({row['shop'] for row in ledger.values()})
    passed = (len(ledger) == len(shopping_list) and spent <= total_budget and distinct_shops >= 2)
    print(f'[LEDGER] {json.dumps(ledger, ensure_ascii=False)}', flush=True)
    print(f'[TOTAL] bought={len(ledger)}/{len(shopping_list)} spent={spent} budget={total_budget} distinct_shops={distinct_shops} passed={passed}', flush=True)
    return {'passed': passed, 'ledger': ledger, 'spent': spent, 'budget': total_budget, 'distinct_shops': distinct_shops, 'states': states}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--directory', required=True)
    ap.add_argument('--shopping-list', required=True)
    ap.add_argument('--total-budget', type=int, required=True)
    ap.add_argument('--result', default='logs/challenge-2/result.json')
    args = ap.parse_args()
    shops = json.loads(Path(args.directory).read_text(encoding='utf-8'))
    items = json.loads(Path(args.shopping_list).read_text(encoding='utf-8'))
    result = shop(shops, items, args.total_budget)
    out = Path(args.result)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    if not result['passed']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
