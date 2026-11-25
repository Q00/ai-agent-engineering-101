import unittest
import scorecard

class DriftTest(unittest.TestCase):
    def test_drift_and_gap_use_same_public_board_scale(self):
        b={'shops':[{'name':scorecard.ME,'profit':1,'revenue':1,'stars_rank':1,'all':.7}],
           'buyers':[{'name':scorecard.ME,'surplus':1,'forum':1,'deals':1,'posts':1,'all':1}],
           'players':[{'player':'Nocha12','total':1.7},{'player':'leader','total':1.9}]}
        o=scorecard.objective(b)
        self.assertEqual(o['board_drift'], {'shops':.3,'buyers':0})
        self.assertEqual(o['board_players_gap'], .2)
        self.assertEqual(o['players'], 2)
        del b['players']
        self.assertIsNone(scorecard.objective(b)['board_players_gap'])


class ForumRatingTest(unittest.TestCase):
    def test_buyer_forum_uses_rating_and_next_step_uses_rating_delta(self):
        me = {'name': scorecard.ME, 'saved': .2, 'forum': .87, 'deals': 10, 'posts': 10, 'all': .761}
        other = {'name': 'other', 'saved': .1, 'forum': .88, 'deals': 1, 'posts': 1, 'all': 0}
        obj = scorecard.objective({'shops': [], 'buyers': [me, other]})
        self.assertEqual(obj['buyers']['all'], .961)
        self.assertEqual(obj['buyers']['parts']['forum']['next']['delta_players'], .003)
        self.assertEqual(obj['board_drift']['buyers'], .2)


class BookTest(unittest.TestCase):
    def test_shop_all_ranks_book_when_the_board_has_it(self):
        me = {'name': scorecard.ME, 'profit': 1, 'book': 9, 'revenue': 5, 'stars_rank': 5, 'all': .75}
        other = {'name': 'x', 'profit': 5, 'book': 5, 'revenue': 9, 'stars_rank': 9, 'all': .25}
        o = scorecard.objective({'shops': [me, other], 'buyers': []})
        self.assertIn('book', o['shops']['parts'])
        self.assertEqual(o['shops']['all'], .5)
        self.assertEqual(o['board_drift']['shops'], -.25)
        r = scorecard.rivals([me, other])
        self.assertEqual((r['ranked_by'], r['my_rank']), ('book', 1))
        self.assertEqual(scorecard.rivals([{k: v for k, v in other.items() if k != 'book'}])['ranked_by'], 'profit')


class RaceTest(unittest.TestCase):
    BOARD = {'players': [{'player': 'lead', 'total': 1.9}, {'player': 'up', 'total': 1.5}, {'player': 'Nocha12', 'total': 1.27}]}

    def test_rank_gaps_and_growth_of_me_the_leader_and_the_player_above(self):
        past = [{'ts': 0, 'players': {'lead': 1.8, 'up': 1.45, 'Nocha12': 1.3}},
                {'ts': 30000, 'players': {'lead': 1.85}}]
        r = scorecard.race(self.BOARD, past, now=0 + 6 * 3600 + 60)
        self.assertEqual((r['rank'], r['above'], r['gap_to_above'], r['gap_to_leader']), (3, 'up', .23, .63))
        self.assertEqual(r['growth']['6h'], {'me': -.03, 'leader': .1, 'above': .05, 'hours': 6.0})
        self.assertIsNone(r['growth']['12h'])

    def test_the_percentile_is_not_rounded_before_the_weights(self):
        rows = [{'name': str(i), 'book': i, 'revenue': i, 'stars_rank': i, 'all': 0} for i in range(7)]
        rows[3]['name'] = scorecard.ME
        o = scorecard.objective({'shops': rows, 'buyers': []})
        self.assertEqual(o['shops']['all'], .5)
        self.assertTrue(o['shops']['ranked'])

    def test_next_step_names_the_next_rival_and_what_passing_it_adds(self):
        rows = [{'name': str(i), 'book': i * 10, 'revenue': i, 'stars_rank': 4.5, 'all': 0} for i in range(5)]
        rows[2]['name'] = scorecard.ME          # book 20 among 0, 10, 20, 30, 40: next rival 30
        o = scorecard.objective({'shops': rows, 'buyers': []})
        nx = o['shops']['parts']['book']['next']
        self.assertEqual((nx['rival_value'], nx['need'], nx['delta_players']), (30, 10, .125))   # .5 x (3/4 - 2/4)
        stars = o['shops']['parts']['stars_rank']['next']
        self.assertIsNone(stars)            # all tied, nobody above
        rows[4]['book'] = 20                 # a tie with me counts half now; passing 30 passes it too: .5 x (4/4 - 2.5/4)
        self.assertEqual(scorecard.objective({'shops': rows, 'buyers': []})['shops']['parts']['book']['next']['delta_players'], .1875)
        self.assertEqual(o['next_steps'][0]['field'], 'book')
        top = [{'name': scorecard.ME, 'book': 9, 'revenue': 9, 'stars_rank': 9}, {'name': 'x', 'book': 1, 'revenue': 1, 'stars_rank': 1, 'all': 0}]
        self.assertEqual(scorecard.objective({'shops': top, 'buyers': []})['next_steps'], [])

    def test_an_unscored_row_is_estimated_but_marked(self):
        me = {'name': scorecard.ME, 'book': 1, 'revenue': 1, 'stars_rank': 1}
        o = scorecard.objective({'shops': [me, {'name': 'x', 'book': 2, 'revenue': 2, 'stars_rank': 2, 'all': .9}], 'buyers': []})
        self.assertFalse(o['shops']['ranked'])


class AlertTest(unittest.TestCase):
    def test_cap_surge_forum_and_drift_raise_alerts(self):
        import json, os, tempfile, time
        now = time.time()
        with tempfile.TemporaryDirectory() as md:
            os.makedirs(os.path.join(md, 'measure'))
            with open(os.path.join(md, 'llm_calls.jsonl'), 'w') as f:
                f.writelines(json.dumps({'ts': now - 10, 'tag': 'seller-harness', 'ok': i > 0, 'cost': 0.001}) + '\n' for i in range(301))
            with open(os.path.join(md, 'measure', 'llm_cap.jsonl'), 'w') as f:
                f.write(json.dumps({'ts': now - 5, 'tag': 'buyer-agent'}) + '\n')
                f.write(json.dumps({'ts': now - 5, 'tag': 'seller-harness'}) + '\n')
            json.dump({'G1': {'list_price': 1000}}, open(os.path.join(md, 'catalog.json'), 'w'))
            json.dump({'units': [{'sku': 'G1', 'cost': 950, 'supplied_at': now - 60},
                                 {'sku': 'G1', 'cost': 700, 'supplied_at': now - 60}]}, open(os.path.join(md, 'inventory.json'), 'w'))
            b = {'objective': {'buyers': {'parts': {'forum': {'value': .55}}}, 'board_drift': {'shops': .002, 'buyers': 0}},
                 'forum_6h_ago': .58}
            got = [a.split(':')[0] for a in scorecard.alerts(md, b, now=now)]
            self.assertEqual(got, ['llm_cap', 'surge', 'forum', 'board_drift'])        # 301 calls an hour: no alert
            shown = scorecard.llm_last_hour(md, now=now)                               # shown, not a limit
            self.assertEqual((shown['calls'], shown['failed'], shown['cost'], shown['tags']), (301, 1, 0.301, [('seller-harness', 301)]))
            first = scorecard.alerts(md, b, now=now)[0]
            self.assertIn('buyer-agent', first)
            self.assertNotIn('seller-harness', first)                                    # it answers by code
            b['forum_6h_ago'] = .555
            b['objective']['board_drift'] = {}
            os.remove(os.path.join(md, 'measure', 'llm_cap.jsonl'))
            self.assertEqual([a.split(':')[0] for a in scorecard.alerts(md, b, now=now)], ['surge'])
            os.remove(os.path.join(md, 'llm_calls.jsonl'))
            self.assertEqual(scorecard.llm_last_hour(md, now=now)['calls'], 0)


class CacheAlertTest(unittest.TestCase):
    def write(self, md, rows):
        import json, os
        with open(os.path.join(md, 'llm_calls.jsonl'), 'w') as f:
            f.writelines(json.dumps(r) + '\n' for r in rows)

    def test_a_falling_cache_share_alerts_and_too_few_calls_or_old_lines_do_not(self):
        import tempfile, time
        now = time.time()
        row = lambda ts, cached, ok=True, backend='codex': {'ts': ts, 'tag': 'seller-harness', 'ok': ok, 'backend': backend,
                                                             'prompt_tokens': 9000, 'cached_tokens': cached}
        with tempfile.TemporaryDirectory() as md:
            before = [row(now - 3 * 3600, 6912 if i % 3 else 0) for i in range(300)]          # 0.51 in the 6 h before
            self.write(md, before + [row(now - 60, 6912 if i % 5 == 0 else 0) for i in range(60)])   # 0.15 now
            a, = scorecard.alerts(md, {}, now=now)
            self.assertTrue(a.startswith('llm_cache: cached input share 0.154 over 60 calls'), a)
            self.assertIn('never cut calls', a)
            self.write(md, before + [row(now - 60, 6912 if i % 2 else 0) for i in range(60)])        # 0.38: no drop
            self.assertEqual(scorecard.alerts(md, {}, now=now), [])
            self.write(md, [row(now - 60, 0) for i in range(49)])                                     # too few calls
            self.assertEqual(scorecard.alerts(md, {}, now=now), [])
            self.write(md, [{'ts': now - 60, 'tag': 't', 'ok': True, 'backend': 'codex', 'prompt_tokens': 9000} for i in range(80)]
                       + [row(now - 60, 0, ok=False) for i in range(80)]                      # no field, failed calls
                       + [row(now - 60, 0, backend='openrouter') for i in range(80)]          # another backend
                       + [row(now - 60, True) for i in range(80)] + [row('x', 0) for i in range(80)]   # odd values
                       + [{**row(now - 60, 0), 'prompt_tokens': '9000'} for i in range(80)])
            self.assertEqual(scorecard.alerts(md, {}, now=now), [])
            self.write(md, [row(now - 60, 0) for i in range(60)])                                     # under the floor
            self.assertEqual([x.split(':')[0] for x in scorecard.alerts(md, {}, now=now)], ['llm_cache'])
            self.assertEqual(scorecard.llm_table(md)['seller-harness']['cache']['ratio'], 0.0)


class JsonlTest(unittest.TestCase):
    def test_a_cut_line_is_skipped_not_the_whole_file(self):
        import os, tempfile
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, 'h.jsonl')
            with open(p, 'w') as f:
                f.write('{"ts": 1}\n{"ts": 2, "pla{"ts": 3}\n{"ts": 4}\n')
            self.assertEqual([r['ts'] for r in scorecard.jsonl(p)], [1, 4])
