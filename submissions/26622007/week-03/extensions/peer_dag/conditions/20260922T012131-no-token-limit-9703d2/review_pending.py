"""Show unreviewed original roots; classify missing final artifacts as incomplete."""
import json
from pathlib import Path
import argparse

B = Path(__file__).resolve().parent
ROOT = B.parent.parent
parser = argparse.ArgumentParser()
parser.add_argument('--recovery', action='store_true')
recovery = parser.parse_args().recovery
FOLDER = B / 'recovery_429' if recovery else B
notes = B / ('recovery_qualitative_notes.jsonl' if recovery else 'qualitative_notes.jsonl')
seen = {json.loads(line)['run_id'] for line in notes.read_text().splitlines()} if notes.exists() else set()
for path in sorted(FOLDER.glob('*.metrics.json')):
    m = json.loads(path.read_text())
    if m['run_id'] in seen:
        continue
    print('\nRUN', m['run_id'], 'FACTS', m['checks_passed'], '/', m['checks_total'], 'DEPTH', m['max_depth'])
    artifact = ROOT / 'runs' / m['run_id'] / 'artifacts' / (m['case_id'] + '.json')
    if m['status'] == 'succeeded':
        print(artifact.read_text())
    else:
        endings = []
        for row in map(json.loads, (ROOT / 'logs' / (m['run_id'] + '.jsonl')).read_text().splitlines()):
            if row['event'] == 'http_response':
                raw = json.loads(row['raw_response'])
                if raw.get('choices', [{}])[0].get('finish_reason') != 'stop':
                    endings.append({'task_id': row['task_id'], 'phase': row['phase'],
                                    'finish_reason': raw['choices'][0].get('finish_reason'),
                                    'completion_tokens': raw.get('usage', {}).get('completion_tokens')})
        print(json.dumps({'failures': m['task_failures'], 'errors': m['verification_errors'], 'endings': endings}, ensure_ascii=False))
        entry = {'run_id': m['run_id'], 'rating': '미충족', 'review_scope': '기계적으로 확인한 최종 실패 상태와 응답 종료 사유; 부분 산출물 품질 점수 아님',
                 'evidence': str(m['task_failures'] or m['error']),
                 'notes': '완료된 최종 결과가 없으므로 요구한 통합 산출물은 미충족이다. 원본 부분 결과는 보존했다.', 'response_endings': endings}
        with notes.open('a') as stream:
            stream.write(json.dumps(entry, ensure_ascii=False) + '\n')
