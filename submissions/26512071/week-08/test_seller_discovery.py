"""seller_r3 unique partial-name discovery (RED first). Offline: fake Terra/Sol/signing, temp MARKET_DIR, no live market.
Fixture units follow the captured live inventory schema; the probe product mirrors the public probe
'I want to buy 젤라또 베개커버' (the implementation must not hard-code any name or SKU)."""
import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
import openai_harness
import seller_r3
import sol_policy

HERE = Path(__file__).resolve().parent
T = 1_800_000_000.0

PILLOW = 'ON 젤라또 베개커버 50x70 17 color'        # probe product
PILLOW_SKU = 'Ga2e3075f'
COTTON = 'ON 순면 베개커버 40x60'                     # same "베개커버" token, different SKU
COTTON_SKU = 'G1b2c3d4e'
SPRAY = '진회색스프레이 오공 락카 스프레이 325진회색 420ml'
SPRAY_SKU = 'G630a202e'
UNKNOWN_SKU = 'G0000beef'                              # SKU-shaped, in no fixture


def unit(uid, sku, name, grade='A', category='가구/침구>침구>침구세트', supplied_at=1794288185, expires_at=None,
         cost=9000, lst=15000, kind='goods'):
    return {'unit':uid, 'sku':sku, 'name':name, 'category':category, 'list_price':lst, 'kind':kind, 'cost':cost,
            'supplied_at':supplied_at, 'expires_at':expires_at, 'grade':grade,
            'cert':{'cert':{'unit':uid, 'sku':sku, 'shop':'fixture', 'kind':kind, 'supplied_at':supplied_at,
                            'expires_at':expires_at, 'grade':grade}, 'sig':'offline'}}


def stock(*units):
    return {'round':3, 'units':list(units)}


def base():
    """Two SKUs share the token 베개커버; one unrelated SKU. Categories never appear in the probe texts."""
    return [unit('p1', PILLOW_SKU, PILLOW), unit('c1', COTTON_SKU, COTTON),
            unit('s1', SPRAY_SKU, SPRAY, category='생활/건강>공구>스프레이도료', cost=2846, lst=3860)]


class FindUnitDiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.p = patch.object(seller_r3, 'now', return_value=T)
        self.p.start()

    def tearDown(self):
        self.p.stop()

    def find(self, text, units, taken=()):
        got = seller_r3.find_unit(text, taken, stock(*units))
        return None if got is None else got['unit']

    def test_module_is_the_candidate(self):
        self.assertEqual(Path(seller_r3.__file__).resolve().parent, HERE)

    # --- existing behavior (must already pass) -------------------------------------------------------------------
    def test_existing_sku_full_name_and_category_matches_unchanged(self):
        units = base()
        self.assertEqual(self.find(f'I want to buy {PILLOW_SKU}', units), 'p1')
        self.assertEqual(self.find(f'I want to buy {PILLOW}', units), 'p1')
        self.assertEqual(self.find(f'I want to buy {COTTON}', units), 'c1')
        self.assertEqual(self.find('I want to buy 스프레이도료', units), 's1')            # category tier
        # SKU beats full name of another product.
        self.assertEqual(self.find(f'I want to buy {PILLOW} ({COTTON_SKU})', units), 'c1')

    def test_category_match_still_wins_over_a_partial_fragment(self):
        # Existing tiers decide first: a text that already matched a category keeps that result.
        self.assertEqual(self.find('I want to buy 스프레이도료 젤라또 베개커버', base()), 's1')

    # --- new intended positives (RED until implemented) -----------------------------------------------------------
    def test_unique_partial_name_fragment_selects_that_sku(self):
        self.assertEqual(self.find('I want to buy 젤라또 베개커버', base()), 'p1')

    def test_unique_partial_fragment_works_for_another_product(self):
        # Not specific to the probe product: any sufficiently specific unique fragment.
        self.assertEqual(self.find('I want to buy 순면 베개커버', base()), 'c1')
        self.assertEqual(self.find('오공 락카 스프레이 있나요', base()), 's1')

    def test_longest_fragment_decides_the_sku_before_eligibility(self):
        # '젤라또 베개커버 50x70' only fits PILLOW; the shorter run would be ambiguous. Unique -> PILLOW.
        twin = unit('p9', 'G9f9f9f9f', 'ON 젤라또 베개커버 40x60')
        self.assertEqual(self.find('I want to buy 젤라또 베개커버 50x70', base() + [twin]), 'p1')

    def test_partial_match_keeps_strict_grade_expiry_taken_and_fefo_within_the_sku(self):
        units = [unit('p-exp', PILLOW_SKU, PILLOW, grade='expired', supplied_at=1),
                 unit('p-old', PILLOW_SKU, PILLOW, grade='A', supplied_at=2, expires_at=T),        # expires_at <= now
                 unit('p-c', PILLOW_SKU, PILLOW, grade='C', supplied_at=3),
                 unit('p-taken', PILLOW_SKU, PILLOW, grade='A', supplied_at=4),
                 unit('p-a1', PILLOW_SKU, PILLOW, grade='A', supplied_at=5),
                 unit('p-a2', PILLOW_SKU, PILLOW, grade='A', supplied_at=6),
                 unit('p-fefo', PILLOW_SKU, PILLOW, grade='A', supplied_at=9, expires_at=T + 3600),
                 unit('c1', COTTON_SKU, COTTON)]
        text = 'I want to buy 젤라또 베개커버'
        self.assertEqual(self.find(text, units, taken={'p-taken'}), 'p-fefo')          # FEFO: finite expiry first
        units = [u for u in units if u['unit'] != 'p-fefo']
        self.assertEqual(self.find(text, units, taken={'p-taken'}), 'p-c')             # no grade named: oldest live
        self.assertEqual(self.find(text + ' A등급', units, taken={'p-taken'}), 'p-a1')  # grade A strict, oldest A
        self.assertEqual(self.find(text + ' grade A', units, taken={'p-taken', 'p-a1'}), 'p-a2')

    # --- fail-closed guards (some already pass today; they must keep passing) ---------------------------------------
    def test_partial_match_never_substitutes_a_grade_or_another_sku(self):
        units = [unit('p-c', PILLOW_SKU, PILLOW, grade='C'), unit('c-a', COTTON_SKU, COTTON, grade='A')]
        self.assertIsNone(self.find('I want to buy 젤라또 베개커버 grade A', units))
        self.assertIsNone(self.find('I want to buy 젤라또 베개커버', units, taken={'p-c'}))

    def test_ambiguous_fragment_across_different_skus_rejects(self):
        self.assertIsNone(self.find('I want to buy 베개커버', base()))                 # PILLOW and COTTON
        twin = unit('p9', 'G9f9f9f9f', 'ON 젤라또 베개커버 40x60')
        self.assertIsNone(self.find('I want to buy 젤라또 베개커버', base() + [twin]))
        # Fragments of two different products in one text: not one unique SKU.
        self.assertIsNone(self.find('I want to buy 젤라또 베개커버 오공 락카', base()))

    def test_short_generic_or_non_word_fragments_reject(self):
        for text in ('I want to buy 젤라또',          # single short token, even if unique
                     'I want to buy color',            # single generic word, unique
                     'I want to buy ON',
                     'I want to buy 커버',             # inside a token only
                     'I want to buy 라또 베개커',       # not on token boundaries
                     'I want to buy 50x70 17'):        # no real word: digits must not pick a product
            self.assertIsNone(self.find(text, base()), text)

    def test_explicit_unknown_sku_never_falls_back_to_a_partial_name(self):
        self.assertIsNone(self.find(f'I want to buy 젤라또 베개커버 {UNKNOWN_SKU}', base()))
        self.assertIsNone(self.find(f'I want to buy 젤라또 베개커버 ({UNKNOWN_SKU})', base()))

    def test_explicit_unknown_sku_beats_full_name_and_category(self):
        self.assertIsNone(self.find(f'I want to buy {PILLOW} {UNKNOWN_SKU}', base()))
        self.assertIsNone(self.find(f'I want to buy 스프레이도료 ({UNKNOWN_SKU})', base()))
        # A known SKU next to an unknown one is still an unknown request.
        self.assertIsNone(self.find(f'I want to buy {PILLOW_SKU} {UNKNOWN_SKU}', base()))

    def test_two_different_explicit_skus_reject(self):
        self.assertIsNone(self.find(f'I want to buy {PILLOW_SKU} {COTTON_SKU}', base()))
        self.assertIsNone(self.find(f'I want to buy {PILLOW} ({PILLOW_SKU}, {SPRAY_SKU})', base()))

    def test_overlapping_fragments_of_two_products_reject(self):
        # spray2 shares the fragment '락카 스프레이' with SPRAY; its category never appears in these texts.
        spray2 = unit('s2', 'G7e7e7e7e', '락카 스프레이 무광 블랙 420ml', category='생활/건강>공구>도장용품')
        self.assertIsNone(self.find('I want to buy 락카 스프레이', base() + [spray2]))
        # Nested is fine: '오공 락카 스프레이' contains every fragment spray2 has here.
        self.assertEqual(self.find('I want to buy 오공 락카 스프레이', base() + [spray2]), 's1')
        # Not nested: SPRAY via '오공 락카 스프레이', spray2 via '스프레이 무광 블랙' -> two products named.
        self.assertIsNone(self.find('I want to buy 오공 락카 스프레이 무광 블랙', base() + [spray2]))

    def test_inquiry_words_alone_never_pick_a_product(self):
        lamp = unit('l1', 'G5d5d5d5d', 'Lamp for the desk', category='가구>조명')
        self.assertIsNone(self.find('how much for the item', base() + [lamp]))
        self.assertEqual(self.find('I want to buy lamp for the desk please', base() + [lamp]), 'l1')

    def test_explicit_unknown_sku_in_any_case_never_falls_back(self):
        for sku in (UNKNOWN_SKU.lower(), UNKNOWN_SKU.upper(), 'g0000BEEF'):
            self.assertIsNone(self.find(f'I want to buy 젤라또 베개커버 {sku}', base()), sku)
            self.assertIsNone(self.find(f'I want to buy {PILLOW} ({sku})', base()), sku)
            self.assertIsNone(self.find(f'I want to buy 스프레이도료 {sku}', base()), sku)
        self.assertEqual(self.find(f'I want to buy {PILLOW_SKU.upper()}', base()), 'p1')   # a known SKU in any case

    def test_oversized_text_gets_no_partial_match_but_existing_tiers_remain(self):
        long_chars = 'I want to buy 젤라또 베개커버 ' + 'x' * seller_r3.PARTIAL_MAX_CHARS
        many_words = 'I want to buy 젤라또 베개커버' + ' please' * seller_r3.PARTIAL_MAX_WORDS
        self.assertLess(len(many_words), seller_r3.PARTIAL_MAX_CHARS)
        for text in (long_chars, many_words):
            self.assertIsNone(self.find(text, base()))
            self.assertIsNone(seller_r3.partial_sku(text, base()))
            pad = text[len('I want to buy 젤라또 베개커버'):]
            self.assertEqual(self.find(f'I want to buy {PILLOW_SKU}' + pad, base()), 'p1')       # explicit SKU
            self.assertEqual(self.find(f'I want to buy {COTTON}' + pad, base()), 'c1')           # full name
            self.assertEqual(self.find('I want to buy 스프레이도료' + pad, base()), 's1')         # category
        # Exactly at the word bound the partial match still works.
        at_bound = 'I want to buy 젤라또 베개커버' + ' please' * (seller_r3.PARTIAL_MAX_WORDS - 6)
        self.assertEqual(self.find(at_bound, base()), 'p1')

    def test_duplicate_units_are_scanned_once_per_sku_and_name(self):
        dupes = [unit(f'p{i}', PILLOW_SKU, PILLOW, supplied_at=i) for i in range(300)]
        units = base() + dupes + [unit(f'c{i}', COTTON_SKU, COTTON) for i in range(300)]
        with patch.object(seller_r3.re, 'findall', wraps=re.findall) as fa:
            got = seller_r3.partial_sku('I want to buy 젤라또 베개커버', units)
        self.assertEqual(got, PILLOW_SKU)
        self.assertEqual(fa.call_count, 1 + 3)            # the text once + one per distinct (sku, name), not per unit
        self.assertEqual(self.find('I want to buy 젤라또 베개커버', units), 'p0')   # selection itself unchanged (oldest)

    def test_unavailable_explicit_sku_is_never_substituted(self):
        units = [unit('p-c', PILLOW_SKU, PILLOW, grade='C'), unit('p-x', PILLOW_SKU, PILLOW, expires_at=T - 1),
                 unit('c1', COTTON_SKU, COTTON), unit('p9', 'G9f9f9f9f', 'ON 젤라또 베개커버 40x60')]
        self.assertIsNone(self.find(f'I want to buy 젤라또 베개커버 {PILLOW_SKU} grade A', units))
        self.assertIsNone(self.find(f'I want to buy 젤라또 베개커버 ({PILLOW_SKU})', units, taken={'p-c'}))
        # Unique fragment names an SKU whose units are all unavailable: no_stock, never the 40x60 twin.
        self.assertIsNone(self.find('I want to buy 젤라또 베개커버 50x70', units, taken={'p-c'}))


class DiscoveryRouteTests(unittest.IsolatedAsyncioTestCase):
    """Through build(): the real A2A route, card and decision journal. Partial-name tasks stay on the model path."""

    async def asyncSetUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.prompts = []

        def ask(prompt):
            self.prompts.append(prompt)
            openai_harness.LAST_CALL.clear()
            return None, 'null'   # no model decision -> deterministic fallback, still the non-fast path
        self.patches = [patch.object(seller_r3, 'MARKET_DIR', self.tmp),
                        patch.object(seller_r3, 'ask_json', side_effect=ask),
                        patch.object(seller_r3, 'sign', side_effect=lambda rec: dict(rec, sig_seller='offline')),
                        patch.object(seller_r3, 'now', return_value=T),
                        patch.object(seller_r3, 'FASTPATH', True),
                        patch.object(sol_policy, 'active', return_value=False),
                        patch.object(sol_policy, 'maybe_refresh_async', return_value=False)]
        for p in self.patches:
            p.start()
        (self.tmp/'inventory.json').write_text(json.dumps(stock(*base()), ensure_ascii=False), encoding='utf-8')
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=seller_r3.build(9999)))

    async def asyncTearDown(self):
        await self.client.aclose()
        for p in reversed(self.patches):
            p.stop()
        self._tmp.cleanup()

    async def send(self, text):
        msg = {'role':'ROLE_USER', 'messageId':f'm-{text}-{len(self.prompts)}', 'parts':[{'text':text}]}
        r = await self.client.post('http://fixture/', headers={'A2A-Version':'1.0'},
                                   json={'jsonrpc':'2.0', 'id':'1', 'method':'SendMessage', 'params':{'message':msg}})
        return r.json()['result']['task']

    def rows(self):
        f = self.tmp/'decisions.jsonl'
        return [json.loads(x) for x in f.read_text(encoding='utf-8').splitlines()] if f.exists() else []

    @staticmethod
    def reply(task):
        return ' '.join(p.get('text', '') for p in task['status']['message']['parts'])

    async def card(self):
        r = await self.client.get('http://fixture/.well-known/agent-card.json')
        self.assertEqual(r.status_code, 200)
        return r.json()

    async def test_probe_quotes_the_unique_sku_on_the_model_path(self):
        task = await self.send('I want to buy 젤라또 베개커버')
        rows = self.rows()
        self.assertEqual([r['event'] for r in rows], ['first_quote'], rows)
        self.assertEqual((rows[0]['sku'], rows[0]['unit']), (PILLOW_SKU, 'p1'))
        self.assertNotEqual(rows[0]['decided_by'], 'deterministic_fastpath')   # fast-path grammar not expanded
        self.assertEqual(len(self.prompts), 1)
        self.assertIn(PILLOW, self.prompts[0])
        self.assertIn(PILLOW, self.reply(task))
        self.assertIn(str(rows[0]['quote']), self.reply(task))

    async def test_route_rejects_ambiguous_and_unknown_sku_without_a_quote(self):
        for text in ('I want to buy 베개커버', f'I want to buy 젤라또 베개커버 {UNKNOWN_SKU}', 'I want to buy 젤라또'):
            task = await self.send(text)
            self.assertIn('none of that in stock', self.reply(task), text)
        self.assertEqual([r['event'] for r in self.rows()], ['no_stock'] * 3)
        self.assertEqual(self.prompts, [])

    async def test_card_mentions_unique_partial_name_discovery_without_new_claims(self):
        card = await self.card()
        desc = card['description']
        self.assertRegex(desc, r'(?i)part(?:ial)?\s+(?:of\s+(?:its|the|a)\s+)?(?:product\s+)?name')
        self.assertRegex(desc, r'(?i)\bunique|only one\b')
        self.assertNotRegex(desc, r'(?i)\bfast(?:er)?\b|\bspeed|\bms\b|latency|all goods|every (?:item|product)')
        self.assertIn('Goods grade A/B: 72 h warranty', desc)                    # warranty wording unchanged
        self.assertIn('otherwise no warranty (0 h)', desc)
        examples = [e for s in card['skills'] for e in s.get('examples', [])]
        self.assertIn('I want to buy 종가 알뜰맘 포기김치 10kg (G00001)', examples)  # existing example kept
        self.assertFalse([e for e in examples if re.search(r'(?i)discount|warrant|coupon', e)], examples)


if __name__ == '__main__':
    unittest.main()
