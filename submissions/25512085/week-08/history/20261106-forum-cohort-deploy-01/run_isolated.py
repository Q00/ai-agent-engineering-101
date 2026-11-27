"""Offline runner: no live inputs, external sockets or out-of-evidence writes."""
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import unittest
import uuid

OUT = Path(__file__).resolve().parent
REPO = OUT.parents[4]
mode, label = sys.argv[1:3]
tree = OUT/'evidence'/label
cwd = tree/'submissions/25512085/week-08'
scratch = OUT/'evidence'/('scratch-'+label+'-'+mode+'-'+uuid.uuid4().hex[:10])
scratch.mkdir(exist_ok=False)
os.environ['TEMP'] = os.environ['TMP'] = str(scratch)
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
tempfile.tempdir = str(scratch)
original_mkdtemp = tempfile.mkdtemp
def isolated_mkdtemp(suffix=None,prefix=None,dir=None):
    return original_mkdtemp(suffix=suffix,prefix=prefix,dir=str(scratch))
tempfile.mkdtemp = isolated_mkdtemp

def audit(event,args):
    if event in ('socket.connect','socket.sendto','socket.getaddrinfo'):
        raise AssertionError('external network forbidden')
    if event == 'open' and isinstance(args[0],(str,bytes,os.PathLike)):
        path = Path(os.fsdecode(args[0])).resolve()
        if REPO in path.parents and OUT not in path.parents and REPO/'.venv' not in path.parents:
            raise AssertionError('live repository access forbidden: '+path.name)
        flags = args[2]
        writing = isinstance(flags,int) and flags & (os.O_WRONLY|os.O_RDWR|os.O_CREAT|os.O_TRUNC|os.O_APPEND)
        if writing and OUT not in path.parents:
            raise AssertionError('write outside evidence forbidden')
sys.addaudithook(audit)
os.chdir(cwd)
sys.path.insert(0,str(cwd))
if mode == 'focused':
    names = ['test_forum_intel_offline','test_forum_quality_evidence_offline','test_seller_cash_offline','test_sku_break_rates_offline','test_hub_compaction_offline','test_buyer_gap_counter_offline','test_buyer_savings_ranking_offline','test_buyer_price_offline','test_buyer_retry_offline','test_buyer_failure_reason_offline','test_buyer_stockout_cooldown_offline','test_goods_selection_offline','test_procurement_guard_offline']
    suite = unittest.defaultTestLoader.loadTestsFromNames(names)
else:
    suite = unittest.defaultTestLoader.discover('.',pattern='test_*_offline.py')
result = unittest.TextTestRunner(verbosity=2).run(suite)
print('BACKPORT_TEST_RESULT='+json.dumps(dict(mode=mode,tree=label,tests_run=result.testsRun,
      failures=len(result.failures),errors=len(result.errors),success=result.wasSuccessful())))
sys.exit(not result.wasSuccessful())
