"""Standard library OpenAI-compatible runner; never reads .env files."""
import argparse
import csv
import fcntl
import json
import os
from pathlib import Path
import urllib.request
import urllib.error
import uuid
from datetime import datetime, timezone
from contract_net import run, CONDITIONS

ROOT = Path(__file__).resolve().parent
HEADER = 'run,condition,tasks,correct,messages,unassigned,misawards,note'.split(',')
EXEC_HEADER = 'run,condition,success,failed,invalid_bids,llm_calls'.split(',')


class Chat:
    def __init__(self, model, provider=None):
        self.model = model
        self.calls = 0
        self.base = os.environ.get('OPENAI_BASE_URL', 'https://api.openai.com/v1').rstrip('/')
        self.key = os.environ['OPENROUTER_API_KEY'] if provider == 'openrouter' else os.environ['OPENAI_API_KEY']
        if provider == 'openrouter':
            self.base = 'https://openrouter.ai/api/v1'
        if not self.base.startswith('https://'):
            raise ValueError('HTTPS endpoint required')

    def __call__(self, messages, tools=None):
        payload = dict(model=self.model, messages=messages, temperature=0, max_tokens=1024)
        if self.base == 'https://openrouter.ai/api/v1':
            payload['reasoning'] = {'enabled': False}
        if tools:
            payload['tools'] = tools
            payload['tool_choice'] = 'auto' if any(m.get('role') == 'tool' for m in messages) else 'required'
        else:
            payload['response_format'] = {'type': 'json_object'}
        req = urllib.request.Request(self.base + '/chat/completions', data=json.dumps(payload).encode(),
                                     headers={'Authorization': 'Bearer ' + self.key, 'Content-Type': 'application/json'})
        self.calls += 1
        with urllib.request.urlopen(req, timeout=90) as response:
            data = json.load(response)
            if data.get('error') or not data.get('choices'):
                raise RuntimeError('Provider returned no completion')
            return data['choices'][0]['message']


def append(path, header, row):
    with path.open('a+', newline='') as f:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        f.seek(0, 2)
        new = f.tell() == 0
        writer = csv.DictWriter(f, fieldnames=header)
        if new:
            writer.writeheader()
        writer.writerow(row)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--provider', choices=['openrouter'], help='Use OPENROUTER_API_KEY and the OpenRouter endpoint')
    parser.add_argument('--condition', choices=CONDITIONS, default='baseline')
    parser.add_argument('--model', default=os.environ.get('AGENT_MODEL', 'nvidia/nemotron-3.5-lightning:free'))
    parser.add_argument('--limit', type=int)
    parser.add_argument('--smoke', action='store_true', help='Separate smoke captures from graded results')
    args = parser.parse_args()
    if args.limit is not None and not args.smoke:
        parser.error('--limit requires --smoke')
    run_id = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S') + '-' + uuid.uuid4().hex[:8]
    folder = ROOT / ('smoke' if args.smoke else 'logs')
    folder.mkdir(exist_ok=True)
    with (folder / (run_id + '.jsonl')).open('x') as log:
        def emit(event, **data):
            line = json.dumps(dict(event=event, **data), ensure_ascii=False)
            print(line, flush=True)
            log.write(line + '\n')
            log.flush()
        chat = None
        try:
            chat = Chat(args.model, provider=args.provider) if args.provider else Chat(args.model)
            emit('setup', run=run_id, condition=args.condition, model=chat.model, provider=chat.base, temperature=0)
            tasks = json.loads((ROOT / 'tasks.json').read_text())
            if args.limit is not None:
                tasks = tasks[:args.limit]
            metrics, execution = run(tasks, args.condition, chat, emit)
            row = dict(run=run_id, condition=args.condition, **metrics, note='')
            erow = dict(run=run_id, condition=args.condition, **execution, llm_calls=chat.calls)
            emit('summary', **row, execution=erow)
        except Exception as exc:
            # Exception bodies can include request details. Record only the class.
            row = dict.fromkeys(HEADER, '')
            row.update(run=run_id, condition=args.condition, note=type(exc).__name__)
            erow = dict(run=run_id, condition=args.condition, llm_calls=chat.calls if chat else 0)
            emit('crash', **row, http_status=exc.code if isinstance(exc, urllib.error.HTTPError) else None)
        if not args.smoke:
            append(ROOT / 'results.csv', HEADER, row)
            append(ROOT / 'execution.csv', EXEC_HEADER, erow)
        return int(bool(row['note']))


if __name__ == '__main__':
    raise SystemExit(main())
