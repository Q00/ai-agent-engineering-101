import json
import tempfile
import unittest
from pathlib import Path
from ledger_window import window


class LedgerWindowTests(unittest.TestCase):
    def test_append_replay_partial_record_and_time_expiry(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'ledger.jsonl'
            own=dict(seq=1,ts=10000,data=dict(shop='ours'))
            public=dict(seq=2,ts=90000,data=dict(shop='other'))
            p.write_text(json.dumps(own)+'\n'+json.dumps(public)+'\n',encoding='utf-8')
            self.assertEqual([e['seq'] for e in window(p,90001,{'ours'})],[1,2])
            with p.open('a',encoding='utf-8') as f:
                f.write(json.dumps(public)+'\n'+json.dumps(dict(public,seq=3)))
            self.assertEqual(len(window(p,90002,{'ours'})),2)
            with p.open('a',encoding='utf-8') as f:f.write('\n')
            self.assertEqual(len(window(p,90003,{'ours'})),3)
            self.assertEqual(window(p,200000,{'ours'}),[])

    def test_truncation_rebuilds_index(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'ledger.jsonl'
            p.write_text(json.dumps(dict(seq=100,ts=100,data=dict(shop='other')))+'\n')
            self.assertEqual(len(window(p,101,set())),1)
            p.write_text('{}\n')
            self.assertEqual(window(p,101,set()),[])

    def test_newer_records_are_not_lost_when_scan_overlaps_writer(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'ledger.jsonl'
            p.write_text(json.dumps(dict(seq=1,ts=105,data=dict(shop='other')))+'\n')
            self.assertEqual(window(p,100,set()),[])
            self.assertEqual(len(window(p,110,set())),1)
