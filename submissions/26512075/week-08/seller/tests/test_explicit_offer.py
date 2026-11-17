import logging
import unittest
from unittest.mock import AsyncMock

from offer_validation import explicit_offer, negotiate_followup


class ExplicitOfferTests(unittest.IsolatedAsyncioTestCase):
    def test_clear_unconditional_formats(self):
        for text in ('2589', '2,589', '2589에 살게요', '2,589크레딧에 구매하겠습니다.',
                     'I accept 2589', 'I offer 2,589 credits.', 'I want to buy it at 2589',
                     '{"action":"buy","price":2589,"sku":"G67639226","qty":1,"currency":"credits"}',
                     '```json\n{"action":"accept","price":2589}\n```'):
            with self.subTest(text=text):
                self.assertEqual(explicit_offer(text, 'G67639226'), 2589)

    def test_ambiguous_or_conditional_messages_do_not_accept(self):
        for text in ('yes', 'I do not accept 2589', 'I accept 2589 if shipping is free',
                     '2589 or 2500', 'I accept -2589', 'I accept 2589.50',
                     'I accept 0', 'buy G2589', '{"price":2589}',
                     '{"action":"accept","price":2589,"sku":"wrong"}',
                     '{"action":"accept","price":2589,"qty":2}',
                     '{"action":"accept","price":2589,"qty":true}',
                     '{"action":"accept","price":2589,"warranty_hours":24}',
                     '{"action":"accept","price":2589,"currency":"USD"}',
                     '{"action":"accept","price":2589,"price":2500}',
                     '{"action":"accept","price":true}',
                     '{"action":"accept","price":"2589"}',
                     '{"action":"accept","price":2589.0}'):
            with self.subTest(text=text):
                self.assertIsNone(explicit_offer(text, 'G67639226'))

    async def test_floor_accepts_explicit_price_but_keeps_lower_offers_below_floor(self):
        model = AsyncMock(side_effect=AssertionError('model must not run at floor'))
        for text, action, price in [('2589에 살게요', 'accept', 2589),
                                    ('I accept 2500', 'offer', 2589),
                                    ('{"action":"buy","price":2589}', 'accept', 2589),
                                    ('I accept 2589 if warranty is included', 'clarify', None)]:
            choice, _ = await negotiate_followup({'itemId': 'G67639226'}, text,
                                                 2589, 2589, model, logging.getLogger('test'))
            self.assertEqual((choice['action'], choice.get('price')), (action, price))
        model.assert_not_awaited()
