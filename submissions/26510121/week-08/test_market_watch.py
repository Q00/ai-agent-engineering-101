"""Check false positives, unknown identities and duplicate/round denominators."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[0]))
from market_watch import analyze
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'week-07'))
from negotiation.market import choose_shop, read_review_cautions, review_trade


def inputs():
    board = {'round': 2, 'buyers': [], 'shops': []}
    registry = {'shops': []}
    for name in ['a','b','c']:
        row = {'name':name, 'owner':{'login':name}, 'card':{'url':'https://'+name+'.example'}}
        board['buyers'].append(row)
        board['shops'].append(row)
        registry['shops'].append({'name':name,'url':'https://'+name+'.example'})
    return board, registry


def event(seq, buyer, seller, **updates):
    return {'seq':seq,'round':2,'kind':'result',
            'data':{'order':str(seq),'buyer':buyer,'shop':'https://'+seller+'.example',
                    'verified':True,'stars':5, **updates}}


class ConcentrationTests(unittest.TestCase):
    def test_reciprocal_pattern_is_advisory_and_has_exact_denominators(self):
        board, registry = inputs()
        rows=[event(n,'a','b') for n in range(20)] + [event(n,'b','a') for n in range(20,40)]
        data=analyze(rows,board,registry)
        self.assertEqual(data['review_caution_shops'],['a','b'])
        self.assertEqual(data['pairs'][0]['a_verified_results'],20)
        self.assertIn('not proof',data['pairs'][0]['interpretation'])

    def test_one_sided_loyalty_and_small_samples_are_not_flagged(self):
        board, registry = inputs()
        rows=[event(n,'a','b') for n in range(100)]
        self.assertEqual(analyze(rows,board,registry)['pairs'],[])
        rows += [event(n,'b','a') for n in range(100,119)]
        self.assertEqual(analyze(rows,board,registry)['pairs'],[])

    def test_unknown_sellers_stay_in_denominator_and_duplicates_do_not_inflate_it(self):
        board, registry = inputs()
        rows=[event(n,'a','b') for n in range(20)] + [event(n,'b','a') for n in range(20,40)]
        rows += [event(n,'a','unknown') for n in range(40,60)]
        rows += rows[:10]
        rows.append({**event(70,'b','a'),'round':3})
        data=analyze(rows,board,registry)
        self.assertEqual(data['pairs'],[])
        self.assertEqual(data['unmapped_seller_results_for_known_buyers'],20)
        self.assertEqual(data['duplicate_results_excluded'],10)
        self.assertEqual(data['verified_results_from_other_rounds_excluded'],1)

    def test_caution_expires_and_never_blacklists_a_good_personal_counterparty(self):
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder)/'cautions.json'
            p.write_text(json.dumps({'schema':1,'advisory_only':True,'generated_epoch':100,
                                    'max_age_seconds':100,'shops':['a']}))
            self.assertEqual(read_review_cautions(p,now=150),{'a'})
            self.assertEqual(read_review_cautions(p,now=201),set())
        candidates=[{'name':n,'url':'https://'+n+'.example','reviews':[],
                     'review_concentration_caution':n=='a'} for n in ['a','b']]
        memory=[{'name':'a','item':'lamp','outcome':'purchased','price':100,
                 'signature_verified':True,'seconds':1}]*10
        picked,_,evidence=choose_shop(candidates,memory,'lamp',120,require_verified=True)
        self.assertEqual(picked['name'],'a')
        self.assertTrue(evidence['public_reviews_discounted'])

    def test_round_three_review_omits_budget_value(self):
        stars,text=review_trade({'outcome':'purchased','receipt':{'price':100}},None,137,2,reveal_budget=False)
        self.assertEqual(stars,5)
        self.assertIn('100',text)
        self.assertNotIn('137',text)


if __name__ == '__main__':
    unittest.main()
