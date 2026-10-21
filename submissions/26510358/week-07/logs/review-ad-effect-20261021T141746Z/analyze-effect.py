"""Offline, reproducible ad-effect audit; no application or market mutations.

The baseline already contains some advertising. Board deltas are descriptive,
not an A/B test. Published reviews are not measured impressions or readers.
"""
from collections import Counter
from datetime import datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
LOGS = HERE.parent
read = lambda path: json.loads(path.read_text())
manifest = read(HERE / 'ledger-export-manifest.json')
cutoff = datetime.fromisoformat(manifest['analysis_cutoff_utc']).timestamp()
rows = []
for page in manifest['pages']:
    raw = gzip.decompress((HERE / 'ledger-pages' / page['file']).read_bytes())
    assert hashlib.sha256(raw).hexdigest() == page['sha256_response_bytes']
    rows.extend(json.loads(raw))
# The first collection reached an empty live page shortly before the cutoff.
# A later read recovered the remaining rows; retain both original attempts.
continuation = read(HERE / 'ledger-terminal-check.json')
assert continuation and continuation[0]['seq'] == rows[-1]['seq'] + 1
rows.extend(continuation)
assert [row['seq'] for row in rows] == list(range(rows[0]['seq'], rows[-1]['seq'] + 1))
assert max(row['ts'] for row in rows) >= cutoff
completion = {'passed': True, 'initial_collector_failed_on_empty_live_page': True,
              'continuation_file': 'ledger-terminal-check.json', 'cutoff_covered': True,
              'first_seq': rows[0]['seq'], 'last_fetched_seq': rows[-1]['seq'],
              'rows_fetched': len(rows), 'analysis_cutoff_utc': manifest['analysis_cutoff_utc']}
(HERE / 'ledger-export-completed.json').write_text(json.dumps(completion, indent=2))
rows = [row for row in rows if row['ts'] <= cutoff]
index = read(HERE / 'board-snapshots-index.json')


def snapshot(filename):
    entry = next(row for row in index if row['file'] == filename)
    board = read(LOGS / filename)
    return entry, board


baseline, baseline_board = snapshot('buyer-access-20261021T105709Z/board-before.json')
start, start_board = snapshot('all-review-ads-20261021T113655Z/board-before.json')
end, end_board = snapshot(HERE.name + '/board.json')
start_ts = datetime.fromisoformat(start['mtime']).timestamp()
own_urls = {'myeonggi'}
for entry in index:
    board = read(LOGS / entry['file'])
    for shop in board['shops']:
        if shop['name'] == 'myeonggi' and shop.get('card', {}).get('url'):
            own_urls.add(shop['card']['url'].rstrip('/'))


def is_inbound(row):
    data = row['data']
    return (str(data.get('shop', '')).rstrip('/') in own_urls or
            (data.get('receipt') or {}).get('shop') == 'myeonggi')


results = [row for row in rows if row['kind'] == 'result' and row['round'] == 2]
verified = [row for row in results if row['data'].get('verified') is True]
ads = [row for row in verified if row['data'].get('buyer') == 'myeonggi'
       and 'Ad (my shop)' in row['data'].get('review', '')]
expanded_ads = [row for row in ads if row['ts'] > start_ts]
incoming = [row for row in verified if row['ts'] > start_ts and is_inbound(row)]
incoming_all = [row for row in results if row['ts'] > start_ts and is_inbound(row)]
assert len(incoming) == end['deals'] - start['deals'], 'Ledger and board deal deltas disagree'
assert len(incoming_all) == end['visits'] - start['visits'], 'Ledger and board visit deltas disagree'
assert len({row['data']['order'] for row in expanded_ads}) == len(expanded_ads)


def format_time(timestamp):
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat()


def ad_version(row):
    review = row['data']['review']
    if 'Hey there' in review:
        return 'long_humorous'
    if 'Ask for a current quote; exact-price' in review:
        return 'short_price_notice'
    return 'original_referral_notice'


versions = {}
for version in sorted({ad_version(row) for row in ads}):
    selected = [row for row in ads if ad_version(row) == version]
    versions[version] = {'publications': len(selected), 'first_utc': format_time(selected[0]['ts']),
                         'last_utc': format_time(selected[-1]['ts'])}
short = [row for row in ads if ad_version(row) == 'short_price_notice']
short_start = min(row['ts'] for row in short)
short_incoming = [row for row in incoming if row['ts'] >= short_start]

# Validate every apparent referral claim against the preserved operator wire.
claims = read(HERE / 'referral-claim-events.json')
wire = [json.loads(line) for line in (LOGS / 'buyer-access-20261021T105709Z/referral-public-wire.jsonl').read_text().splitlines()]
canceled_probe_ids = {row['request']['params']['id'] for row in wire if row['request']['method'] == 'CancelTask'}
claim_ids = {row['task_id'] for row in claims}
assert claim_ids <= canceled_probe_ids
assert not claim_ids.intersection((row['data'].get('receipt') or {}).get('task') for row in verified)

comparison = []
for name in ('myeonggi', 'lee-sungsoo', '11im'):
    a = next(row for row in start_board['shops'] if row['name'] == name)
    b = next(row for row in end_board['shops'] if row['name'] == name)
    comparison.append({'shop': name, 'visits_before': a['visits'], 'visits_after': b['visits'],
                        'added_visits': b['visits'] - a['visits'], 'deals_before': a['deals'],
                        'deals_after': b['deals'], 'added_deals': b['deals'] - a['deals']})
summary = {'analysis_cutoff_utc': manifest['analysis_cutoff_utc'],
           'expanded_ads_since_utc': start['mtime'], 'minutes': (cutoff - start_ts)/60,
           'advertising_publications': len(expanded_ads),
           'ad_destination_counts': dict(Counter((row['data'].get('receipt') or {}).get('shop') for row in expanded_ads)),
           'ad_versions_in_export': versions,
           'incoming_verified_deals': len(incoming), 'incoming_results': len(incoming_all),
           'incoming_verified_buyers': dict(Counter(row['data']['buyer'] for row in incoming)),
           'incoming_all_buyers': dict(Counter(row['data']['buyer'] for row in incoming_all)),
           'incoming_verified_stars': dict(Counter(row['data']['stars'] for row in incoming)),
           'referral_claim_tasks': len(claim_ids), 'operator_quote_cancel_claims': len(claim_ids & canceled_probe_ids),
           'nonoperator_referral_claims_observed': len(claim_ids - canceled_probe_ids),
           'short_ad_observation_minutes': (cutoff - short_start)/60,
           'short_ad_verified_incoming_deals': len(short_incoming),
           'shop_comparison': comparison,
           'own_rank_before': start['rank'], 'own_rank_after': end['rank'],
           'caveats': ['Published advertisements are not measured impressions or unique readers.',
                       'The pre-expansion baseline already contains advertising and is not an untreated control.',
                       'Prices, public URLs, service reliability, shop description and buyer restrictions also changed.',
                       'Anonymous callers cannot be linked to ledger buyer names or attributed to a review read.',
                       'REVIEW10 was not available in the acquisition phase; absence of code use cannot rule out ad influence.',
                       'The short advertisement has too little observation time for a reliable effect estimate.']}
(HERE / 'effect-summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2))
evidence = [{'seq': row['seq'], 'ts': row['ts'], 'buyer': row['data']['buyer'],
             'item': row['data']['receipt']['item'], 'price': row['data']['receipt']['price'],
             'stars': row['data']['stars'], 'review': row['data']['review']} for row in incoming]
(HERE / 'incoming-verified-evidence.json').write_text(json.dumps(evidence, ensure_ascii=False, indent=2))
print(json.dumps(summary, ensure_ascii=False, indent=2))
