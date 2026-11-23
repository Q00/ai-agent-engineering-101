"""Separate course-required allocation study on the same committed five tasks."""
from contextlib import redirect_stdout
import csv
from datetime import datetime, timezone
import fcntl
import json
from pathlib import Path
import subprocess
import sys
import time
import uuid

HERE = Path(__file__).resolve().parent
BASE = HERE.parent.parent
sys.path.insert(0, str(BASE))
from contract_net import CONDITIONS, load_tasks
from openrouter_client import OpenRouterClient, rate_limit_policy, read_key
from run import digest, load_config, require_committed_tasks, run_one, snapshot


def main():
    config = load_config(HERE / 'config.json')
    tasks, gold = load_tasks((BASE / 'tasks.json').read_text())
    order = [condition for offset in range(3) for condition in CONDITIONS[offset:] + CONDITIONS[:offset]]
    planned = len(tasks) * 3 * len(order)
    assert len(tasks) == 5 and len(order) == 9
    assert planned * rate_limit_policy(config)['max_attempts'] <= config['max_http_requests']
    state = snapshot(config)
    state['git_commit'] = require_committed_tasks()
    repo = Path(subprocess.check_output(['git','rev-parse','--show-toplevel'], cwd=BASE, text=True).strip())
    files = [HERE / 'config.json', Path(__file__), HERE / 'PROTOCOL.md']
    hashes = {}
    for path in files:
        relative = path.relative_to(repo).as_posix()
        assert subprocess.check_output(['git','show',f'HEAD:{relative}'], cwd=BASE) == path.read_bytes()
        hashes[relative] = digest(path.read_bytes())
    if sys.argv[1:] == ['plan']:
        print(json.dumps({'schedule': order, 'planned_calls': planned, 'max_http_requests': config['max_http_requests'],
                          'config': config, 'experiment_id':state['experiment_id']}, ensure_ascii=False, indent=2))
        return
    if sys.argv[1:] != ['run']:
        raise ValueError('choose plan or run')
    folder = HERE / (datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S') + '-' + uuid.uuid4().hex[:6])
    folder.mkdir()
    manifest = {'schedule': order, 'planned_calls':planned, 'state':state, 'source_hashes':hashes,
                'policy':'all nine scheduled rounds retained; failures not replaced; no peer execution results included'}
    with (folder/'manifest.json').open('x') as f:
        json.dump(manifest,f,ensure_ascii=False,indent=2);f.write('\n')
    client = OpenRouterClient(read_key(BASE.parent/'.env'), config)
    recorded = []
    with (BASE/'.run.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        for index, condition in enumerate(order, 1):
            if snapshot(config)['experiment_id'] != state['experiment_id']:
                raise ValueError('source changed during allocation study')
            print(json.dumps({'event':'allocation_start','index':index,'condition':condition,'folder':str(folder)}),flush=True)
            with (folder/f'{index}-{condition}.console.log').open('x') as f, redirect_stdout(f):
                completed = run_one(tasks, gold, condition, client, state)
            with (BASE/'results.csv').open() as f:
                row = list(csv.DictReader(f))[-1]
            recorded.append(row)
            with (folder/f'{index}-{condition}.result.json').open('x') as f:
                json.dump(row,f,ensure_ascii=False,indent=2);f.write('\n')
            print(json.dumps({'event':'allocation_end','index':index,'condition':condition,'completed':completed,
                              'run_id':row['run'],'correct':row['correct'],'messages':row['messages'],'misawards':row['misawards']},ensure_ascii=False),flush=True)
            if index < len(order): time.sleep(15)
    with (folder/'results.json').open('x') as f:
        json.dump(recorded,f,ensure_ascii=False,indent=2);f.write('\n')
    print(json.dumps({'event':'allocation_study_end','folder':str(folder),'rounds':len(recorded)}),flush=True)


if __name__ == '__main__': main()
