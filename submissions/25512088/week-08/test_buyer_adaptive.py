import tempfile
import time
import unittest

import httpx

from adaptive_control import Control
from buyer_memory import Memory
from buyer_r3 import Buyer


class BuyerAdaptiveTests(unittest.TestCase):
    def test_offer_is_idempotent_and_unknown_is_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory=Memory(tmp);control=Control(tmp)
            order={'order':'o1','expires':time.time()+100,'budget':100,'kind':'goods','acceptable':['S']}
            buyer=Buyer(order,memory,httpx.Client(),{'S':{'list_price':100}},control=control)
            session={'shop':{'url':'https://s','name':'s'},'sku':'S','task':'t'}
            buyer.send=lambda _s,_text: {'id':'t','status':{'state':'TASK_STATE_INPUT_REQUIRED'}}
            task=buyer.send_offer(session,50)
            self.assertIn('INPUT_REQUIRED',task['status']['state'])
            row=control.db.execute('SELECT state FROM mutations').fetchone()
            self.assertEqual(row[0],'CONFIRMED')
            with self.assertRaises(RuntimeError):buyer.send_offer(session,50)
            self.assertEqual(control.db.execute('SELECT COUNT(*) FROM mutations').fetchone()[0],1)
            buyer.c.close();memory.close();control.close()

    def test_network_loss_marks_uncertain(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory=Memory(tmp);control=Control(tmp)
            order={'order':'o2','expires':time.time()+100,'budget':100,'kind':'goods','acceptable':['S']}
            buyer=Buyer(order,memory,httpx.Client(),{'S':{'list_price':100}},control=control)
            session={'shop':{'url':'https://s','name':'s'},'sku':'S','task':'t'}
            buyer.send=lambda *_: (_ for _ in ()).throw(httpx.ReadTimeout('lost'))
            with self.assertRaises(httpx.ReadTimeout):buyer.send_offer(session,50)
            self.assertEqual(control.db.execute('SELECT state FROM mutations').fetchone()[0],'UNCERTAIN')
            buyer.c.close();memory.close();control.close()


if __name__=='__main__':unittest.main()
