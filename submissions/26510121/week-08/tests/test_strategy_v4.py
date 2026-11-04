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
