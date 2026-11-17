import unittest
from unittest.mock import patch
import seller
import market_buyer

class Neutrality(unittest.TestCase):
    def test_card_never_claims_instructor_approval(self):
        with patch.multiple(seller, MARKET=True, ALLIES='unverified-partner'):
            app=seller.build(9999)
            # Actual descriptor through its route's bound card is tested over HTTP below.
            import asyncio, httpx
            async def card():
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as c:
                    return (await c.get('/.well-known/agent-card.json')).json()
            text=asyncio.run(card())['description'].lower()
            self.assertNotIn('approved',text)
            self.assertNotIn('alliance',text)
            self.assertNotIn('deals here count',text)

    def test_buyer_does_not_prefer_configured_ally(self):
        shops=[dict(name='ally',url='http://ally'),dict(name='model-choice',url='http://other')]
        with patch.multiple(market_buyer,ALLIES=['ally'],ALLY_RATE=1,EXPLORE=0), \
                patch.object(market_buyer,'board_stats',return_value={}), \
                patch.object(market_buyer,'harness',return_value='{"shop":"model-choice","reason":"Neutral choice"}'):
            chosen,_=market_buyer.pick(shops,'lamp',120,[])
            self.assertEqual(chosen['name'],'model-choice')

if __name__=='__main__': unittest.main()
