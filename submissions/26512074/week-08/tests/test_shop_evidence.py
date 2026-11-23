import unittest
from shop_evidence import analyze, render


class EvidenceTests(unittest.TestCase):
    def test_round_window_duplicates_aliases_and_rotation(self):
        board = {'round': 3, 'shops': [
            {'name': 'a', 'all': .9, 'refunds': 2, 'broken': 1},
            {'name': 'b', 'all': .8}, {'name': 'c', 'all': .7},
            {'name': 'd', 'all': .6}]}
        shops = [{'name': 'a', 'url': 'https://a'}]
        events = [{'seq': i, 'round': 3, 'kind': 'settle', 'ts': 999,
                   'data': {'buyer': 'buyer-1', 'shop': 'https://a',
                            'sku': 'x', 'amount': 100}} for i in range(4)]
        bad = [{**events[0], 'seq': 90, 'round': 2},
               {**events[0], 'seq': 91, 'ts': 2000},
               {**events[0], 'seq': 92, 'ts': -99999}]
        evidence = analyze(board, shops, events+events+bad, 1000)
        self.assertEqual(len(evidence['leaders']), 3)
        self.assertEqual(evidence['products'][0]['count'], 4)
        self.assertEqual(evidence['products'][0]['revenue'],400)
        self.assertEqual(evidence['repeat_pairs'][0]['count'], 4)
        self.assertEqual(evidence['repeat_pairs'][0]['amount'], 400)
        self.assertEqual(evidence['repeat_pairs'][0]['share'], 1)
        self.assertEqual(evidence['repeat_pairs'][0]['reverse_count'], 0)
        self.assertFalse(evidence['repeat_pairs'][0]['buyer_shop_identity_verified'])
        first, refs = render(evidence, 0)
        second, _ = render(evidence, 300)
        self.assertEqual(first[1], second[1])
        self.assertIn('환불 2건', first[1])
        self.assertIn('고장 1건', second[1])
        self.assertTrue(refs)
        self.assertIn('검증된 부정적 사실 없음', first[2])
        text = '\n'.join(first)
        self.assertIn('구매자 buyer-1 → 판매처 a: 4건', text)
        self.assertIn('100.0%', text)
        self.assertNotIn('동일 소유자', text)

    def test_missing_board_never_invents_leaders(self):
        self.assertEqual(analyze({}, [], [], 1000)['leaders'], [])
        self.assertEqual(analyze({'round': 2, 'shops': [{'name': 'a', 'all': 1}]}, [], [], 1000)['leaders'], [])


if __name__ == '__main__':
    unittest.main()
