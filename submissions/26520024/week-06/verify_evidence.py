"""Independent checks of committed live evidence, not a replacement for the course test."""
from collections import Counter, defaultdict
from datetime import datetime
import json
from pathlib import Path

from codex_backend import inspect_events
from core import strict_json, validate_buyer, validate_plan, validate_seller

ROOT = Path(__file__).resolve().parent


def records(path):
    for line in path.read_text().splitlines():
        if line.startswith('{'):
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if 'event' in record:
                yield record


def verify_models(paths):
    inputs, outputs, valid, last_actions = {}, {}, {}, {}
    calls = Counter()
    input_tokens = output_tokens = 0
    max_candidates = max_recent = max_memory = 0
    for path in paths:
        for r in records(path):
            event, key = r['event'], r.get('decision_id')
            if event == 'decision_input':
                assert key not in inputs, ('duplicate decision input', key)
                inputs[key] = r
                s = r['state']
                max_candidates = max(max_candidates, len(s.get('candidates', [])))
                max_recent = max(max_recent, len(s.get('recent', [])))
                max_memory = max(max_memory, len(s.get('memory', '')))
            elif event == 'model_output':
                assert r['returncode'] == 0, ('failed live call', path, key)
                text, usage = inspect_events(r['stdout'])
                outputs[key] = text
                calls[r['purpose']] += 1
                input_tokens += usage['input_tokens']
                output_tokens += usage['output_tokens']
            elif event == 'decision_valid':
                assert key in outputs and strict_json(outputs[key]) == r['action']
                state = inputs[key]['state']
                a = r['action']
                if 'candidates' in state:
                    validate_seller(a, state['candidates'], state['item_id'], state['incoming_offer'])
                elif 'shops' in state:
                    validate_plan(a, state['items'], [s['shop_id'] for s in state['shops']], state['total_budget'])
                else:
                    validate_buyer(a, state['budget'])
                valid[key] = a
                if 'task_id' in r:
                    role = 'seller' if 'candidates' in state else 'buyer'
                    last_actions[(role, r['task_id'])] = a
            elif event in ('seller_action', 'buyer_action'):
                role = event.split('_')[0]
                assert r['action'] == last_actions[(role, r['task_id'])], 'Action differs from model decision'
            elif event in ('buyer_error', 'seller_error', 'model_timeout', 'call_error'):
                raise AssertionError(('failed live execution', path, r))
    assert inputs and set(inputs) == set(valid), 'Missing completed decisions'
    assert max_candidates <= 12 and max_recent <= 2 and max_memory <= 600
    return dict(decisions=len(valid), calls=dict(calls), input_tokens=input_tokens, output_tokens=output_tokens,
                max_candidates=max_candidates, max_recent=max_recent, max_memory_chars=max_memory)


def check():
    report = json.loads((ROOT / 'test-report.json').read_text())
    assert report['passed'] and len(report['results']) == 21
    assert all(r['pass'] for r in report['results'])
    buyer_files = sorted((ROOT / 'logs').glob('buyer-under-test-*.log'))
    assert len(buyer_files) == 6
    buyer_outcomes = []
    for path in buyer_files:
        final = [r for r in records(path) if r['event'] == 'buyer_final']
        assert len(final) == 1, ('Buyer did not finish normally', path)
        outcome = final[0]['outcome']
        assert outcome == 'bought' if 'buyer_deal' in path.name else outcome in ('walked', 'no_deal')
        buyer_outcomes.append(dict(file=path.name, outcome=outcome))
    official = verify_models([ROOT / 'logs/seller-under-test.log'] + buyer_files)
    challenge_summaries = []
    for number in (1, 2):
        runs = sorted((ROOT / 'logs' / ('challenge-' + str(number))).glob('*/results.json'))
        assert runs, ('Missing challenge', number)
        for result_file in runs:
            folder = result_file.parent
            result = json.loads(result_file.read_text())
            assert result['passed'] and all(r['passed'] for r in result['results']), ('Challenge failure', result_file)
            models = verify_models([folder / 'run.jsonl'] + sorted(folder.glob('*.log')))
            summary = dict(challenge=number, folder=str(folder.relative_to(ROOT)), models=models)
            if number == 1:
                assert len(result['results']) == 6
                products = {p['id']: p for p in json.loads((ROOT / 'catalog.json').read_text())}
                for row in result['results']:
                    assert row['receipts'][0]['item_id'] == row['expected_id']
                    assert row['receipts'][0]['price'] >= products[row['expected_id']]['reserve']
                summary['correct_products'] = 6
            else:
                assert len(json.loads((folder / 'agent-cards.json').read_text())) == 20
                assert len(result['results']) == 3
                events = list(records(folder / 'run.jsonl'))
                overlap = []
                for row in result['results']:
                    bought = row['outcomes']
                    assert len(bought) == 4 and len({o['result']['receipt']['item'] for o in bought}) == 4
                    total = sum(o['result']['receipt']['price'] for o in bought)
                    assert total == row['total'] and total <= row['budget']
                    for outcome in bought:
                        assert outcome['result']['receipt']['item'] == outcome['purchase']['item']
                        assert outcome['result']['receipt']['price'] <= outcome['purchase']['budget']
                    intervals = defaultdict(dict)
                    for event in events:
                        if event.get('run_id') == row['run_id'] and event['event'] in ('buyer_start', 'buyer_final'):
                            intervals[event['item']][event['event']] = datetime.fromisoformat(event['time'])
                    assert len(intervals) == 4
                    seconds = (min(t['buyer_final'] for t in intervals.values()) -
                               max(t['buyer_start'] for t in intervals.values())).total_seconds()
                    assert seconds > 0, 'No real overlap between all four negotiations'
                    overlap.append(seconds)
                summary.update(totals=[r['total'] for r in result['results']], all_four_overlap_seconds=overlap)
            challenge_summaries.append(summary)
    return dict(passed=True, official=official, buyer_outcomes=buyer_outcomes, challenges=challenge_summaries)


if __name__ == '__main__':
    print(json.dumps(check(), indent=2, ensure_ascii=False))
