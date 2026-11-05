import base64
import hashlib
import json
from pathlib import Path
import sys
import time
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from starlette.testclient import TestClient
from r3 import customer_memory as memory
from r3.state import Journal, atomic_json, policy
from r3.protocol import canon
from r3.economy import shop_candidates
from seller_r3 import Seller, build
from test_market_contract import unit, pub


def setup(tmp_path):
    j = Journal(tmp_path)
    u = unit()
    atomic_json(tmp_path / 'catalog.json', {'G111': dict(name=u['name'], category=u['category'],
                                                       kind=u['kind'], list_price=u['list_price'])})
    atomic_json(tmp_path / 'inventory.json', {'round': 3, 'balance': 5000000, 'units': [u]})
    return j, u


def proof(key, shop, mid, text, now):
    payload = {'scope': memory.SCOPE, 'shop_pubkey': shop, 'message_id': mid, 'text': text, 'ts': now}
    return {'buyer_memory_proof': {'pubkey': pub(key), 'ts': now,
                                   'sig': base64.b64encode(key.sign(canon(payload))).decode()}}


def test_identity_proof_is_shop_bound_fresh_and_not_transferable(tmp_path):
    j, _ = setup(tmp_path)
    key = Ed25519PrivateKey.generate()
    now = int(time.time())
    metadata = proof(key, 'our-shop', 'm1', 'G111', now)
    subject = memory.signed_visitor(j, 't1', 'm1', 'G111', metadata, now, expected_shop='our-shop')
    assert subject and subject.startswith('key:')
    assert memory.signed_visitor(j, 't1', 'm1', 'G111', metadata, now, expected_shop='our-shop') == subject
    assert memory.signed_visitor(j, 'other-task', 'm1', 'G111', metadata, now, expected_shop='our-shop') is None
    assert memory.signed_visitor(j, 't1', 'm1', 'G111', metadata, now + 301, expected_shop='our-shop') is None
    assert memory.signed_visitor(j, 't1', 'm1', 'G111', metadata, now, expected_shop='different-shop') is None
    assert memory.signed_visitor(j, 't1', 'm1', '9000', metadata, now, expected_shop='our-shop') is None
    assert memory.signed_visitor(j, 't1', 'm1', 'G111', {'buyer': 'victim'}, now, expected_shop='our-shop') is None


def test_structured_memory_survives_restart_and_deduplicates_retries(tmp_path):
    j, u = setup(tmp_path)
    memory.visit(j, 't', 'G111 warranty certificate', u)
    for _ in range(2):
        memory.incoming(j, 't', 'm1', 'G111 warranty certificate', turn=1)
        memory.incoming(j, 't', 'm2', '7000', last_ask=9000, turn=2)
    restored = Journal(tmp_path)
    result = memory.summary(restored, 't', u)
    assert result['remembered_turns'] == 2 and result['current_offers'] == [7000]
    assert result['current_mentions']['mentions_warranty']
    assert len(j.events('customer_turn')) == 2
    assert 'G111 warranty certificate' not in json.dumps(j.events('customer_turn'))


def test_agreement_is_not_payment_and_visitors_are_not_merged(tmp_path):
    j, u = setup(tmp_path)
    memory.visit(j, 'one', 'G111', u)
    memory.finish(j, 'one', 'agreed_not_yet_paid')
    memory.visit(j, 'two', 'G111', u)
    result = memory.summary(j, 'two', u)
    assert result['identity_status'] == 'anonymous'
    assert result['category_behavior']['paid_visits'] == 0
    assert result['category_behavior']['unpaid_or_unknown_visits'] == 1
    assert 'customer_profile' not in result


def test_confirmed_market_payer_links_receipt_and_profile_once(tmp_path):
    j, u = setup(tmp_path)
    memory.visit(j, 't', 'G111', u)
    memory.paid(j, 't', 8000, 'G111', 101)
    memory.paid(j, 't', 8000, 'G111', 101)
    assert not memory.bind_market_payer(j, 999, {'buyer': 'unrelated'})
    assert memory.bind_market_payer(j, 101, {'buyer': 'confirmed-buyer'})
    assert memory.bind_market_payer(j, 101, {'buyer': 'confirmed-buyer'})
    assert not memory.bind_market_payer(j, 101, {'buyer': 'different-buyer'})
    result = memory.profile(j, 'market-round3:confirmed-buyer')
    assert result['paid_visits'] == 1 and result['purchased_categories'] == {u['category']: 1}
    assert len(j.events('customer_paid')) == len(j.events('customer_identity')) == 1


def test_public_ledger_before_receipt_is_reconciled(tmp_path):
    j, u = setup(tmp_path)
    memory.visit(j, 't', 'G111', u)
    j.add('public_settle', {'buyer': 'market-buyer'}, eid='public:101')
    assert not memory.bind_market_payer(j, 101, {'buyer': 'market-buyer'})
    memory.paid(j, 't', 8000, 'G111', 101)
    assert j.get('customer-payer:t') == 'market-round3:market-buyer'


def test_verified_customer_summary_reaches_real_a2a_decision(tmp_path):
    j, u = setup(tmp_path)
    buyer_key, shop_key = Ed25519PrivateKey.generate(), Ed25519PrivateKey.generate()
    subject = 'key:' + hashlib.sha256(base64.b64decode(pub(buyer_key))).hexdigest()
    for n in range(3):
        task = f'past-{n}'
        memory.visit(j, task, 'G111', u, signer=subject)
        memory.incoming(j, task, 'm-' + task, '8000', last_ask=9000, turn=1)
        memory.paid(j, task, 8000, 'G111', 100 + n)
    keypath = tmp_path / 'synthetic-shop.pem'
    keypath.write_bytes(shop_key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                              serialization.NoEncryption()))
    contexts = []
    class Capture:
        def choose(self, role, candidates, context, **kwargs):
            contexts.append(context)
            return max(candidates, key=lambda c: c['score'])
    text, mid, now = 'G111 certificate and warranty', 'returning-visit', int(time.time())
    with TestClient(build(9999, Seller(j, chooser=Capture(), key_path=keypath))) as client:
        response = client.post('/', headers={'A2A-Version': '1.0'}, json={
            'jsonrpc': '2.0', 'id': '1', 'method': 'SendMessage', 'params': {'message': {
                'role': 'ROLE_USER', 'messageId': mid, 'parts': [{'text': text}],
                'metadata': proof(buyer_key, pub(shop_key), mid, text, now)}}})
    assert response.status_code == 200 and contexts
    remembered = contexts[0]['customer_memory']
    assert remembered['identity_status'] == 'verified_key'
    assert remembered['customer_profile']['category_paid_visits'] == 3
    assert remembered['customer_profile']['median_paid_to_list_ratio'] == .8
    assert remembered['current_mentions']['mentions_certificate']


def test_history_price_candidate_obeys_existing_floor_and_quote_ceiling(tmp_path):
    j, u = setup(tmp_path)
    memory_context = {'category_behavior': {'paid_visits': 3, 'median_paid_to_list_ratio': .8}}
    candidates = shop_candidates(u, {}, {}, j, policy(), customer_memory=memory_context)
    assert any(c['price'] == 8000 for c in candidates)
    memory_context['category_behavior']['median_paid_to_list_ratio'] = .1
    candidates = shop_candidates(u, {}, {}, j, policy(), previous_ask=8500, customer_memory=memory_context)
    assert all(c['price'] >= c['recovery_floor'] and c['price'] <= 8500 for c in candidates if c['action']!='hold')


def test_stockout_category_is_observed_without_inventing_product(tmp_path):
    j, u = setup(tmp_path)
    result = memory.visit(j, 't', 'I want appliance but it is unavailable')
    assert result['category'] == u['category'] and result['sku'] is None


def test_fifo_removes_old_visit_behavior_and_identity_but_keeps_liabilities(tmp_path):
    j, u = setup(tmp_path)
    for n in range(4):
        task = f'visit-{n}'
        memory.visit(j, task, 'G111', u)
        memory.incoming(j, task, task, '8000', last_ask=9000)
        memory.paid(j, task, 8000, 'G111', n, {'buyer': 'buyer-' + str(n)})
    j.add('shop_settled', {'task': 'visit-0', 'pending_warranty': 8000}, eid='liability')
    # A late turn does not move the oldest visit to the back of the FIFO.
    memory.incoming(j, 'visit-0', 'late-turn', '8100')
    memory.retain(j, max_visits=2, max_events=20)
    assert {e['task'] for e in j.events('customer_visit')} == {'visit-2', 'visit-3'}
    assert not j.get('customer-visit:visit-0') and not j.get('customer-payer:visit-0')
    assert not j.get('customer-ledger:0')
    assert memory.profile(j, 'market-round3:buyer-0')['paid_visits'] == 0
    assert len(j.events('shop_settled')) == 1
    with j.connect() as connection:
        assert connection.execute("SELECT count(*) FROM events WHERE kind LIKE 'customer_%' "
                                  "AND json_extract(data,'$.task')='visit-0'").fetchone()[0] == 0


def test_fifo_limits_turns_inside_one_long_visit(tmp_path):
    j, u = setup(tmp_path)
    memory.visit(j, 't', 'G111', u)
    for n in range(10):
        memory.incoming(j, 't', str(n), str(8000 + n), turn=n)
    memory.retain(j, max_visits=1, max_events=5)
    assert {e['turn'] for e in j.events('customer_turn')} == {6, 7, 8, 9}
    assert len(j.events('customer_visit')) == 1


def test_proof_capacity_preserves_valid_replay_guards(monkeypatch, tmp_path):
    j, _ = setup(tmp_path)
    monkeypatch.setattr(memory, 'MAX_PROOFS', 1)
    key, now = Ed25519PrivateKey.generate(), int(time.time())
    first = proof(key, 'shop', 'one', 'G111', now)
    second = proof(key, 'shop', 'two', 'G111', now)
    assert memory.signed_visitor(j, 't1', 'one', 'G111', first, now, 'shop')
    assert memory.signed_visitor(j, 't2', 'two', 'G111', second, now, 'shop') is None
    memory.retain(j)
    assert memory.signed_visitor(j, 'replay', 'one', 'G111', first, now, 'shop') is None
    fresh = proof(key, 'shop', 'two', 'G111', now + 301)
    assert memory.signed_visitor(j, 't2', 'two', 'G111', fresh, now + 301, 'shop')
