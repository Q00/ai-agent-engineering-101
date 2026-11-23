"""Four isolated schema diagnostics; never counted as condition-study results."""
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from urllib.request import urlopen
import uuid

from jsonschema import Draft202012Validator

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
from openrouter_client import OpenRouterClient, read_key, redact


def main():
    provider = sys.argv[1]
    if provider not in ('deepinfra', 'morph', 'together'):
        raise ValueError('diagnostic provider must be deepinfra, morph or together')
    root = BASE / 'extensions/peer_dag'
    config = json.loads((root / 'config.json').read_text())['transport']
    assert not {'max_tokens', 'max_completion_tokens'} & config.keys()
    config['provider']['only'] = [provider]
    tape = root / 'logs/20260922T012131-no-token-limit-9703d2-r1-release-review-baseline.jsonl'
    selected = {}
    for row in map(json.loads, tape.read_text().splitlines()):
        if row['event'] == 'http_request':
            selected.setdefault(row['phase'], row)
    assert set(selected) == {'propose', 'review', 'execute', 'synthesize'}
    folder = BASE / 'diagnostics' / (datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S') + '-no-token-limit-' + provider + '-' + uuid.uuid4().hex[:6])
    folder.mkdir()
    with urlopen('https://openrouter.ai/api/v1/models/deepseek/deepseek-v4.1-flash/endpoints', timeout=30) as response:
        metadata = response.read()
    (folder / 'endpoint_metadata.json').write_bytes(metadata)
    key = read_key(BASE.parent / '.env')
    outcomes = []
    with (folder / 'events.jsonl').open('x') as stream:
        for phase in ('propose', 'review', 'execute', 'synthesize'):
            source = selected[phase]
            wire = source['payload']
            events = []
            def emit(event, **fields):
                value = {'event': event, 'phase': phase, 'at': datetime.now(timezone.utc).isoformat(), **fields}
                events.append(value)
                stream.write(redact(json.dumps(value, ensure_ascii=False), key) + '\n')
                stream.flush()
            client = OpenRouterClient(key, config)
            try:
                raw = client.complete(wire['messages'], emit, source['task_id'], source['contractor'], response_format=wire['response_format'])
                data = json.loads(raw)
                Draft202012Validator(wire['response_format']['json_schema']['schema']).validate(data)
                error = None
            except Exception as exc:
                error = redact(type(exc).__name__ + ': ' + str(exc), key)
            usages = [e for e in events if e['event'] == 'usage']
            requests = [e for e in events if e['event'] == 'request']
            outcome = {'phase': phase, 'schema_passed': error is None, 'error': error,
                       'request_count': client.request_count,
                       'request_contract_passed': bool(requests) and all(e['payload']['response_format'] == wire['response_format'] and not {'max_tokens', 'max_completion_tokens'} & e['payload'].keys() for e in requests),
                       'providers': [e['provider'] for e in usages], 'finish_reasons': [e['finish_reason'] for e in usages]}
            outcomes.append(outcome)
            print(json.dumps(outcome, ensure_ascii=False), flush=True)
    report = {'scope': 'Alternative provider availability/schema diagnostic only; not part of the ongoing Fireworks 45-run study.',
              'source_tape': str(tape.relative_to(BASE)), 'config': config,
              'passed': all(o['schema_passed'] and o['request_contract_passed'] for o in outcomes), 'phases': outcomes}
    with (folder / 'result.json').open('x') as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    print(json.dumps({'folder': str(folder), 'passed': report['passed']}), flush=True)


if __name__ == '__main__':
    main()
