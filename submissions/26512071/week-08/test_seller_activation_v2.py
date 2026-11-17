"""Safety regression: editing a live subprocess hook must not activate a pilot."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

class ActivationGateTests(unittest.TestCase):
    def test_cli_without_explicit_approval_never_buys(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)
            (p/'inventory.json').write_text(json.dumps({'round':3,'start':5000000,'balance':5000000,'units':[]}))
            (p/'catalog.json').write_text(json.dumps({'X':{'kind':'goods','category':'test','list_price':10000}}))
            (p/'public-settlements.json').write_text(json.dumps({'fetched_at':9999999999,'attempted_at':9999999999,'rows':[]}))
            r = subprocess.run([sys.executable, str(Path(__file__).with_name('restock_r3.py')), '--state',d],capture_output=True,text=True,encoding='utf-8',timeout=20,stdin=subprocess.DEVNULL)
            self.assertEqual(r.returncode,0,r.stderr)
            out=json.loads(r.stdout.splitlines()[-1]); self.assertEqual(out['buy'],[]); self.assertIsInstance(out['invocation'],str)

    def test_policy_name_alone_cannot_activate_unreviewed_source(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)
            (p/'inventory.json').write_text(json.dumps({'round':3,'start':5000000,'balance':5000000,'units':[]}))
            (p/'catalog.json').write_text(json.dumps({'X':{'kind':'goods','category':'test','list_price':10000}}))
            (p/'public-settlements.json').write_text(json.dumps({'fetched_at':9999999999,'attempted_at':9999999999,'rows':[]}))
            (p/'seller-v2-approved.json').write_text(json.dumps({'policy_version':'bounded-v2','approved':True,'source_sha256':{}}))
            r=subprocess.run([sys.executable,str(Path(__file__).with_name('restock_r3.py')),'--state',d],capture_output=True,text=True,encoding='utf-8',timeout=20,stdin=subprocess.DEVNULL)
            self.assertEqual(r.returncode,0,r.stderr)
            out=json.loads(r.stdout.splitlines()[-1]); self.assertEqual(out['buy'],[]); self.assertIsInstance(out['invocation'],str)

if __name__ == '__main__': unittest.main()
