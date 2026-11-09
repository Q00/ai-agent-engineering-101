"""Recompute experiment metrics from immutable event captures."""
import csv
import json
from pathlib import Path
from contract_net import matches

ROOT = Path(__file__).resolve().parent


def verify():
    with (ROOT / 'results.csv').open() as f:
        rows = list(csv.DictReader(f))
    with (ROOT / 'execution.csv').open() as f:
        executions = {r['run']: r for r in csv.DictReader(f)}
    for row in rows:
        events = [json.loads(line) for line in (ROOT/'logs'/(row['run']+'.jsonl')).read_text().splitlines()]
        assert events[0]['condition'] == row['condition']
        if row['note']:
            assert events[-1]['event'] == 'crash'
            assert all(row[k] == '' for k in ('tasks','correct','messages','unassigned','misawards'))
            continue
        summary = events[-1]
        assert summary['event'] == 'summary'
        awards = [e for e in events if e['event'] == 'award']
        evaluations = [e for e in events if e['event'] == 'evaluation']
        assert len(awards) == len(evaluations)
        correct = sum(e['contractor'] == e['gold'] for e in evaluations)
        unassigned = sum(e['event'] == 'unassigned' for e in events)
        counts = dict(tasks=len(evaluations)+unassigned, correct=correct,
                      messages=sum(e['event'] in ('announcement','bid','award') for e in events),
                      unassigned=unassigned, misawards=len(awards)-correct)
        for k,v in counts.items():
            assert int(row[k]) == v == summary[k], (row['run'],k)
        for e in evaluations:
            assert e['success'] == (e['status']=='completed' and matches(e['answer'],e['expected']))
        execution=executions[row['run']]
        assert int(execution['success']) == sum(e['success'] for e in evaluations)
        assert int(execution['failed']) == sum(not e['success'] for e in evaluations)
        assert int(execution['invalid_bids']) == sum(e['event']=='bid' and e['reason']=='invalid_json_bid' for e in events)
        for e in events:
            if e['event']=='announcement':
                assert set(e['task']) == {'id','desc'}
    print(f'Verified {len(rows)} run records against their raw logs')


if __name__=='__main__': verify()
