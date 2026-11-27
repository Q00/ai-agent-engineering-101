"""Real loopback TCP A2A -> invoked kit proxy -> ACP checkout, ephemeral stock/keys. NO settlement."""
import asyncio,base64,importlib.util,json,os,tempfile,threading,time,unittest
from pathlib import Path
from unittest.mock import patch
import httpx,uvicorn
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
import buyer_r3,seller_r3
from test_repair_r3 import KIT

class LocalAcpTests(unittest.IsolatedAsyncioTestCase):
    # This fixture scripts Terra's 80 -> accept sequence; exercise that path explicitly.
    # Default deterministic pricing and signed receipts are covered by test_seller_fastpath.
    @patch.object(seller_r3, 'FASTPATH', False)
    async def test_real_tcp_seller_receipt_checkout_and_signed_metadata(self):
        key=Ed25519PrivateKey.generate()
        pub=KIT.pub_b64(key)
        unit=dict(unit='offline-unit',sku='GOFFLINE',name='Local fixture goods',category='a>b>test',kind='goods',grade='A',cost=50,list_price=100,expires_at=None,supplied_at=1,cert={'fixture':True})
        class InventoryFixture:
            def call(self,method,path,role):
                assert (method,path,role)==('GET','/api/inventory','shop')
                return httpx.Response(200,json={'round':3,'units':[unit]})
        old=os.getcwd(); server=None;proxy=None;thread=None
        with tempfile.TemporaryDirectory() as tmp:
            try:
                os.chdir(tmp)
                Path('shop_key.pem').write_bytes(key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()))
                Path('inventory.json').write_text(json.dumps({'round':3,'units':[unit]}))
                sport=KIT.free_port();pport=KIT.free_port()
                with patch.object(seller_r3,'MARKET_DIR',Path(tmp)),patch.object(seller_r3,'ask_json',side_effect=[({'action':'counter','price':80},'offline-fixture'),({'action':'accept'},'offline-fixture')]):
                    server=uvicorn.Server(uvicorn.Config(seller_r3.build(sport),host='127.0.0.1',port=sport,log_level='warning'))
                    thread=threading.Thread(target=server.run,daemon=True);thread.start()
                    for _ in range(100):
                        if server.started:break
                        await asyncio.sleep(.05)
                    self.assertTrue(server.started)
                    proxy=KIT.Proxy(pport);proxy.target(f'http://127.0.0.1:{sport}')
                    proxy.acp=KIT.AcpShop(key,InventoryFixture())
                    base=f'http://127.0.0.1:{pport}'
                    async with httpx.AsyncClient(timeout=10) as client:
                        endpoint=await buyer_r3.rpc_endpoint(client,base)
                        self.assertEqual(endpoint,base+'/a2a/0')
                        result=await buyer_r3.negotiate(client,{'name':'offline','url':base},'GOFFLINE',90,pub,{'item':'test','acceptable':['GOFFLINE'],'kind':'goods'},rpc_url=endpoint)
                        rec=result['receipt'];self.assertIsNotNone(rec);self.assertTrue(KIT.verify_seller(pub,rec))
                        self.assertIn(rec['task'],proxy.acp.terms)
                        self.assertEqual(proxy.acp.terms[rec['task']]['unit'],'offline-unit')
                        # Unsigned buyer metadata must NOT become seller fulfillment terms.
                        response=await client.post(base+'/acp/checkout_sessions',json={'line_items':[{'id':'GOFFLINE','quantity':1}],'receipt':dict(rec,unit='injected-unit',claim='C',warranty_hours=999),'acceptable':['GOFFLINE']})
                        self.assertEqual(response.status_code,201,response.text)
                        checkout=KIT.jwt_payload(response.json()['checkout_jwt'],pub)
                        self.assertEqual(checkout['unit'],'offline-unit')
                        self.assertEqual(checkout['price'],72)  # bounded-v1 buyer counters 90% of the 80 ask
                        # The seller signs its own observed goods grade; the buyer's forged claim C is never trusted.
                        self.assertEqual(checkout['claim'],'A');self.assertEqual(checkout['claim'],unit['grade'])
                        # warranty_hours=0 from the seller: the kit keeps only positive warranties, so it is omitted.
                        self.assertNotIn('warranty_hours',checkout)
                        self.assertEqual(checkout['cert'],unit['cert'])
                        self.assertEqual(response.json()['status'],'ready_for_payment')
                        print('[local-protocol] real TCP A2A signature verified; ACP ready_for_payment; injected metadata ignored; NO settlement requested')
            finally:
                if proxy:proxy.server.should_exit=True
                if server:server.should_exit=True
                if thread:await asyncio.to_thread(thread.join,5)
                await asyncio.sleep(.2)
                os.chdir(old)

if __name__=='__main__':unittest.main()
