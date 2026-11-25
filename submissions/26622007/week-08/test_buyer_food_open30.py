"""Public pilot seam: a real food opening, immutable finite assignment, no payment API."""
import json
import os
import tempfile
import unittest

import buyer_food_open30 as pilot


class FoodOpen30Test(unittest.TestCase):
    def test_real_food_first_offer_is_one_percent_lower_only_in_recorded_B(self):
        # Actual cheapest-in-town G2108d9bc opening: 9812, paid first baseline8830.
        with tempfile.TemporaryDirectory() as md:
            cfg = {'enabled': True, 'study': 'food-open30-test', 'salt': 'food30-test',
                   'start': 2000000000, 'end': 2000001800}
            path = os.path.join(md, 'buyer_food_open30.json')
            with open(path, 'w') as f:
                json.dump(cfg, f)
            os.utime(path, (1999999990, 1999999990))
            order = {'order': 'o-food30-0', 'kind': 'food'}
            q = {'ask': 9812, 'ask_src': 'text', 'sku': 'G2108d9bc',
                 'text': '시티델리 풍미가득 볶음밥 5종 10봉 택 (G2108d9bc), grade C, about 171.8 h shelf life left. Market certificate included; warranty 0 h. Asking price: 9812. Send one price as a number, or accept.'}
            note = pilot.assign(md, order, 'cheapest-in-town', q, (.9,), True,
                                probing=False, code_priced=True, limit=13230, env={}, now=2000000001)
            self.assertEqual(note['arm'], 'B')
            self.assertEqual(note['steps'], [.9])
            baseline = {'shop_price': 9812, 'action': 'offer', 'price': 8830}
            out = pilot.apply(baseline, [], 13230, (.9,), note, md=md, env={}, now=2000000001)
            self.assertEqual(out['price'], 8732)
            self.assertEqual(baseline['price'], 8830)
            self.assertTrue(note['applied'])
            # After any actual offer, all counter and walk rules are exactly unchanged.
            counter = {'shop_price': 9300, 'action': 'offer', 'price': 9300}
            self.assertEqual(pilot.apply(counter, [('you offered',8732)], 13230, (.9,), note,
                                         md=md, env={}, now=2000000002), counter)
            self.assertEqual(pilot.apply(baseline, [], 13230, (.9,), note, md=md, env={}, now=2000001800), baseline)


class FoodOpen30Guards(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.md = self.tmp.name
        self.cfg = {'enabled': True, 'study': 'food-open30-test', 'salt': 'food30-test',
                    'start': 2000000000, 'end': 2000001800}
        self.path = os.path.join(self.md, 'buyer_food_open30.json')
        self.quote = {'ask': 9812, 'ask_src': 'text', 'sku': 'G2108d9bc',
                      'text': '시티델리 볶음밥 (G2108d9bc), grade C. Asking price: 9812.'}
        self.order = {'order': 'o-food30-0', 'kind': 'food'}
        self.baseline = {'shop_price': 9812, 'action': 'offer', 'price': 8830}
        self.flag()

    def flag(self, **changes):
        with open(self.path, 'w') as f:
            json.dump(dict(self.cfg, **changes), f)
        os.utime(self.path, (1999999990, 1999999990))

    def assign(self, order=None, quote=None, shop='cheapest-in-town', **kwargs):
        args = dict(probing=False, code_priced=True, limit=13230, env={}, now=2000000001)
        args.update(kwargs)
        return pilot.assign(self.md, self.order if order is None else order, shop,
                            self.quote if quote is None else quote, (.9,), True, **args)

    def test_A_is_unmodified_and_B_price_cannot_enter_A_trade_learning(self):
        import buyer_food_ladder
        note = self.assign(order=dict(self.order, order='o-food30-2'))
        self.assertEqual(note['arm'], 'A')
        self.assertEqual(pilot.apply(self.baseline, [], 13230, (.9,), note,
                                    md=self.md, env={}, now=2000000001), self.baseline)
        a, b = {'order':'control', 'food_ladder':'A'}, {'order':'treatment','food_ladder':'B'}
        self.assertEqual(buyer_food_ladder.priced([a,b]), [a])
        self.assertTrue(buyer_food_ladder.laddered({'food_ladder':dict(note, arm='B')}))

    def test_only_selected_single_food_SKU_without_other_opening_policy(self):
        cases = [dict(order=dict(self.order, kind='goods')),
                 dict(quote=dict(self.quote, sku='G1c830e4a')),
                 dict(quote=dict(self.quote, text=self.quote['text']+' Also G1c830e4a')),
                 dict(shop='olstjr'), dict(probing=True), dict(code_priced=False),
                 dict(order=dict(self.order, _one_won_default=True)),
                 dict(order=dict(self.order, _food_quote_yield='B')),
                 dict(order=dict(self.order, _b3={'applied':False})),
                 dict(order=dict(self.order, _food_ladder={'arm':'A'})),
                 dict(order=dict(self.order, _food_shelf={'would_drop':['x']})),
                 dict(quote=dict(self.quote, ask=True)), dict(limit=8000)]
        for case in cases:
            with self.subTest(case=case):
                self.assertIsNone(self.assign(**case))
        self.assertFalse(os.path.exists(os.path.join(self.md, pilot.JOURNAL)))

    def test_missing_bad_or_late_flag_and_finite_boundaries_keep_baseline(self):
        for now in (1999999999,2000001800,2000001801,float('nan'),float('inf'),10**400):
            self.assertIsNone(self.assign(now=now))
        self.assertIsNotNone(self.assign(now=2000000000))
        self.assertIsNotNone(self.assign(now=2000001799))
        for changes in ({'end':2000001801},{'end':float('inf')},{'start':True},{'enabled':False}):
            self.flag(**changes)
            self.assertIsNone(self.assign())
        self.flag()
        os.utime(self.path,(2000000001,2000000001))
        self.assertIsNone(self.assign())
        os.unlink(self.path)
        self.assertIsNone(self.assign())
        with open(self.path,'w') as f:
            f.write('{broken')
        self.assertIsNone(self.assign())

    def test_journal_failure_is_fail_closed_and_existing_assignment_is_not_duplicated(self):
        path = os.path.join(self.md,pilot.JOURNAL)
        os.mkdir(path)
        self.assertIsNone(self.assign())
        os.rmdir(path)
        with open(path,'w') as f:
            f.write('{broken')
        self.assertIsNone(self.assign())
        os.unlink(path)
        first = self.assign()
        with open(path) as f:
            original = f.read()
        self.assertEqual(self.assign(),first)
        with open(path) as f:
            self.assertEqual(f.read(),original)
        self.assertIsNone(self.assign(quote=dict(self.quote,ask=9813)))
        self.assertEqual(len(json.loads(original)['orders']),1)

    def test_switch_off_after_assignment_blocks_first_treatment_and_limits_remain(self):
        note = self.assign()
        self.flag(enabled=False)
        self.assertEqual(pilot.apply(self.baseline, [], 13230, (.9,), note,
                                    md=self.md, env={}, now=2000000002), self.baseline)
        self.assertFalse(note['applied'])
        self.flag()
        self.assertEqual(pilot.apply(self.baseline, [], 8700, (.9,), note,
                                    md=self.md, env={}, now=2000000002), self.baseline)
        self.assertIsNone(self.assign(env={'BUYER_FOOD_OPEN30':'0'}))
        self.assertIsNone(self.assign(env={'BUYER_FOOD_LADDER':'0'}))

    def test_explicit_retry_scope_treats_a_real_selected_alternate_food_sku(self):
        # Real selected order o-f7e3333dd50b: ask10866, baseline9779, food G2560a8c9.
        self.flag(scope='food-code90-five-shops-v2')
        q = {'ask':10866, 'ask_src':'text', 'sku':'G2560a8c9',
             'text':'사세 직화K버팔로윙 1kg (G2560a8c9), grade B, about 562.5 h shelf life left. Market certificate included; warranty 0 h. Asking price: 10866. Send one price as a number, or accept.'}
        note = self.assign(quote=q, limit=12530)
        self.assertIsNotNone(note)
        self.assertEqual(note['scope'],'food-code90-five-shops-v2')
        self.assertEqual(note['sku'],'G2560a8c9')
        baseline = {'shop_price':10866, 'action':'offer', 'price':9779}
        out = pilot.apply(baseline, [], 12530, (.9,), note, md=self.md, env={}, now=2000000001)
        self.assertEqual(out['price'],9670)
        self.assertTrue(note['applied'])
        # New scope does not bypass goods, opening-probe, or first-price ceilings.
        self.assertIsNone(self.assign(quote=q, order=dict(self.order, kind='goods'), limit=12530))
        self.assertIsNone(self.assign(quote=q, limit=9600))
        self.assertIsNone(self.assign(quote=q, shop='unapproved-food-shop', limit=12530))

    def test_retry_assignment_cannot_be_reused_for_a_different_selected_sku(self):
        self.flag(scope='food-code90-five-shops-v2')
        self.assertIsNotNone(self.assign())
        q = dict(self.quote, sku='G2560a8c9', text=self.quote['text'].replace('G2108d9bc','G2560a8c9'))
        self.assertIsNone(self.assign(quote=q))
        self.assertIsNone(self.assign(shop='YAHO1212'))

    def test_real_quote_flows_through_the_actual_assignment_and_first_price_hook(self):
        import ast
        from pathlib import Path
        import oneshot
        self.flag(scope='food-code90-five-shops-v2')
        source = ast.parse(Path('market_buyer.py').read_text())
        hook = next(n for n in ast.walk(source) if isinstance(n,ast.If) and
                    any(isinstance(c,ast.Call) and isinstance(c.func,ast.Attribute) and
                        isinstance(c.func.value,ast.Name) and c.func.value.id=='buyer_food_open30' and
                        c.func.attr=='assign' for c in ast.walk(n)) and
                    not any(isinstance(c,ast.FunctionDef) for c in n.body))
        price = next(n for n in ast.walk(source) if isinstance(n,ast.FunctionDef) and n.name=='price_ladder')
        price.body = price.body[:2] + [ast.Return(ast.Name('d',ast.Load()))]
        opener = next(n for n in ast.walk(source) if isinstance(n,ast.FunctionDef) and n.name=='opening_probe_turn')
        q = dict(self.quote)
        order = dict(self.order, _food_ladder=None)
        env = dict(r3=True, order=order, shop={'name':'cheapest-in-town'}, q=q, key=q['sku'],
                   md=self.md, steps=(.9,), resume=True, probe_arm=None, discount_arm=None, limit=13230,
                   buyer_food_open30=pilot, log=lambda *args:None, oneshot=oneshot)
        # Only copied-source call sites execute; live market_buyer module/main is never imported.
        from unittest.mock import patch
        with patch.object(pilot.time,'time',return_value=2000000001):
            exec(compile(ast.fix_missing_locations(ast.Module([opener,hook,price],type_ignores=[])),
                         '<actual-food-hooks>','exec'),env)
            self.assertEqual(env['price_ladder'](None,q['text'],[])['price'],8732)
            self.assertTrue(order['_food_ladder']['applied'])
        with patch.object(pilot.time,'time',return_value=2000001800):
            self.assertEqual(env['price_ladder'](None,q['text'],[])['price'],8830)


class FoodOpen30JournalContract(FoodOpen30Guards):
    def corrupt(self, change):
        path = os.path.join(self.md, pilot.JOURNAL)
        with open(path) as f:
            journal = json.load(f)
        journal['orders'][self.order['order']] = change(journal['orders'][self.order['order']])
        with open(path, 'w') as f:
            json.dump(journal, f)

    def test_corrupt_candidate_cannot_lower_offer_outside_the_approved_policy(self):
        self.assign()
        self.corrupt(lambda row:dict(row, candidate=1))
        note = self.assign()
        self.assertIsNone(note)
        self.assertEqual(pilot.apply(self.baseline, [], 13230, (.9,), note,
                                    md=self.md, env={}, now=2000000001), self.baseline)

    def test_incomplete_reused_assignment_is_rejected_before_logging(self):
        self.assign()
        self.corrupt(lambda row:{k:row[k] for k in ('arm','ask','baseline')})
        self.assertIsNone(self.assign())

    def test_directory_fsync_is_required_and_its_failure_cannot_return_an_assignment(self):
        import stat
        from unittest.mock import patch
        real = os.fsync
        seen = []
        def require_directory(fd):
            directory = stat.S_ISDIR(os.fstat(fd).st_mode)
            seen.append(directory)
            if directory:
                raise OSError('directory persistence failed')
            return real(fd)
        with patch.object(os, 'fsync', side_effect=require_directory):
            self.assertIsNone(self.assign())
        self.assertIn(True, seen)
        # A prior unacknowledged rename also has to be made durable before reuse.
        with patch.object(os, 'fsync', side_effect=require_directory):
            self.assertIsNone(self.assign())
        self.assertIsNotNone(self.assign())


class FoodOpen88Test(unittest.TestCase):
    # The agreed seams are config/assign/apply and buyer.talk first send.
    setUp = FoodOpen30Guards.setUp
    assign = FoodOpen30Guards.assign
    flag = FoodOpen30Guards.flag
    SCOPE = 'food-code90-five-shops-088-v3'

    def test_explicit_88_scope_has_separate_policy_and_exact_integer_price(self):
        self.flag(scope=self.SCOPE)
        cfg = pilot.config(self.md, {}, 2000000001)
        self.assertIsNotNone(cfg)
        self.assertEqual(cfg['scope'], self.SCOPE)
        note = self.assign()
        self.assertIsNotNone(note)
        self.assertEqual(note['policy'], 'food-first-088')
        self.assertEqual(note['candidate'], 8634)
        self.assertEqual(note['baseline'], 8830)
        self.assertEqual(pilot.apply(self.baseline, [], 13230, (.9,), note,
            md=self.md, env={}, now=2000000001)['price'], 8634)

    def test_worked_prices_and_low_limit(self):
        for ask, limit, expected, baseline in ((9812, 13230, 8634, 8830),
                (10000, 13230, 8800, 9000), (9812, 8700, 8634, 8700)):
            with self.subTest(ask=ask, limit=limit):
                self.flag(scope=self.SCOPE)
                q = dict(self.quote, ask=ask)
                n = self.assign(quote=q, limit=limit)
                self.assertEqual((n['candidate'], n['baseline']), (expected, baseline))
                os.unlink(os.path.join(self.md, pilot.JOURNAL))
        self.assertIsNone(self.assign(limit=8600))

    def test_A_OFF_expiry_and_followup_remain_baseline(self):
        self.flag(scope=self.SCOPE)
        a = self.assign(order=dict(self.order, order='o-food30-2'))
        self.assertEqual(a['arm'], 'A')
        self.assertEqual(pilot.apply(self.baseline, [], 13230, (.9,), a,
            md=self.md, env={}, now=2000000001), self.baseline)
        b = self.assign()
        for now in (1999999999, 2000001800):
            self.assertEqual(pilot.apply(self.baseline, [], 13230, (.9,), b,
                md=self.md, env={}, now=now), self.baseline)
        self.assertEqual(pilot.apply(self.baseline, [('you offered', 8634)], 13230, (.9,), b,
            md=self.md, env={}, now=2000000001), self.baseline)
        self.flag(scope=self.SCOPE, enabled=False)
        self.assertEqual(pilot.apply(self.baseline, [], 13230, (.9,), b,
            md=self.md, env={}, now=2000000001), self.baseline)

    def test_scope_guards_and_durability_and_old_journal_are_fail_closed(self):
        from unittest.mock import patch
        self.flag(scope=self.SCOPE)
        for changes in (dict(order=dict(self.order, kind='goods')), dict(probing=True),
                dict(code_priced=False), dict(quote=dict(self.quote, ask=None)),
                dict(quote=dict(self.quote, text=self.quote['text']+' G2560a8c9')),
                dict(shop='not-allowed'), dict(order=dict(self.order, _food_ladder={'arm':'B'}))):
            with self.subTest(changes=changes):
                self.assertIsNone(self.assign(**changes))
        self.assertIsNone(pilot.assign(self.md, self.order, 'cheapest-in-town', self.quote,
            (.8,.9), True, probing=False, code_priced=True, limit=13230, env={}, now=2000000001))
        with patch.object(pilot.os, 'fsync', side_effect=OSError('fixture persistence failure')):
            self.assertIsNone(self.assign())
        self.assertFalse(os.path.exists(os.path.join(self.md, pilot.JOURNAL)))
        self.flag(scope=pilot.RETRY_SCOPE)
        old = self.assign()
        self.flag(scope=self.SCOPE)
        self.assertIsNone(self.assign())
        self.assertEqual(pilot.apply(self.baseline, [], 13230, (.9,), old,
            md=self.md, env={}, now=2000000001), self.baseline)
        self.flag(scope='unsupported-scope')
        self.assertIsNone(pilot.config(self.md, {}, 2000000001))

    def test_new_policy_cannot_reuse_tampered_price_or_policy(self):
        self.flag(scope=self.SCOPE)
        n = self.assign()
        for changes in (dict(candidate=1), dict(candidate=8732), dict(policy=pilot.POLICY),
                dict(scope=pilot.RETRY_SCOPE), dict(limit=8700)):
            self.assertEqual(pilot.apply(self.baseline, [], 13230, (.9,), dict(n, **changes),
                md=self.md, env={}, now=2000000001), self.baseline)

    def test_actual_caller_to_first_send_supports_new_policy_and_keeps_A_OFF_legacy(self):
        import ast
        from pathlib import Path
        from unittest.mock import patch
        import buyer
        import oneshot
        cases = (
            (self.SCOPE, 'o-food30-0', 2000000001, True, '8634', 'food-first-088'),
            (pilot.RETRY_SCOPE, 'o-food30-0', 2000000001, True, '8732', pilot.POLICY),
            (None, 'o-food30-0', 2000000001, True, '8732', pilot.POLICY),
            (self.SCOPE, 'o-food30-2', 2000000001, True, '8830', 'food-first-088'),
            (self.SCOPE, 'o-food30-0', 2000001800, True, '8830', None),
            (self.SCOPE, 'o-food30-0', 2000000001, False, '8830', None),
        )
        for scope, oid, now, enabled, expected, policy in cases:
            with self.subTest(scope=scope, order=oid, now=now, enabled=enabled):
                with tempfile.TemporaryDirectory() as md:
                    cfg = dict(self.cfg, enabled=enabled)
                    if scope is not None:
                        cfg['scope'] = scope
                    path = os.path.join(md, pilot.SWITCH)
                    with open(path, 'w') as f:
                        json.dump(cfg, f)
                    os.utime(path, (1999999990, 1999999990))
                    source = ast.parse(Path('market_buyer.py').read_text())
                    hook = next(n for n in ast.walk(source) if isinstance(n, ast.If) and
                        any(isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute) and
                            isinstance(c.func.value, ast.Name) and c.func.value.id == 'buyer_food_open30' and
                            c.func.attr == 'assign' for c in ast.walk(n)) and
                        not any(isinstance(c, ast.FunctionDef) for c in n.body))
                    price = next(n for n in ast.walk(source) if isinstance(n, ast.FunctionDef) and n.name == 'price_ladder')
                    # Keep the real opening hook and policy dispatch; unrelated later policies are off.
                    price.body = price.body[:2] + [ast.Return(ast.Name('d', ast.Load()))]
                    opener = next(n for n in ast.walk(source) if isinstance(n, ast.FunctionDef) and n.name == 'opening_probe_turn')
                    q = dict(self.quote, task={'id': 'isolated-food88-task'})
                    order = dict(self.order, order=oid, _food_ladder=None)
                    env = dict(r3=True, order=order, shop={'name': 'cheapest-in-town'}, q=q, key=q['sku'],
                               md=md, steps=(.9,), resume=True, probe_arm=None, discount_arm=None, limit=13230,
                               buyer_food_open30=pilot, log=lambda *args: None, oneshot=oneshot)
                    with patch.object(pilot.time, 'time', return_value=now):
                        exec(compile(ast.fix_missing_locations(ast.Module([opener, hook, price], type_ignores=[])),
                                     '<actual-food88-hooks>', 'exec'), env)
                        task = {'id': 'isolated-food88-task', 'status': {'state': 'TASK_STATE_INPUT_REQUIRED',
                            'message': {'parts': [{'text': q['text']}]}}}
                        with patch.object(buyer, 'send', return_value={'id': task['id'],
                                'status': {'state': 'TASK_STATE_CANCELED'}}) as send, \
                                patch.object(buyer, 'decide', side_effect=AssertionError('model forbidden')):
                            buyer.talk('fixture', task, 'food', 13230, '',
                                       {'history': [], 'agreed': 0, 'holds': 0}, ladder=env['price_ladder'])
                        self.assertEqual(send.call_args.args[1], expected)
                        self.assertEqual((order.get('_food_ladder') or {}).get('policy'), policy)

    def test_five_shops_accept_only_canonical_single_sku(self):
        self.flag(scope=self.SCOPE)
        for shop in sorted(pilot.RETRY_SHOPS):
            n = self.assign(shop=shop)
            self.assertEqual(n['shop'], shop)
            os.unlink(os.path.join(self.md, pilot.JOURNAL))
        self.assertIsNone(self.assign(quote=dict(self.quote, sku='Ginvalid')))


if __name__ == '__main__':
    unittest.main()
