"""Replay frozen public evidence; never send votes, posts, or trade requests."""
from collections import Counter
from datetime import datetime, timezone
import json
from r3.state import ROOT, read_json


def main():
    public_path = 'logs/review-public-source-20261106T051107345511Z.json'
    trace_path = 'logs/negative-review-trace-20261106T052050229133Z.json'
    public = read_json(ROOT / public_path)
    trace = read_json(ROOT / trace_path)
    board = json.loads(public['responses']['/api/board']['text'])
    forum = json.loads(public['responses']['/api/forum?limit=100']['text'])
    owners = {r['name']: (r.get('owner') or {}).get('login') for r in board['buyers']}
    names = {s.get('card', {}).get('url'): s['name'] for s in board['shops']}
    events = {}
    for response in trace['responses'].values():
        for event in json.loads(response['text']):
            events[event['seq']] = event
    for event in json.loads(public['responses']['/api/ledger?after=-500']['text']):
        events[event['seq']] = event
    settles = {e['data']['order']: e for e in events.values() if e['kind'] == 'settle'}
    catalog = read_json(ROOT / '.market/catalog.json', {})
    comparisons = []
    for event in sorted(events.values(), key=lambda e: e['seq']):
        d = event['data']
        if event['kind'] != 'result' or not d.get('verified') or owners.get(d.get('buyer')) != 'shaun0927':
            continue
        settled = settles.get(d['order'], {})
        s = settled.get('data', {})
        list_price = catalog.get(s.get('sku'), {}).get('list_price')
        price = d.get('receipt', {}).get('price')
        comparisons.append({
            'result_seq': event['seq'], 'settle_seq': settled.get('seq'),
            'buyer': d['buyer'], 'owner': owners[d['buyer']],
            'shop': names.get(d['shop'], d['shop']), 'stars': d['stars'],
            'price': price, 'list_price': list_price,
            'price_list_ratio': price / list_price if list_price and price else None,
            'grade': s.get('grade'), 'claim_honest': s.get('claim_honest'),
            'warranty_hours': s.get('warranty_hours'),
            'expiry_hours': (s['expires_at'] - settled['ts']) / 3600 if s.get('expires_at') else None,
            'review_delay_seconds': event['ts'] - settled['ts'] if settled else None,
            'review': d.get('review'),
        })
    own = [r for r in forum if r.get('owner') == 'seochanit']
    others = [r for r in forum if r.get('owner') != 'seochanit']
    result = {
        'at': datetime.now(timezone.utc).isoformat(),
        'sources': [public_path, trace_path, 'logs/review-detail-source-20261106T051229261673Z.json'],
        'forum': {'snapshot_at': public['at'], 'posts': len(forum),
                  'posts_with_dislikes': sum(r['dislikes'] > 0 for r in forum),
                  'other_posts': len(others),
                  'other_posts_with_dislikes': sum(r['dislikes'] > 0 for r in others),
                  'own_posts': [{k: r[k] for k in ['id', 'role', 'likes', 'dislikes', 'score']} for r in own],
                  'voter_identity_available': False},
        'shop': {'own_low_reviews': [r for r in comparisons if r['shop'] == 'seochanit' and r['stars'] <= 2],
                 'reviewer_owner': 'shaun0927',
                 'verified_comparison_count': len(comparisons),
                 'comparison_stars': dict(Counter(r['stars'] for r in comparisons)),
                 'comparisons': comparisons},
        'limits': [
            'Forum sample is the latest 100 posts at one frozen snapshot, not all Round 3.',
            'Comparison ledger windows were selected around two low reviews plus a recent window; not a random sample.',
            'Forum dislikes cannot be attributed to the identified transaction reviewer from these public sources.',
            'Verified settlement and honest grade do not prove buyer satisfaction or future product quality.',
            'Subsecond reviews precede product use; exact rating policy and intent are not publicly established.',
            'Catalog list prices are a local cache; authoritative paid amounts and grades come from public settle events.',
            'Only recent reviews are exposed; exact account-weighted score counterfactual cannot be reconstructed.',
        ],
    }
    path = ROOT / 'logs' / ('review-evidence-analysis-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ') + '.json')
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print('ANALYSIS', path)
    print(json.dumps({'forum': result['forum'], 'comparison_count': len(comparisons),
                      'stars': result['shop']['comparison_stars'],
                      'own_reviewers': [r['buyer'] for r in result['shop']['own_low_reviews']]}, ensure_ascii=True))


if __name__ == '__main__':
    main()
