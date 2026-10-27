"""Read public market evidence; discount concentrated reviews without issuing accusations.

OPEN / L2 discussion: signatures do not establish independence between traders.
This bounded sample measures reciprocal concentration, not secret agreements.
It never posts a review, reports a player, requests an order, or calls settlement.
"""
import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import time

import httpx

ROOT = Path(__file__).resolve().parent
AGENT = ROOT.parent / 'week-07'
MARKET = 'https://market.ourolabs.ai'


def owner_maps(board, registry):
    buyers, shops, urls = {}, {}, {}
    for role, target in [('buyers', buyers), ('shops', shops)]:
        for row in board.get(role, []):
            login = (row.get('owner') or {}).get('login')
            if login and not row.get('house'):
                target[row['name']] = login.casefold()
                if role == 'shops' and isinstance(row.get('card'), dict):
                    url = row['card'].get('url')
                    if url:
                        urls[url.rstrip('/')] = (row['name'], login.casefold())
    for row in registry.get('shops', []):
        if row['name'] in shops:
            urls[row['url'].rstrip('/')] = (row['name'], shops[row['name']])
    return buyers, shops, urls


def analyze(events, board, registry, *, minimum=20, share=0.8):
    buyers, shops, urls = owner_maps(board, registry)
    # One verified result per order. Keep unknown sellers in the buyer denominator.
    seen, verified, duplicates, excluded_rounds = set(), [], 0, 0
    rounds = Counter()
    for event in events:
        data = event.get('data') or {}
        if event.get('kind') != 'result' or data.get('verified') is not True:
            continue
        if event.get('round') != board.get('round'):
            excluded_rounds += 1
            continue
        order = data.get('order')
        if not order:
            continue
        if order in seen:
            duplicates += 1
            continue
        seen.add(order)
        verified.append((event, data))
        rounds[str(event.get('round'))] += 1
    by_buyer, edges, evidence, stars = Counter(), Counter(), defaultdict(list), defaultdict(list)
    unknown_buyers = unknown_sellers = house_or_same_owner = 0
    for event, data in verified:
        buyer = buyers.get(data.get('buyer'))
        if not buyer:
            unknown_buyers += 1
            continue
        by_buyer[buyer] += 1
        seller = urls.get(str(data.get('shop', '')).rstrip('/'))
        if not seller:
            unknown_sellers += 1
            continue
        name, owner = seller
        if owner == buyer:
            house_or_same_owner += 1
            continue
        edge = (buyer, owner)
        edges[edge] += 1
        evidence[edge].append(event['seq'])
        rating = data.get('stars')
        if type(rating) is int and 1 <= rating <= 5:
            stars[edge].append(rating)
    pairs, visited, cautions = [], set(), set()
    for (a, b), count in edges.items():
        pair = tuple(sorted((a, b)))
        if pair in visited:
            continue
        visited.add(pair)
        reverse = edges[(b, a)]
        if by_buyer[a] < minimum or by_buyer[b] < minimum:
            continue
        ab, ba = count / by_buyer[a], reverse / by_buyer[b]
        if ab < share or ba < share:
            continue
        row = {'owners': [a, b], 'a_to_b': count, 'a_verified_results': by_buyer[a],
               'a_to_b_share': ab, 'b_to_a': reverse, 'b_verified_results': by_buyer[b],
               'b_to_a_share': ba, 'a_to_b_mean_stars': sum(stars[(a,b)])/len(stars[(a,b)]) if stars[(a,b)] else None,
               'b_to_a_mean_stars': sum(stars[(b,a)])/len(stars[(b,a)]) if stars[(b,a)] else None,
               'example_ledger_sequences': evidence[(a,b)][:3] + evidence[(b,a)][:3],
               'interpretation': 'Reciprocal concentration in this sample; not proof of collusion.'}
        pairs.append(row)
        cautions.update(name for name, owner in shops.items() if owner in {a,b})
    return {'verified_results': len(verified), 'duplicate_results_excluded': duplicates,
            'verified_results_from_other_rounds_excluded': excluded_rounds,
            'verified_results_by_round': dict(rounds), 'unmapped_buyer_results': unknown_buyers,
            'unmapped_seller_results_for_known_buyers': unknown_sellers,
            'same_owner_results_excluded_from_pairs': house_or_same_owner,
            'thresholds': {'minimum_verified_results_per_buyer': minimum, 'reciprocal_share': share},
            'pairs': sorted(pairs, key=lambda x: -(x['a_to_b']+x['b_to_a'])),
            'review_caution_shops': sorted(cautions)}


def fetch(client, path, output, **params):
    response = client.get(MARKET + path, params=params)
    response.raise_for_status()
    data = response.json()
    output.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return data


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--events', type=int, default=6000)
    parser.add_argument('--round', type=int, default=2)
    parser.add_argument('--minimum', type=int, default=20)
    parser.add_argument('--share', type=float, default=0.8)
    args = parser.parse_args()
    if not 1 <= args.events <= 10000 or args.minimum < 20 or not 0.5 <= args.share <= 1:
        parser.error('Use 1..10000 events, minimum >=20 and share 0.5..1')
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    folder = ROOT / 'logs' / ('public-market-' + stamp)
    folder.mkdir(parents=True)
    with httpx.Client(timeout=httpx.Timeout(20, connect=10), follow_redirects=False) as client:
        registry = fetch(client, '/api/shops', folder/'registry.json')
        # Freeze the end sequence so a busy market does not create a moving denominator.
        tail = fetch(client, '/api/ledger', folder/'ledger-tail.json', after=-1)
        end = max(row['seq'] for row in tail)
        start, cursor = max(0, end - args.events), max(0, end - args.events)
        events = []
        for page in range((args.events + 499)//500 + 2):
            rows = fetch(client, '/api/ledger', folder/f'ledger-{page:02}.json', after=cursor)
            rows = [row for row in rows if cursor < row['seq'] <= end]
            if not rows:
                break
            events.extend(rows)
            cursor = max(row['seq'] for row in rows)
            if cursor >= end:
                break
            time.sleep(0.15)
        board = fetch(client, '/api/board', folder/'board.json', round=args.round)
    seqs = [row['seq'] for row in events]
    complete = len(set(seqs)) == end-start and min(seqs,default=0) == start+1 and max(seqs,default=0) == end
    summary = analyze(events, board, registry, minimum=args.minimum, share=args.share)
    summary.update({'snapshot_utc': stamp, 'market_current_round': registry['round'],
                    'board_round': args.round, 'requested_seq_after': start, 'snapshot_end_seq': end,
                    'events_read': len(events), 'sequence_window_complete': complete,
                    'sources': [MARKET+'/api/shops', MARKET+f'/api/board?round={args.round}',
                                MARKET+f'/api/ledger?after={start}'],
                    'limitations': ['Recent bounded window, not the entire market history.',
                                    'Only registry/board URL mappings identify sellers; unmapped sellers remain unknown.',
                                    'Preferential repeat buying can be legitimate; this does not prove coordination.',
                                    'No claim that these heuristic thresholds are optimal or that all collusion is detectable.']})
    (folder/'analysis.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    # Advisory data may only reduce reliance on public reviews; never fabricate negative ratings.
    state = {'schema': 1, 'advisory_only': True, 'generated_epoch': time.time(),
             'source_round': args.round, 'max_age_seconds': 7200,
             'shops': summary['review_caution_shops'] if complete else [],
             'evidence': str((folder/'analysis.json').relative_to(AGENT.parent))}
    target = AGENT/'.market'/'review-cautions.json'
    temporary = target.with_suffix('.tmp')
    temporary.write_text(json.dumps(state,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    temporary.replace(target)
    print(json.dumps({k:summary[k] for k in ['market_current_round','events_read','sequence_window_complete',
                                           'verified_results','unmapped_buyer_results',
                                           'unmapped_seller_results_for_known_buyers','pairs','review_caution_shops']},
                     ensure_ascii=True,indent=2))
    print('Evidence:',folder.relative_to(ROOT))


if __name__ == '__main__':
    main()
