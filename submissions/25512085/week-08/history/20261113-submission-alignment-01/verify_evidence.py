"""No live board/log reread. Verify code drift, original preservation, and artifacts."""
import ast
import collections
import difflib
import hashlib
import json
from pathlib import Path
import re
from collect import OUT, REPO, save, ssh

manifest = json.loads((OUT / 'runtime-manifest.json').read_text())
metrics = json.loads((OUT / 'sanitizedmetrics.json').read_text())
comparison = {}
expected = {}
for name, row in manifest['sources'].items():
    local = REPO / name
    rec = {'vm_exists': row['exists'], 'local_exists': local.is_file()}
    expected[name] = row.get('sha256')
    if row['exists']:
        mirror = OUT / 'runtime-source' / name
        assert hashlib.sha256(mirror.read_bytes()).hexdigest() == row['sha256']
        if local.is_file():
            assert hashlib.sha256(local.read_bytes()).hexdigest() == row['local_sha256'], 'original local source changed: ' + name
            a, b = mirror.read_text(), local.read_text()
            rec.update(raw_bytes_equal=row['matches_local'], newline_normalized_equal=a == b,
                       vm_lines=len(a.splitlines()), local_lines=len(b.splitlines()),
                       vm_normalized_sha256=hashlib.sha256(a.encode()).hexdigest(),
                       local_normalized_sha256=hashlib.sha256(b.encode()).hexdigest(),
                       changed_ranges_vm_then_local=[{'vm_start': i+1, 'vm_end_exclusive': j+1, 'local_start': k+1, 'local_end_exclusive': l+1}
                           for tag,i,j,k,l in difflib.SequenceMatcher(None, a.splitlines(), b.splitlines(), autojunk=False).get_opcodes() if tag != 'equal'])
    comparison[name] = rec
save('source-comparison.json', comparison)

for row in manifest['local_existing_logs']:
    p = REPO / 'submissions/25512085/week-08/logs' / row['name']
    assert hashlib.sha256(p.read_bytes()).hexdigest() == row['sha256'], 'original log changed'
    assert p.stat().st_size == row['bytes']

# Verify against the deployed commit without exposing global dirty file names.
code = '''from pathlib import Path
import hashlib,json,subprocess,datetime
root=Path('/home/comalswjd/ai-agent-engineering-101')
expected=EXPECTED
def git(*a): return subprocess.check_output(['git',*a],cwd=root)
checks={}
for name,digest in expected.items():
 p=root/name
 if digest is None:
  checks[name]={'still_absent':not p.exists()}
  continue
 data=p.read_bytes()
 tracked=bool(git('ls-files','--',name).strip())
 checks[name]={'disk_sha256_unchanged':hashlib.sha256(data).hexdigest()==digest,'tracked':tracked}
 if tracked: checks[name]['head_blob_matches_disk']=hashlib.sha256(git('show','HEAD:'+name)).hexdigest()==digest
status=git('status','--porcelain','--untracked-files=no').decode().splitlines()
print(json.dumps({'observed_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'head':git('rev-parse','HEAD').decode().strip(),'global_tracked_dirty_count':len(status),'source_checks':checks}))
'''.replace('EXPECTED', repr(expected))
remote = ssh(code)
assert remote['head'] == manifest['head']
assert all(all(v.values()) for v in remote['source_checks'].values())
save('runtime-final-readonly-verification.json', remote)

counts = collections.Counter()
reasons = collections.Counter()
excerpts = []
for log in metrics['logs']:
    assert log['bytes_read'] <= 4*1024*1024
    assert log['stable_during_read']
    counts.update(log['counts'])
    reasons.update(log['reason_phrase_line_counts_nonexclusive'])
    for row in log['exact_substring_examples_NOT_FULL_LOG_LINES']:
        excerpts.append(dict(source_file=log['name'], **row))
assert len(metrics['board']['own']['shops']) == len(metrics['board']['own']['buyers']) == len(metrics['board']['own']['players']) == 1
validation = {'original_allowlisted_local_sources_preserved': True, 'existing_local_log_sha256_preserved': True,
              'vm_allowlisted_sources_unchanged_and_match_head': True, 'board_gets': 1,
              'bounded_log_bytes_total': sum(r['bytes_read'] for r in metrics['logs']),
              'complete_log_lines_total': sum(r['complete_lines_scanned'] for r in metrics['logs']),
              'log_counts_sum_NOT_BOARD_COUNTS': dict(counts), 'nonexclusive_reason_phrase_counts': dict(reasons),
              'python_source_syntax_checked_without_import_or_pyc': 0,
              'normalized_equal_files': sum(r.get('newline_normalized_equal', False) for r in comparison.values()),
              'normalized_different_files': sum(r.get('newline_normalized_equal') is False for r in comparison.values()),
              'vm_absent_local_present': [n for n,r in comparison.items() if not r['vm_exists'] and r['local_exists']]}
for p in OUT.rglob('*.py'):
    ast.parse(p.read_text(encoding='utf-8'), filename=str(p))
    validation['python_source_syntax_checked_without_import_or_pyc'] += 1
save('verification.json', validation)

# Only exact discontiguous substrings are retained, explicitly not original lines.
text = '# Settlement substring excerpt — NOT original full market-kit.log\n'
text += '# Quoted spans from the same line are separated by [omitted]; unit/URL/IDs/signatures were not exported.\n'
for e in excerpts:
    text += f"{e['source_file']} {e['clock_only']} ({e['role']}): " + ' [omitted] '.join(e['exact_substrings']) + '\n'
save('logs/settlement-substrings-excerpt.txt', text.encode())
print(json.dumps(validation, ensure_ascii=False, indent=2))
