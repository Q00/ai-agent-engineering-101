import sys
import time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from r3.state import Journal, policy, atomic_json
from r3.economy import cash_capacity


def test_only_confirmed_matching_full_refund_releases_gross_reserve(tmp_path):
    j, now = Journal(tmp_path), time.time()
    for unit in ('closed', 'partial', 'other-sku', 'future', 'pending'):
        j.add('shop_settled', {'unit': unit, 'sku': 'G1', 'price': 100,
                              'paid_at': now - 100, 'warranty_hours': 6}, ts=now - 100)
    for unit, sku, refund, ts in [('closed', 'G1', 100, now - 10),
                                   ('closed', 'G1', 100, now - 9),
                                   ('partial', 'G1', 99, now - 10),
                                   ('other-sku', 'G2', 100, now - 10),
                                   ('future', 'G1', 100, now + 10)]:
        j.add('public_broken', {'unit': unit, 'item': sku, 'refunded': refund}, ts=ts)
    result = cash_capacity({'balance': 1000000, 'units': []}, j, policy(), now)
    assert result['refund_liability'] == 400
    assert result['discharged_refund_reserve'] == 100
    assert len(j.events('public_broken')) == 5  # Financial evidence stays immutable.


def test_matured_grade_risk_excludes_ambiguous_and_immature_purchases(tmp_path):
    from r3.risk import quality_book, goods_risk
    j, now = Journal(tmp_path), time.time()
    for n in range(100):
        j.add('purchase', {'order': str(n), 'unit': str(n), 'kind': 'goods', 'grade': 'A',
                           'paid_at': now - 22000})
        j.add('outcome', {'order': str(n), 'broken': n < 2, 'matured': True})
    j.add('purchase', {'order': 'young', 'kind': 'goods', 'grade': 'A', 'paid_at': now - 1})
    j.add('purchase', {'order': 'ambiguous', 'kind': 'goods', 'grade': 'A', 'paid_at': now - 22000})
    j.add('outcome', {'order': 'ambiguous', 'matured': True})
    j.add('ambiguous_break', {'orders': ['ambiguous']})
    row = quality_book(j, now)['A']
    assert row == {'n': 100, 'broken': 2, 'within3': 0}
    assessment = goods_risk(j, 'A', 6, policy(), now)
    assert .02 < assessment['failure_upper'] < .15
    assert assessment['covered_lower'] == assessment['failure_upper']


def test_partial_warranty_is_a_checkout_requirement_with_conservative_value(tmp_path):
    from r3.market_memory import buyer_assessment, buyer_price_actions
    j, now = Journal(tmp_path), time.time()
    j.put('quality_risk_v4', {'at': now, 'grades': {'A': {'n': 100, 'broken': 10, 'within3': 7}}})
    p = {'kind': 'goods', 'list_price': 10000}
    a = buyer_assessment(p, {'budget': 10000}, {'grade': 'A', 'warranty_hours': 3}, j, policy())
    assert a['required_warranty'] == 3
    assert 0 < a['covered_failure'] < a['failure'] < .3
    assert 7000 < a['cap'] < 10000
    move = buyer_price_actions(a['cap'], a['cap'], p, 8000, .92, 3, 4,
                              failure_stress=a['failure'], covered_failure=a['covered_failure'])[0]
    assert 0 <= move['expected_delta']['surplus'] < 2


def test_zero_safe_value_cannot_become_a_one_credit_counteroffer():
    from r3.market_memory import buyer_price_actions
    assert buyer_price_actions(100, 0, {'list_price': 100}, 80, .92, 0, 4,
                               failure_stress=1, covered_failure=0) == []


def test_public_price_above_budget_does_not_become_fake_affordable_offer(tmp_path):
    from r3.economy import buyer_candidates
    j, now = Journal(tmp_path), time.time()
    cat = {'G1': {'kind': 'goods', 'list_price': 10000}}
    j.add('public_settle', {'sku': 'G1', 'amount': 9000}, ts=now)
    candidate = buyer_candidates({'acceptable': ['G1'], 'budget': 5000},
        [{'name': 'peer', 'url': 'url', 'offers': [{'sku': 'G1', 'qty': 1}]}], cat, j)[0]
    assert candidate['reference_price'] == 9000
    assert candidate['evidence']['budget_feasibility_scenario'] == .1


def test_core_capital_can_buy_hot_expensive_sku_without_breaching_gross_reserves(tmp_path):
    from r3.economy import restock_candidates
    cfg=dict(policy(),shop_internal_capital_caps_enabled=True,shop_reputation_v24_enabled=False)
    j, now = Journal(tmp_path), time.time()
    cat = {'G1': {'kind': 'goods', 'category': 'sofa', 'list_price': 2320000}}
    for n in range(80):
        j.add('public_settle', {'sku': 'G1', 'amount': 1900000, 'shop': 'peer'}, ts=now-n)
    inv = {'balance': 6250000, 'units': []}
    j.add('shop_settled', {'price': 200000, 'paid_at': now, 'warranty_hours': 6})
    cs = restock_candidates(inv, cat, [], j, cfg, now)
    assert cs == []  # The maximum surge draw cannot fit this SKU's capital cap.
    inv['balance']=9000000
    cs = restock_candidates(inv, cat, [], j, cfg, now)
    assert cs == []  # v7 also caps concentration against deployable working capital.
    inv['balance']=11000000
    cs = restock_candidates(inv, cat, [], j, cfg, now)
    assert len(cs) == 1 and cs[0]['sku'] == 'G1'
    capacity = cash_capacity(inv, j, cfg, now)
    assert sum(c['capital_commitment'] for c in cs) <= capacity['spendable']
    assert cs[0]['capital_commitment'] <= .35 * inv['balance']
    assert cs[0]['capital_commitment'] <= .35 * capacity['spendable']
    assert cs[0]['allocation_book'] == 'capital_velocity'


def test_seller_never_reduces_explicit_or_promised_warranty(tmp_path):
    from r3.economy import shop_candidates
    from test_market_contract import unit
    j = Journal(tmp_path)
    for minimum, promised, expected in [(0, 0, 0), (6, 0, 6), (0, 6, 6)]:
        u = dict(unit(), minimum_warranty_hours=minimum, promised_warranty_hours=promised)
        candidates = shop_candidates(u, {'balance': 5000000}, {'G111': {}}, j, policy())
        assert all(c['warranty_hours'] == expected for c in candidates)


def test_high_value_rfq_compares_live_quotes_reuses_task_and_cancels_unused(tmp_path, monkeypatch):
    import base64
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from r3.protocol import canon, signed_body
    from test_market_contract import pub
    from market_buyer import buy
    monkeypatch.setenv('R3_MODEL_MODE', 'offline')
    j, now = Journal(tmp_path), time.time()
    key = Ed25519PrivateKey.generate()
    atomic_json(j.md / 'catalog.json', {'G1': {'kind': 'goods', 'list_price': 1000000}})
    atomic_json(j.md / 'registry.json', {u: {'pubkey': pub(key)} for u in ('expensive', 'cheaper')})
    requests, bids, cancelled, closed = [], [], [], []
    class Peer:
        def discover(self, url): self.url = url
        def send(self, text, task=None):
            if not task:
                requests.append(self.url)
                return {'id': self.url, 'status': {'state': 'TASK_STATE_INPUT_REQUIRED',
                    'message': {'metadata': {'ask': 800000 if self.url == 'expensive' else 650000,
                                             'grade': 'A', 'warranty_hours': 6}}}}
            bids.append(self.url)
            rec = {'shop': self.url, 'item': 'G1', 'price': int(text), 'task': self.url,
                   'cert': True, 'claim': 'A', 'warranty_hours': 6}
            rec['sig_seller'] = base64.b64encode(key.sign(canon(signed_body(rec)))).decode()
            return {'id': self.url, 'status': {'state': 'TASK_STATE_COMPLETED'},
                    'artifacts': [{'parts': [{'data': rec}]}]}
        def cancel(self, task): cancelled.append(task)
        def close(self): closed.append(self.url)
    shops = [{'name': u, 'url': u, 'offers': [{'sku': 'G1', 'qty': 1}]} for u in ('expensive', 'cheaper')]
    result = buy({'order': 'o', 'acceptable': ['G1'], 'kind': 'goods', 'budget': 1000000,
                  'expires': now+300}, shops, j, peer_factory=Peer)
    assert result['shop'] == 'cheaper' and result['accept']['grades'] == ['A']
    assert sorted(requests) == ['cheaper', 'expensive'] and bids == ['cheaper']
    assert cancelled == ['expensive'] and sorted(closed) == ['cheaper', 'expensive']
    comparisons=j.events('quote_comparison')
    assert len([r for r in comparisons if r.get('source')!='selected_live_contract'])==2
    if policy().get('service_contract_v26_enabled'):
        assert any(r.get('source')=='selected_live_contract' and r['shop']=='cheaper' for r in comparisons)
