"""Independently check LAB 1 evidence, without treating expected LAB 2 failures as success."""
import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parents[2]


def verify(evidence):
    hashes = json.loads((evidence / 'source-hashes.json').read_text())
    for name, expected in hashes.items():
        assert hashlib.sha256((REPO / name).read_bytes()).hexdigest() == expected, name
    report = json.loads((ROOT / 'test-report.json').read_text())
    assert len(report['results']) == 25
    failed = [r for r in report['results'] if not r['pass']]
    assert len(failed) == 3 and all(r['test'] == 'seller_signed_receipt' for r in failed), failed
    assert report['passed'] is False
    runs, total_calls, remembered = [], 0, 0
    for scenario in ('buyer_deal', 'buyer_holds_budget', 'buyer_reads_reviews'):
        for number in range(1, 4):
            path = ROOT / 'logs' / ('buyer-under-test-%s-%s.log' % (scenario, number))
            lines = path.read_text().splitlines()
            final = json.loads(lines[-1])
            assert set(final) == {'shop', 'receipt', 'stars', 'review'}, path
            assert type(final['stars']) is int and 1 <= final['stars'] <= 5 and final['review']
            records = [json.loads(line) for line in lines if line.startswith('{')]
            starts = [r for r in records if r.get('event') == 'buyer_start']
            saved = [r for r in records if r.get('event') == 'market_memory_saved']
            assert len(starts) == len(saved) == 1
            assert saved[0]['outcome'] in ('bought', 'walked', 'no_deal', 'limit')
            assert not any(r.get('event') == 'buyer_error' for r in records), path
            choices = [r for r in records if r.get('event') == 'market_choice']
            assert len(choices) == 1 and choices[0]['url'] == final['shop']
            inputs = [r for r in records if r.get('event') == 'decision_input' and 'shops' in r.get('state', {})]
            assert len(inputs) == 1
            if any(s['personal_memory']['counts'] for s in inputs[0]['state']['shops']):
                remembered += 1
            calls = sum(r.get('event') == 'call_end' for r in records)
            assert calls >= 2
            total_calls += calls
            offers, received = [], []
            for record in records:
                if record.get('event') == 'http_request':
                    assert record['headers']['A2A-Version'] == '1.0'
                    body = record.get('body') or {}
                    if body.get('method') == 'SendMessage':
                        message = body['params']['message']
                        text = message['parts'][0]['text']
                        if text.isdigit():
                            assert message.get('taskId')
                            assert int(text) <= starts[0]['budget']
                            offers.append(int(text))
                elif record.get('event') == 'http_response':
                    task = json.loads(record['raw']).get('result', {}).get('task', {})
                    if task.get('status', {}).get('state') == 'TASK_STATE_COMPLETED':
                        received += [p['data'] for a in task.get('artifacts', []) for p in a.get('parts', []) if 'data' in p]
            if final['receipt'] is not None:
                assert received == [final['receipt']]
                assert offers and final['receipt']['price'] == offers[-1]
            else:
                assert not received
            runs.append(dict(scenario=scenario, run=number, shop=choices[0]['shop_name'],
                             offers=offers, stars=final['stars'], outcome=saved[0]['outcome'], model_calls=calls))
    memory = json.loads((evidence / 'test-memory-export.json').read_text())
    assert len(memory) == 9 and len({r['id'] for r in memory}) == 9
    assert remembered >= 5
    summary = dict(scope='LAB 1 only; complete week-07 still fails seller signing',
                   official_passed=22, official_total=25, expected_lab2_failures=failed,
                   buyer_model_calls=total_calls, runs_with_personal_memory=remembered, runs=runs)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('evidence', type=Path)
    verify(parser.parse_args().evidence.resolve())
