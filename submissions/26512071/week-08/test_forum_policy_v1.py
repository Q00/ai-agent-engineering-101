"""RED: bounded opt-in forum report from exact public Round 3 settle evidence (forum_r3.maybe_report)."""
import json
import socket
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

SHOP = 'https://shop-x.trycloudflare.com'
NAME = 'Secret Sofa 3000'
T0 = 1_800_000_000


def settle(seq, order, sku, price, buyer='fair-furniture', shop=SHOP, kind='settle', grade='A', warranty=6):
    return {'seq':seq, 'ts':T0, 'kind':kind, 'round':3,
            'data':{'buyer':buyer, 'shop':shop, 'order':order, 'item':sku, 'amount':price,
                    'grade':grade, 'warranty_hours':warranty}}


def make_state(root, enabled=True, payments=()):
    state = Path(root)/'.market'
    state.mkdir()
    if enabled is not None:
        (state/'forum-policy.json').write_text(json.dumps({'enabled':enabled}), encoding='utf-8')
    rows = [dict(order=o, shop=SHOP, item=sku, price=p, paid=True, ledger=seq) for o, sku, p, seq in payments]
    (state/'payments.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in rows), encoding='utf-8')
    (state/'catalog.json').write_text(json.dumps({'SKU123':{'name':NAME}}), encoding='utf-8')
    (state/'deal.json').write_text(json.dumps({'order':'o2', 'receipt':{'price':90000},
        'settled':{'ledger':42, 'shop':SHOP, 'sku':'SKU123', 'name':NAME, 'grade':'A', 'claim_honest':True}}), encoding='utf-8')
    return state


def outbox(state):
    path = state/'outbox.jsonl'
    return [json.loads(l) for l in path.read_text(encoding='utf-8').splitlines()] if path.is_file() else []


def no_network():
    return mock.patch.object(socket.socket, 'connect', side_effect=AssertionError('network used with injected rows'))


class ForumPolicyV1(unittest.TestCase):
    def test_disabled_by_default_and_hook_keeps_truthful_review(self):
        import forum_r3
        import review_r3
        with tempfile.TemporaryDirectory() as root, no_network():
            state = make_state(root, enabled=None, payments=[('o2', 'SKU123', 90000, 42)])
            result = forum_r3.maybe_report(state, now=T0, rows=[settle(42, 'o2', 'SKU123', 90000)])
            self.assertFalse(result.get('queued'))
            self.assertEqual(outbox(state), [])
            expected = review_r3.review(json.loads((state/'deal.json').read_text(encoding='utf-8')))
            with mock.patch('forum_r3.maybe_report') as helper:
                self.assertEqual(review_r3.review_file(state/'deal.json'), expected)
                helper.assert_not_called()  # not opt-in -> helper never called
            (state/'forum-policy.json').write_text(json.dumps({'enabled':True}), encoding='utf-8')
            with mock.patch('forum_r3.maybe_report', side_effect=RuntimeError('post failed')) as helper:
                self.assertEqual(review_r3.review_file(state/'deal.json'), expected)
                helper.assert_called_once()

    def test_enabled_queues_one_exact_factual_report_then_rate_limits_and_dedupes(self):
        import forum_r3
        with tempfile.TemporaryDirectory() as root, no_network():
            state = make_state(root, payments=[('o1', 'SKU999', 50000, 40), ('o2', 'SKU123', 90000, 42)])
            rows = [settle(40, 'o1', 'SKU999', 50000), settle(42, 'o2', 'SKU123', 90000)]
            first = forum_r3.maybe_report(state, now=T0, rows=rows)
            self.assertIs(first.get('queued'), True)
            self.assertNotIn('posted', first)  # local append is not an external publish
            lines = outbox(state)
            self.assertEqual(len(lines), 1)
            line = lines[0]
            self.assertEqual(set(line), {'post', 'refs', 'as'})
            self.assertEqual((line['refs'], line['as']), ([42], 'buyer'))  # latest paid ledger only
            text = line['post']
            for fact in ('SKU123', '90000', 'A', '6'):
                self.assertIn(fact, text)
            low = text.lower()
            for caveat in ('durability', 'unknown', 'sample'):
                self.assertIn(caveat, low)
            for leak in (NAME, 'Secret', 'Paid settlement', 'claim_honest', SHOP):
                self.assertNotIn(leak, text)
            # new settlement within 12h: nothing queued; after 12h: one queued; old content never again
            with open(state/'payments.jsonl', 'a', encoding='utf-8') as f:
                f.write(json.dumps(dict(order='o3', shop=SHOP, item='SKU777', price=70000, paid=True, ledger=50))+'\n')
            rows.append(settle(50, 'o3', 'SKU777', 70000, grade='B', warranty=0))
            self.assertFalse(forum_r3.maybe_report(state, now=T0+3600, rows=rows).get('queued'))
            self.assertEqual(len(outbox(state)), 1)
            self.assertTrue(forum_r3.maybe_report(state, now=T0+13*3600, rows=rows).get('queued'))
            self.assertEqual([l['refs'] for l in outbox(state)], [[42], [50]])
            self.assertFalse(forum_r3.maybe_report(state, now=T0+40*3600, rows=rows).get('queued'))
            self.assertEqual(len(outbox(state)), 2)

    def test_rejects_unmatched_public_evidence_and_fails_closed_after_failed_append(self):
        import forum_r3
        payments = [('o2', 'SKU123', 90000, 42)]
        bad = [settle(42, 'o2', 'SKU123', 90000, buyer='other-buyer'),
               settle(42, 'o2', 'SKU123', 89999),
               settle(42, 'o9', 'SKU123', 90000),
               settle(42, 'o2', 'SKU124', 90000),
               settle(42, 'o2', 'SKU123', 90000, shop='https://other.trycloudflare.com'),
               settle(42, 'o2', 'SKU123', 90000, kind='order'),
               settle(41, 'o2', 'SKU123', 90000)]
        with no_network():
            for row in bad:
                with self.subTest(row=row), tempfile.TemporaryDirectory() as root:
                    state = make_state(root, payments=payments)
                    self.assertFalse(forum_r3.maybe_report(state, now=T0, rows=[row]).get('queued'))
                    self.assertEqual(outbox(state), [])
            with tempfile.TemporaryDirectory() as root:
                state = make_state(root, payments=payments)
                (state/'outbox.jsonl').mkdir()  # append fails after intent was recorded
                try:
                    result = forum_r3.maybe_report(state, now=T0, rows=[settle(42, 'o2', 'SKU123', 90000)])
                    self.assertFalse(result.get('queued'))
                except OSError:
                    pass
                (state/'outbox.jsonl').rmdir()
                self.assertFalse(forum_r3.maybe_report(state, now=T0+13*3600,
                                                       rows=[settle(42, 'o2', 'SKU123', 90000)]).get('queued'))
                self.assertEqual(outbox(state), [])  # recorded intent blocks a duplicate attempt


if __name__ == '__main__':
    unittest.main()
