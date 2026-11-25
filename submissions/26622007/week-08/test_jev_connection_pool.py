import threading
import unittest
import httpx
from jev_connection_pool import JevConnectionPool

class PoolTest(unittest.TestCase):
    def test_reuses_same_thread_without_cookies_or_retry(self):
        created=[];seen=[]
        def factory():
            def handle(req):
                seen.append((req.headers.get("cookie"),req.content))
                return httpx.Response(200,headers={"set-cookie":"prior=one"},json={"ok":True})
            c=httpx.Client(transport=httpx.MockTransport(handle));created.append(c);return c
        pool=JevConnectionPool(factory)
        try:
            for _ in range(2):
                self.assertEqual(pool.post("https://example.test/",json={"same":1},timeout=.7).status_code,200)
            self.assertEqual(len(created),1)
            self.assertEqual(len(seen),2)
            self.assertEqual(seen[0],seen[1])
            self.assertIsNone(seen[1][0])
        finally:pool.close()
        self.assertTrue(created[0].is_closed)
    def test_thread_clients_are_isolated(self):
        created=[]
        def factory():
            c=httpx.Client(transport=httpx.MockTransport(lambda req:httpx.Response(200)));created.append(c);return c
        pool=JevConnectionPool(factory)
        try:
            pool.post("https://example.test/",timeout=.7)
            t=threading.Thread(target=lambda:pool.post("https://example.test/",timeout=.7));t.start();t.join()
            self.assertEqual(len(created),2)
        finally:pool.close()
    def test_failure_is_not_retried(self):
        calls=[]
        def fail(req):calls.append(req);raise httpx.ReadTimeout("fixture")
        pool=JevConnectionPool(lambda:httpx.Client(transport=httpx.MockTransport(fail)))
        try:
            with self.assertRaises(httpx.ReadTimeout):pool.post("https://example.test/",timeout=.7)
            self.assertEqual(len(calls),1)
        finally:pool.close()
