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
    if event in ('subprocess.Popen', 'os.system', 'os.posix_spawn'):
        raise AssertionError('external process forbidden')
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
# These two existing launcher tests intentionally spawn bash and a validation
# interpreter. Preserve their source, but explicitly skip under this stricter
# no-child-process run rather than weakening its guard.
import test_visibility_guard_offline as visibility_tests
for name in ('test_bash_stub_preserves_auth_loop_and_other_python_forwarding',
             'test_bash_stub_structure_failure_cannot_fall_back_to_vanilla'):
    method = getattr(visibility_tests.LauncherTests, name)
    setattr(visibility_tests.LauncherTests, name,
            unittest.skip('child processes disabled for local goods terms verification')(method))
if mode in ('review-grade', 'review-warranty', 'review-option'):
    methods = {
        'review-grade': ['test_review_grade_rejection_never_issues_initial_receipt',
                         'test_review_grade_rejection_never_issues_later_receipt'],
        'review-warranty': ['test_review_minimum_warranty_cannot_close_B6_or_C0_initially',
                            'test_review_minimum_warranty_checked_after_B6_quote_without_price_too',
                            'test_review_minimum_warranty_fulfilled_by_actual_A72'],
        'review-option': ['test_review_negated_and_unclear_options_preserve_entire_A0_quote',
                          'test_review_explicit_positive_options_requote_without_signing',
                          'test_negated_option_choice_never_changes_terms'],
    }
    names = ['test_goods_terms_pilot_offline.GoodsTermsPilotTests.'+name for name in methods[mode]]
    suite = unittest.defaultTestLoader.loadTestsFromNames(names)
elif mode == 'focused':
    names = ['test_goods_terms_pilot_offline', 'test_grade_warranty_offline', 'test_warranty_disclosure_offline', 'test_goods_floor_offline', 'test_goods_selection_offline']
    suite = unittest.defaultTestLoader.loadTestsFromNames(names)
else:
    suite = unittest.defaultTestLoader.discover('.',pattern='test_*_offline.py')
result = unittest.TextTestRunner(verbosity=2).run(suite)
print('BACKPORT_TEST_RESULT='+json.dumps(dict(mode=mode,tree=label,tests_run=result.testsRun,
      failures=len(result.failures),errors=len(result.errors),skipped=len(result.skipped),success=result.wasSuccessful())))
sys.exit(not result.wasSuccessful())
