import unittest
from ops.market_consistency import compare,NAME

class ConsistencyTests(unittest.TestCase):
    def board(self):
        return {'shops':[{'name':NAME,'deals':10,'revenue':1000,'book':5000,'all':.7}],
                'buyers':[{'name':NAME,'deals':20,'saved':.72,'all':.6}]}

    def test_displayed_zero_never_erases_paid_evidence_or_becomes_real_rank(self):
        b=self.board();b['shops'][0].update(deals=0,revenue=0)
        r=compare(b,{'settled':10,'revenue':1000,'latest_ledger':30},
                  {'settled':20,'revenue':2000,'latest_ledger':31},now=100)
        self.assertFalse(r['official_ranking_reliable'])
        self.assertEqual(r['official_seller']['deals'],0)
        self.assertEqual(r['seller']['settled'],10)

    def test_identical_board_with_new_paid_ledger_is_stale_after_one_minute(self):
        seller={'settled':10,'revenue':1000,'latest_ledger':30}
        buyer={'settled':20,'revenue':2000,'latest_ledger':31}
        old=compare(self.board(),seller,buyer,now=100)
        new=compare(self.board(),seller,{**buyer,'latest_ledger':40},old,now=200)
        self.assertIn('board_unchanged_while_new_paid_ledgers_arrived',new['issues'])
        self.assertEqual(new['official_buyer']['saved'],.72)

    def test_local_partial_history_does_not_claim_remote_counts_are_wrong(self):
        r=compare(self.board(),{'settled':3,'revenue':200,'latest_ledger':30},
                  {'settled':4,'revenue':500,'latest_ledger':31},now=100)
        self.assertTrue(r['official_ranking_reliable'])


    def test_refund_adjusted_revenue_is_not_misreported_as_stale_board(self):
        b=self.board();b['shops'][0].update(revenue=900,refunds=1)
        r=compare(b,{'settled':10,'revenue':1000,'latest_ledger':30},
                  {'settled':20,'revenue':2000,'latest_ledger':31},now=100)
        self.assertTrue(r['official_ranking_reliable'])
        self.assertEqual(r['gross_local_minus_official_revenue'],100)
