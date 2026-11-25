"""Summarize preserved copy probes without overwriting or hiding failed runs."""
import argparse
from collections import Counter,defaultdict
import json
from pathlib import Path
import statistics


def read_rows(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def summarize(paths):
    results={};calls=[];failures=[]
    for path in paths:
        manifest=json.loads((path/'manifest.json').read_text());model=manifest['model']
        calls.extend(read_rows(path/'calls.jsonl'))
        for row in read_rows(path/'results.jsonl'):
            if row.get('error'):
                failures.append({'run':str(path),'model':model,**row});continue
            key=(model,row['case'])
            if key in results:raise ValueError(f'Duplicate successful case: {key}; do not select a favored rerun')
            results[key]={'model':model,**row}
    selections=defaultdict(list);negotiations=defaultdict(list)
    for row in results.values():
        if row['phase']=='selection':selections[row['model']].append(row)
        elif row['phase']=='negotiation':negotiations[(row['model'],row['variant'])].append(row)
    counts={model:{'cases':len(rows),'by_variant':dict(Counter(r['variant'] for r in rows)),
        'by_position':dict(Counter(r['position'] for r in rows)),
        'by_kind':{kind:dict(Counter(r['variant'] for r in rows if r['kind']==kind)) for kind in ('goods','food')}}
        for model,rows in selections.items()}
    measured=[]
    for (model,variant),rows in negotiations.items():
        names={r['case'] for r in rows}
        relevant=[c for c in calls if c['model']==model and c['case'] in names]
        valid=[c for c in relevant if c.get('usage') and not c.get('error')]
        measured.append({'model':model,'variant':variant,'cases':len(rows),
            'mock_receipts':sum(r['mock_receipt_received'] for r in rows),
            'requests_per_case':[r['request_count'] for r in rows],
            'offers_per_case':[r['offers'] for r in rows],
            'model_calls_per_case':[sum(c['case']==r['case'] for c in relevant) for r in rows],
            'mean_model_prompt_bytes':statistics.mean(c['prompt_bytes_utf8'] for c in relevant),
            'mean_cli_input_tokens_including_system_overhead':statistics.mean(c['usage']['input_tokens'] for c in valid),
            'mean_cli_output_tokens_including_reasoning':statistics.mean(c['usage']['output_tokens'] for c in valid),
            'mean_cli_cached_input_tokens':statistics.mean(c['usage'].get('cached_input_tokens',0) for c in valid)})
    return {'selection':counts,'negotiation':measured,'successful_cases':len(results),
        'model_call_attempts':len(calls),'model_calls_with_usage':sum(bool(c.get('usage')) for c in calls),
        'preserved_failures':failures,
        'interpretation_limits':['Selection is hypothetical visibility, not our production buyer-selection flow.',
            'Mock receipts are not signed real receipts or settled trades.',
            'Two negotiation repetitions per copy do not establish reliable success-rate differences.',
            'CLI token usage includes variable system overhead, caching and reasoning; it is not a direct copy-only cost effect.',
            'Model-reported reasons are explanations, not established causal effects.']}


def main():
    parser=argparse.ArgumentParser();parser.add_argument('runs',type=Path,nargs='+')
    parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
    result=summarize(args.runs)
    with args.output.open('x') as target:json.dump(result,target,ensure_ascii=False,indent=2)
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
