"""Offline peer-review style comparison from previously saved verified trades.

Text-feature counts describe wording, not honesty or causal referral effects.
The 1,000 results include repeat templates from the same 25 buyers.
"""
from collections import Counter
from datetime import datetime, timezone
import gzip
import json
from pathlib import Path
import re

HERE = Path(__file__).resolve().parent
SAVED = HERE.parent / 'review-ad-effect-20261021T141746Z'
rows = []
for path in (SAVED / 'ledger-pages').glob('*.gz'):
    rows.extend(json.loads(gzip.decompress(path.read_bytes())))
rows.extend(json.loads((SAVED / 'ledger-terminal-check.json').read_text()))
rows.sort(key=lambda row: row['seq'])
assert len({row['seq'] for row in rows}) == len(rows)
peers = [row for row in rows if row['round'] == 2 and row['kind'] == 'result'
         and row['data'].get('verified') is True and row['data'].get('buyer') != 'myeonggi'
         and row['data'].get('review')][-1000:]
assert len(peers) == 1000
patterns = {'receipt_or_signature_mentioned': r'\b(?:receipt|signed|signature)\b',
            'budget_mentioned': r'\bbudget\b',
            'component_scores': r'\b(?:price|speed|receipt|conduct)\s+\d(?:\.\d+)?/5',
            'explicit_self_shop_call_to_action': r'\btry the store [\x27\"]Cheapest in Town'}
features = {key: sum(bool(re.search(pattern, row['data']['review'], re.I)) for row in peers)
            for key, pattern in patterns.items()}
authors = Counter(row['data']['buyer'] for row in peers)
examples = {}
for name in ('fair-furniture', 'kcwarni', 'move_76', 'olstjr', 'cheapest-in-town'):
    selected = [row for row in peers if row['data']['buyer'] == name]
    examples[name] = {'verified_reviews': len(selected),
                      'examples': [{'seq': row['seq'], 'review': row['data']['review']}
                                   for row in selected[:2]]}
shops = json.loads((HERE / 'shops.json').read_text())['shops']
input_reviews = [row for shop in shops for row in shop.get('reviews', [])]
summary = {'sample': {'verified_peer_reviews': len(peers), 'distinct_buyers': len(authors),
                      'first_seq': peers[0]['seq'], 'last_seq': peers[-1]['seq'],
                      'first_utc': datetime.fromtimestamp(peers[0]['ts'], timezone.utc).isoformat(),
                      'last_utc': datetime.fromtimestamp(peers[-1]['ts'], timezone.utc).isoformat()},
           'text_features_nonexclusive': features, 'author_counts': dict(authors),
           'examples_by_author': examples,
           'live_buyer_input': {'shops': len(shops), 'reviews': len(input_reviews),
                                'observed_review_fields': sorted({key for row in input_reviews for key in row}),
                                'reviews_with_author_field': sum('author' in row for row in input_reviews)},
           'recommendation': 'Use one neutral English line with actual item, paid price, budget and observed receipt outcome; no advertising, invented product experience or arbitrary component scores.',
           'limits': ['Style is not randomized; this evidence does not establish that a wording style drives visits to the reviewer\'s own shop.',
                      'Repeated templates are not independent opinions or unique readers.',
                      'Default kit input passes reviews of the reviewed shop; the observed review objects omit author identity.',
                      'The previously saved ledger sample ends earlier than the live profile snapshot.']}
(HERE / 'peer-review-style-summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2))
print(json.dumps({key: value for key, value in summary.items() if key != 'examples_by_author'}, ensure_ascii=False, indent=2))
