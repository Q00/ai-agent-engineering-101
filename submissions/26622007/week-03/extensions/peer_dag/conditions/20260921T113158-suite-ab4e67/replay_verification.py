"""Post-hoc implementation check: first scheduled baseline per case, no API calls."""
import json
from pathlib import Path
import subprocess
import sys

B = Path(__file__).resolve().parent
ROOT = B.parent.parent
sys.path.insert(0, str(ROOT))
from audit import audit
from core import fingerprint

manifest = json.loads((B / 'manifest.json').read_text())
selected = [item for item in manifest['schedule'] if item['block'] == 1 and item['condition'] == 'baseline']
rows = []
for item in selected:
    original_dir = ROOT / 'runs' / item['run_id']
    original_result = json.loads((original_dir / 'result.json').read_text())
    def artifacts(folder):
        return {p.relative_to(folder / 'artifacts').as_posix(): json.loads(p.read_text())
                for p in (folder / 'artifacts').rglob('*.json')}
    original_artifacts = artifacts(original_dir)
    for parallel in (1, 3):
        run_id = item['run_id'] + f'-replay-p{parallel}'
        command = [sys.executable, str(ROOT / 'cli.py'), 'replay', '--replay', str(ROOT / 'logs' / (item['run_id'] + '.jsonl')),
                   '--condition', item['condition'], '--parallel', str(parallel), '--run-id', run_id, '--replay-delay-ms', '100']
        with (B / (run_id + '.console.log')).open('x') as stream:
            process = subprocess.run(command, stdout=stream, stderr=subprocess.STDOUT, timeout=60)
        directory = ROOT / 'runs' / run_id
        result = json.loads((directory / 'result.json').read_text())
        replay_artifacts = artifacts(directory)
        trace = audit(ROOT / 'logs' / (run_id + '.jsonl'))
        row = {'case_id': item['case_id'], 'source_run_id': item['run_id'], 'run_id': run_id, 'parallel': parallel,
               'source_status': original_result['status'], 'replay_status': result['status'],
               'same_status': result['status'] == original_result['status'],
               'same_evaluation': result['evaluation'] == original_result['evaluation'],
               'same_all_artifacts': replay_artifacts == original_artifacts,
               'same_calls': result['calls'] == original_result['calls'],
               'replay_complete': result['replay_complete'], 'http_requests': result['http_requests'],
               'artifact_sha': fingerprint(replay_artifacts), 'trace_passed': trace['passed'],
               'peak_execution_calls': trace['peak_execution_calls'], 'exit_code': process.returncode}
        rows.append(row)
        print(json.dumps(row, ensure_ascii=False), flush=True)
checks = ('same_status', 'same_evaluation', 'same_all_artifacts', 'same_calls', 'replay_complete', 'trace_passed')
report = {'scope': 'Post-hoc implementation check, fixed recorded replies and artificial 100ms latency; separate from 45 live attempts.',
          'selection': 'First scheduled baseline for every case, including failures; no success-based selection.',
          'script_sha': fingerprint(Path(__file__).read_text()), 'passed': all(all(row[k] for k in checks) and row['http_requests'] == 0 for row in rows),
          'runs': rows}
with (B / 'replay_verification.json').open('x') as stream:
    json.dump(report, stream, ensure_ascii=False, indent=2)
    stream.write('\n')
