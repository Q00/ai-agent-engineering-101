import json,tempfile,unittest,time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch,MagicMock,AsyncMock
from optimizer import db,choose_quote
from profit_strategy import opening
import seller_r3


class QuoteRecoveryTests(unittest.TestCase):
    def test_no_feasible_statistical_band_still_returns_profitable_quote(self):
        r=opening(dict(cost=200000,list_price=300000),dict(floor=202000,market_reference=100000),[])
        self.assertTrue(r['pricing_evidence']['fallback'])
        self.assertGreaterEqual(r['ask'],224000)

    def test_paid_premium_history_is_used_and_unquoted_legacy_failures_are_excluded(self):
        with tempfile.TemporaryDirectory() as directory:
            with db(directory) as c:
                for i in range(30):
                    c.execute('insert into trials(task,sku,kind,grade,at,ask,cost,reference,strategy) values(?,?,?,?,?,?,?,?,?)',
                              ('bad'+str(i),'x','goods','A',1000,120000,100000,200000,'response-ceiling-v1'))
                for i in range(10):
                    c.execute('insert into trials(task,sku,kind,grade,at,ask,cost,reference,strategy,responded,settled_at) values(?,?,?,?,?,?,?,?,?,?,?)',
                              ('good'+str(i),'x','goods','A',1000,250000,100000,200000,'premium-offensive-v1',1,1100))
            r=choose_quote(directory,'new',dict(unit='u',sku='x',kind='goods',grade='A',cost=100000,list_price=300000),dict(units=[]),dict(floor=101000,ask=200000,market_reference=200000),10000)
            self.assertNotIn(.6,r['pricing_evidence']['failed_bands'])
            self.assertGreater(r['ask'],110000)


class SellerRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_unsupported_statistics_do_not_reject_before_negotiation(self):
        with tempfile.TemporaryDirectory() as directory:
            md=Path(directory);unit=dict(unit='u',sku='G11111111',name='Mattress',category='bed',kind='goods',grade='A',cost=200000,list_price=300000,supplied_at=time.time(),expires_at=None)
            (md/'inventory.json').write_text(json.dumps(dict(round=3,balance=6000000,units=[unit])))
            updater=MagicMock();updater.requires_input=AsyncMock();updater.reject=AsyncMock()
            context=SimpleNamespace(current_task=SimpleNamespace(id='t',context_id='c'),message=None)
            plan=dict(ask=224000,floor=202000,market_reference=100000,pricing_evidence=dict(supported=False,fallback=True))
            with patch.object(seller_r3,'MARKET_DIR',md),patch.object(seller_r3,'TaskUpdater',return_value=updater),patch.object(seller_r3,'get_message_text',return_value='G11111111'),patch.object(seller_r3,'choose_quote',return_value=plan):
                await seller_r3.Seller().execute(context,None)
            updater.requires_input.assert_awaited_once()
            updater.reject.assert_not_called()
