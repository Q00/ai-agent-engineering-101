import json
import tempfile
import unittest

import httpx

from market_policy import LIMIT, Monitor


class InputFreshnessTests(unittest.TestCase):
    def test_poll_accepts_bounded_official_inputs(self):
        def handler(request):
            if request.url.path=='/api/board':return httpx.Response(200,json={'round':3,'players':[]})
            if request.url.path=='/api/shops':return httpx.Response(200,json={'shops':[]})
            return httpx.Response(200,text='official content')
        with tempfile.TemporaryDirectory() as tmp, httpx.Client(transport=httpx.MockTransport(handler)) as client:
            m=Monitor(tmp,'https://market.test',client);out=m.poll()
            self.assertTrue(out['mutations_allowed'])
            self.assertTrue(all(out['fresh'].values()));m.close()

    def test_malformed_or_oversized_does_not_replace_last_good(self):
        mode={'bad':False}
        def handler(request):
            if request.url.path=='/api/board':
                return httpx.Response(200,content=(b'x'*(LIMIT+1) if mode['bad'] else json.dumps({'round':3}).encode()))
            if request.url.path=='/api/shops':return httpx.Response(200,json={'shops':[]})
            return httpx.Response(200,text='official')
        with tempfile.TemporaryDirectory() as tmp, httpx.Client(transport=httpx.MockTransport(handler)) as client:
            m=Monitor(tmp,'https://market.test',client);m.poll()
            before=m.control.db.execute("SELECT content_hash FROM input_snapshots WHERE source='board'").fetchone()[0]
            mode['bad']=True;m.poll()
            after=m.control.db.execute("SELECT content_hash FROM input_snapshots WHERE source='board'").fetchone()[0]
            self.assertEqual(before,after)
            event=m.control.db.execute("SELECT body FROM runtime_events WHERE kind='official_input_error' ORDER BY id DESC").fetchone()[0]
            self.assertNotIn('xxx',event);m.close()


if __name__=='__main__':unittest.main()
