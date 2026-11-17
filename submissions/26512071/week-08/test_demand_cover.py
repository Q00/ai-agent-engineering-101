"""demand-cover-v1 (Claude Opus 5.5, 2026-11-14): one-day sizing is bounded by evidenced demand, not by room alone.
Live defect: qty = min(room // ub, PER_CALL) bought 79 cookies and 38 sesame-oil sets in 4 min (18:42-18:46 KST) while
the oil had 1 own sale in 12 h; the locked capital then left the 7/h cola one unit per window. Offline fixtures only."""
import json
import unittest

from test_oneday_pilot import ACT, NOW, Base, CATALOG, INV, setup, read, ctx
from test_microeconomics import led_with, u, sale

H = 3600
T = NOW+86400  # evaluation time


def facts_for(sales):
    """sales: sku -> list of hours-before-T of exact own pilot settles (supplied at NOW)."""
    import microeconomics
    units, events, seq = {}, {}, 100
    for sku, ago in sales.items():
        for h in ago:
            seq += 1
            units[f'u-{seq}'] = u(sku, 1000, 'food', gone=T-h*H)
            events.update(sale(f'u-{seq}', sku, 1500, seq, T-h*H))
    inv = {'balance':5634613, 'start':5000000, 'storage_per_day':.01, 'units':[]}
    return microeconomics.contributions(led_with(units, events, {}), inv, T, ACT)


def held(sku, n):
    return [{'unit':f'u-h-{sku}-{k}', 'sku':sku, 'cost':1000, 'kind':'food', 'grade':'A', 'supplied_at':T-H,
             'expires_at':T+30*86400} for k in range(n)]


def run(p, ranked, facts, units=(), pending_extra=(), room=10**7, table=()):
    import restock_r3
    inv = json.loads((p/'inventory.json').read_text(encoding='utf-8'))
    inv['units'] = list(units)
    pending = read(p, 'restock-pending.json')+list(pending_extra)
    c = dict(ctx(p, T), room=room, active=True, reason='active')
    obs = {'public_coverage':{k:None for k in ('sample', 'settle_count', 'fetched_at', 'stale', 'missing')}}
    micro = ([], ranked, list(table), facts, None)
    buy = restock_r3._oneday_plan(p, T, inv, CATALOG, {'own_sku_ids':{}}, pending, inv['units'], {}, c, dict(INV, at=T, invocation='inv-dc'),
                                  obs, {}, micro)['buy']
    dec = [json.loads(x) for x in (p/'restock-decisions.jsonl').read_text(encoding='utf-8').splitlines()][-1]
    return {b['sku']:b['qty'] for b in buy}, {s['sku']:s for s in dec['sizing']}, dec


def cand(sku, ub=1000):
    return (sku, ub, None, 1.0)


class DemandCover(Base):
    def setUp(self):
        super().setUp()
        setup(self.p)

    def test_slow_seller_with_stock_gets_no_greedy_lump(self):
        # live G63ef2f11: 1 own sale in 12 h, 44 on hand; old sizing requested 20 (PER_CALL) per window
        f = facts_for({'O':[5]})
        buy, sizing, _ = run(self.p, [cand('O', 40500)], f, units=held('O', 44))
        self.assertEqual(buy.get('O', 0), 0)
        self.assertEqual(sizing['O']['limit'], 'demand_cover')
        self.assertEqual(sizing['O']['demand']['on_hand'], 44)

    def test_fast_seller_sized_to_rate_times_cover(self):
        # 6 exact own sales in the last 3 h -> 2/h; 12 h window gives .5/h; the faster rate is used
        f = facts_for({'C':[.5, 1, 1.5, 2, 2.5, 2.9]})
        buy, sizing, _ = run(self.p, [cand('C', 16639)], f, units=held('C', 2))
        import restock_r3
        target = 2*restock_r3.COVER_H
        self.assertEqual(sizing['C']['demand']['rate_per_hour'], 2)
        self.assertEqual(sizing['C']['demand']['basis'], 'own_exact_settles')
        self.assertEqual(buy['C'], target-2)

    def test_unconfirmed_supply_counts_old_supplied_unmatched_rows_do_not(self):
        # rows with no supply result yet are units that may still arrive; 'supplied' rows older than the recency
        # window already arrived (on hand or sold) and counting them again would block the SKU forever (live: 74 cola)
        import restock_r3
        f = facts_for({'C':[.5, 1, 1.5, 2, 2.5, 2.9]})
        row = {'sku':'C', 'reserve':16639, 'category':'cC', 'matched_unit_id':None, 'baseline_unit_ids':[],
               'one_day_pilot':'one-day-v1'}
        legacy = {k:v for k, v in row.items() if k != 'one_day_pilot'}  # frozen UNKNOWN: money reserve, not a slot
        extra = [dict(row, at=T-60)]*3+[dict(row, at=T-7200, supply_result='supplied')]*50 \
            + [dict(row, at=T-60, supply_result='supplied')]*2+[dict(row, at=T-60, supply_result='refused')]*4 \
            + [dict(legacy, at=T-9*86400)]*2
        buy, sizing, _ = run(self.p, [cand('C', 16639)], f, units=held('C', 2), pending_extra=extra)
        self.assertEqual(sizing['C']['demand']['unconfirmed'], 5)
        self.assertEqual(buy['C'], 2*restock_r3.COVER_H-2-5)

    def test_no_evidence_probe_only_when_nothing_held(self):
        f = facts_for({})
        buy, sizing, _ = run(self.p, [cand('K', 25549)], f)
        self.assertEqual(buy['K'], 1)
        self.assertEqual(sizing['K']['demand']['basis'], 'no_rate_evidence_probe')
        buy, sizing, _ = run(self.p, [cand('K', 25549)], f, units=held('K', 1))
        self.assertEqual(buy.get('K', 0), 0)

    def test_qualified_public_goods_use_per_shop_rate_prior(self):
        import restock_r3
        f = facts_for({})
        table = [{'sku':'G', 'tier':'qualified_new_market_goods', 'buy_candidate':True,
                  'market':{'settlements':60, 'shops':10, 'buyers':20, 'window_s':7200,
                            'median_paid':14513, 'unit_upper_bound':14513}}]  # worstbound-market-v1: median >= UB
        buy, sizing, _ = run(self.p, [cand('G', 14513)], f, table=table)
        self.assertAlmostEqual(sizing['G']['demand']['rate_per_hour'], 3)
        self.assertEqual(sizing['G']['demand']['basis'], 'public_per_shop_share')
        self.assertEqual(buy['G'], min(3*restock_r3.COVER_H, restock_r3.PER_CALL))

    def test_high_demand_has_no_per_sku_cap_only_transport_guard(self):
        import restock_r3
        f = facts_for({'C':[k*.1 for k in range(1, 30)]})  # 29 sales in 3 h
        buy, sizing, _ = run(self.p, [cand('C', 16639)], f)
        self.assertEqual(buy['C'], restock_r3.PER_CALL)
        self.assertEqual(sizing['C']['limit'], 'per_call_transport_guard')
        self.assertIsNone(ctx(self.p, T)['sku_cap'])

    def test_room_flows_to_fast_seller_after_slow_one_is_covered(self):
        f = facts_for({'O':[5], 'C':[.5, 1, 1.5, 2, 2.5, 2.9]})
        buy, sizing, dec = run(self.p, [cand('O', 40500), cand('C', 16639)], f, units=held('O', 44)+held('C', 2),
                               room=200000)
        self.assertEqual(buy.get('O', 0), 0)
        self.assertEqual(buy['C'], min(10, 200000//16639))
        self.assertTrue(dec['sizing_policy'].startswith('demand-cover-v1'))


if __name__ == '__main__':
    unittest.main()
