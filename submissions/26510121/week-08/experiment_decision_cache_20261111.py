"""Offline experiment: reuse event reads within one frozen decision only.

No production patch or network/model/payment action. This is limited evidence
about redundant computation, not a release-quality cache implementation.
"""
from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import json
import sqlite3
import statistics
import time

from profile_main_latency_20261111 import ROOT, ShadowJournal
from r3.economy import buyer_candidates, shop_candidates
from r3 import market_memory, contracts, long_memory, buyer_quality


class CachedShadowJournal(ShadowJournal):
    def __init__(self, *args):
        super().__init__(*args)
        self.cache = {}

    def events(self, kind, limit=500):
        key = (kind, limit)
        if key not in self.cache:
            self.cache[key] = super().events(kind, limit)
        else:
            self.calls['cache_hit:' + kind] += 1
        return [dict(row) for row in self.cache[key]]


def clear_caches():
    market_memory._BOOK_CACHE.clear()
    contracts._HISTORY.clear()
    contracts._IDENTITIES.clear()
    long_memory._FACT_CACHE.clear()
    buyer_quality._CACHE.clear()


def main():
    cutoff = time.time()
    files = {name: json.loads((ROOT / '.market' / name).read_text(encoding='utf-8-sig'))
             for name in ('catalog.json', 'board.json', 'inventory.json', 'registry.json',
                          'order.json', 'shops.json', 'market_stock_counts.json')}
    cfg = json.loads((ROOT / 'policy.json').read_text(encoding='utf-8-sig'))
    cases = {}
    for kind in ('food', 'goods'):
        unit = next((u for u in files['inventory.json'].get('units', []) if u.get('kind') == kind), None)
        if unit:
            cases['shop_' + kind] = lambda j, unit=unit: shop_candidates(
                unit, files['inventory.json'], files['catalog.json'], j, cfg, now=cutoff)
    order_at = (ROOT / '.market/order.json').stat().st_mtime
    shops_at = (ROOT / '.market/shops.json').stat().st_mtime
    if shops_at >= order_at:
        cases['buyer_' + files['order.json'].get('kind', 'unknown')] = lambda j: buyer_candidates(
            files['order.json'], files['shops.json'], files['catalog.json'], j, cfg)
    rows = defaultdict(list)
    with sqlite3.connect((ROOT / '.market/r3.sqlite3').as_uri() + '?mode=ro', uri=True) as db:
        db.execute('BEGIN')
        original_time = time.time
        try:
            time.time = lambda: cutoff
            for name, function in cases.items():
                for repeat in range(3):
                    variants = [('baseline', ShadowJournal), ('decision_cache', CachedShadowJournal)]
                    if repeat % 2:
                        variants.reverse()
                    for variant, cls in variants:
                        clear_caches()
                        journal = cls(db, cutoff, files)
                        started = time.perf_counter()
                        output = function(journal)
                        seconds = time.perf_counter() - started
                        signature = hashlib.sha256(json.dumps(output, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
                        rows[name].append({'repeat': repeat, 'variant': variant, 'seconds': seconds,
                            'output_rows': len(output), 'full_output_sha256': signature,
                            'decoded_rows': sum(journal.rows.values())})
        finally:
            time.time = original_time
    summary = {}
    for name, runs in rows.items():
        baseline = [r['seconds'] for r in runs if r['variant'] == 'baseline']
        cached = [r['seconds'] for r in runs if r['variant'] == 'decision_cache']
        summary[name] = {'baseline_median_seconds': statistics.median(baseline),
            'cache_median_seconds': statistics.median(cached),
            'full_output_identical_across_runs': len({r['full_output_sha256'] for r in runs}) == 1,
            'baseline_decoded_rows': next(r['decoded_rows'] for r in runs if r['variant'] == 'baseline'),
            'cache_decoded_rows': next(r['decoded_rows'] for r in runs if r['variant'] == 'decision_cache')}
    result = {'at': datetime.now(timezone.utc).isoformat(), 'cutoff': cutoff,
        'summary': summary, 'runs': dict(rows), 'deployed': False,
        'limits': ['Three paired repetitions of one input per case, in one local process.',
            'No concurrency, real writes, full API latency or economic outcomes tested.',
            'Cache only covers a frozen decision; cross-decision freshness/invalidation is untested.',
            'Output identity in these samples is not full regression or a deployment approval.']}
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    path = ROOT / 'logs' / ('decision-cache-experiment-' + stamp + '.json')
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print('SOURCE', path)
    print(json.dumps(summary, indent=2))


if __name__ == '__main__':
    main()
