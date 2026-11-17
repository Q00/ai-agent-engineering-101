"""RED: bounded-v1 restock evidence. Offline fixtures only; plan() must never touch the network."""
import json
import socket
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

OWN = 'https://own.example'
OTHER = 'https://other.example'
START = 5000000


def settle(seq, shop, sku, amount=8000, round_=3, kind='settle'):
    # Observed public ledger shape: logs/opus-auth-20261104/gap-shop-exact.json (row.kind, data.amount).
    return {'seq':seq, 'kind':kind, 'round':round_, 'data':{'shop':shop, 'item':sku, 'amount':amount}}


def goods(cat, price=10000):
    return dict(name=f'Fixture {cat}', category=cat, kind='goods', list_price=price)


def write_state(p, catalog, inv=None, rows=None, own_urls=(OWN+'/',)):
    inv = inv or {'round':3, 'balance':START, 'start':START, 'units':[]}
    (p/'inventory.json').write_text(json.dumps(inv))
    (p/'catalog.json').write_text(json.dumps(catalog))
    if rows is not None:
        cache = {'source_url':'https://market.ourolabs.ai/api/ledger?after=-500', 'fetched_at':50,
                 'own_urls':list(own_urls), 'rows':rows}
        (p/'public-settlements.json').write_text(json.dumps(cache))


def offline_plan(p, now):
    import restock_r3
    boom = AssertionError('plan() opened a network connection')
    with patch.object(socket.socket, 'connect', side_effect=boom), \
         patch.object(socket, 'create_connection', side_effect=boom):
        return restock_r3.plan(p, now=now)


def read(p, name):
    return json.loads((p/name).read_text(encoding='utf-8'))


class PublicEvidenceTests(unittest.TestCase):
    ROWS = [settle(10, OTHER, 'A'), settle(11, OWN, 'B'), settle(12, OTHER, 'A', kind='order'),
            settle(13, OTHER, 'A', round_=2), settle(14, OTHER, 'A', amount=0), {'seq':15, 'kind':'settle', 'round':3},
            settle(16, OTHER, 'NOPE'), 'garbage', settle(18, OTHER, 'A'), settle(19, OTHER+'/', 'A', amount=-1),
            settle(20, OTHER, 'A')]

    def catalog(self):
        c = {f'F{i}':goods(f'fill{i}') for i in range(6)}
        c.update(A=goods('catA'), B=goods('catB'))
        return c

    def test_exact_sample_validation_and_own_sku_priority(self):
        import public_evidence
        got = public_evidence.ingest(self.ROWS, self.catalog(), [OWN+'/'])
        self.assertTrue(got['source_url'].endswith('/api/ledger?after=-500'))
        self.assertEqual(got['sample'], {'seq_min':10, 'seq_max':20})
        self.assertEqual(got['settle_count'], 5)  # 10, 11, 16, 18, 20
        self.assertEqual(got['unmapped'], 1)
        self.assertEqual(got['category_counts'], {'catA':3})  # own and unmapped excluded
        self.assertEqual(got['own_sku_counts'], {'B':1})
        self.assertEqual(got['own_ledger_ids'], [11])

        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            write_state(p, self.catalog(), rows=self.ROWS)
            buy = offline_plan(p, now=100)['buy']
            self.assertEqual([x['sku'] for x in buy[:2]], ['B', 'A'])
            pending = {r['sku']:r for r in read(p, 'restock-pending.json')}
            self.assertEqual(pending['B']['policy_version'], 'bounded-v2')
            self.assertEqual(pending['B']['evidence_ids'], [11])
            self.assertEqual(sorted(pending['A']['evidence_ids']), [10, 18, 20])


class PublicRankingTests(unittest.TestCase):
    def test_public_sample_changes_ranking_within_caps(self):
        import evidence_memory
        catalog = {f'G{i}':goods(f'cat{i}') for i in range(12)}
        catalog.update(GX=goods('catX', price=70000), Z1=goods('catZ'), Q1=goods('catQ'))
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            write_state(p, catalog)
            baseline = {x['sku'] for x in offline_plan(p, now=100)['buy']}
        target = next(s for s in sorted(catalog) if s.startswith('G') and s not in baseline)
        rows = [settle(100+i, OTHER, target) for i in range(5)] + [settle(200+i, OTHER, 'GX', 60000) for i in range(10)]
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            write_state(p, catalog, rows=rows)
            (p/'payments.jsonl').write_text(json.dumps({'order':'o9', 'shop':OTHER, 'item':'Z1', 'price':9000,
                                                        'paid':True, 'ledger':900})+'\n')
            evidence_memory.sync(p, order={'order':'oQ', 'category':'catQ'})
            buy = offline_plan(p, now=100)['buy']
            skus = [x['sku'] for x in buy]
            self.assertIn(target, skus)
            self.assertNotIn('GX', skus)  # 60k per-SKU cap beats public popularity
            self.assertLessEqual(len(buy), 3)
            self.assertTrue(all(x['qty'] == 1 for x in buy))
            self.assertEqual(len({catalog[s]['category'] for s in skus}), len(skus))
            self.assertLessEqual(sum(catalog[s]['list_price'] for s in skus), .05*START)

            obs = read(p, 'restock-observation.json')
            self.assertEqual(obs['policy_version'], 'bounded-v2')
            self.assertEqual(obs['public_coverage']['settle_count'], 15)
            self.assertEqual(obs['public_coverage']['sample'], {'seq_min':100, 'seq_max':209})
            self.assertEqual(obs['local_biased_counts'], {'catZ':1})  # requested catQ is not demand
            self.assertNotIn('catQ', obs['public_coverage']['category_counts'])
            self.assertEqual(obs['decision']['reason'], 'chosen')
            self.assertEqual(obs['decision']['chosen'], skus)
            pending = {r['sku']:r for r in read(p, 'restock-pending.json')}
            self.assertEqual(sorted(pending[target]['evidence_ids']), list(range(100, 105)))


class WindowTests(unittest.TestCase):
    def test_closed_window_no_reservations_or_buy(self):
        catalog = {f'G{i}':goods(f'cat{i}') for i in range(6)}
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            inv = {'round':3, 'balance':START, 'start':START, 'units':[],
                   'restock':{'open':False, 'opens_at':100+600, 'closes_at':100+780}}
            write_state(p, catalog, inv=inv)
            self.assertEqual(offline_plan(p, now=100), {'buy':[]})
            pending = p/'restock-pending.json'
            self.assertTrue(not pending.exists() or read(p, 'restock-pending.json') == [])
            obs = read(p, 'restock-observation.json')
            self.assertEqual(obs['decision']['reason'], 'window')
            self.assertEqual(obs['decision']['planned_opens_at'], 700)

            # Within 90 s the kit's own wait covers the gap, so buying is allowed.
            inv['restock'] = {'open':False, 'opens_at':100+60, 'closes_at':100+240}
            (p/'inventory.json').write_text(json.dumps(inv))
            self.assertTrue(offline_plan(p, now=100)['buy'])


if __name__ == '__main__': unittest.main()
