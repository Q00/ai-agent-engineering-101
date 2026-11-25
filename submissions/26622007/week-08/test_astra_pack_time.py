import datetime
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import meta_harness as mh

class PackTimeTest(unittest.TestCase):
    def test_observed_time_beats_checkout_time_and_skips_current(self):
        with tempfile.TemporaryDirectory() as d, patch.object(mh, 'ROOT', d):
            folder=Path(d,'meta','evidence');folder.mkdir(parents=True)
            for name,t,mtime in [('astra-1105-093817',100,900),('astra-1106-230016',200,400),('astra-1107-113730',300,500)]:
                f=folder/(name+'.json')
                f.write_text(json.dumps({'time':datetime.datetime.fromtimestamp(t,datetime.timezone.utc).isoformat(),'log_counts':{'lines':10,'sold':8}}))
                os.utime(f,(mtime,mtime))
            r=mh.since_last({'log_counts':{'lines':20,'sold':3}},'astra',now=300)
            self.assertEqual(r['previous'],'astra-1106-230016.json')
            self.assertIsNone(r['log_counts']['sold'])
            self.assertEqual(r['log_count_gaps'],['sold'])

    def test_missing_rejected_pack_uses_nearby_observation_and_names_gap(self):
        with tempfile.TemporaryDirectory() as d, patch.object(mh, 'ROOT', d):
            folder=Path(d,'meta','evidence');folder.mkdir(parents=True)
            stamp=lambda t:datetime.datetime.fromtimestamp(t,datetime.timezone.utc).isoformat()
            for name,t in [('astra-1105-093817',100),('terra-1106-225000',190)]:
                (folder/(name+'.json')).write_text(json.dumps({'time':stamp(t)}))
            Path(d,'meta','changes.jsonl').write_text(json.dumps({'cycle':'astra-1106-230016','time':stamp(200)})+'\n')
            r=mh.since_last({},'astra',now=300)
            self.assertEqual(r['previous'],'terra-1106-225000.json')
            self.assertEqual(r['missing_previous_cycle'],'astra-1106-230016')
