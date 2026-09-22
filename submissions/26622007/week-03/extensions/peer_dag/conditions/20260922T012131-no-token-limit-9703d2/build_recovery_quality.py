"""Export reviewed recovery outputs, separately from the primary 45-run reports."""
from collections import Counter
import json
import os
from pathlib import Path

B = Path(__file__).resolve().parent
ROOT = B.parent.parent
FOLDER = B / 'recovery_429'
summary = json.loads((FOLDER / 'summary.json').read_text())
notes = [json.loads(line) for line in (B / 'recovery_qualitative_notes.jsonl').read_text().splitlines()]
reviews = {row['run_id']: row for row in notes}
assert len(notes) == len(reviews) == len(summary['runs'])
assert set(reviews) == {row['run_id'] for row in summary['runs']}
documents = FOLDER / 'documents'
documents.mkdir()
lines = ['# 별도 429 복구 산출물 점검', '',
         '본 실험 45회에 합산하지 않는 복구 실행이다. 자동 facts 검사와 별개로 최종 문서 내용을 검토했다.',
         '코딩 보조 에이전트의 요구사항 대조이며 별도 맹검 심사가 아니다. 원본 산출물은 수정하지 않았다.',
         f"판정 수: {dict(Counter(row['rating'] for row in notes))}", '']
for metric in summary['runs']:
    run = metric['run_id']
    review = reviews[run]
    artifact_folder = ROOT / 'runs' / run / 'artifacts'
    main = artifact_folder / (metric['case_id'] + '.json')
    files = ([main] if main.exists() else []) + sorted(p for p in artifact_folder.rglob('*.json') if p != main)
    target = documents / (run + '.md')
    output = [f"# {metric['case_id']} / {metric['condition']} — 429 복구", '',
              f"상태: {metric['status']}. facts: {metric['checks_passed']}/{metric['checks_total']}.",
              '모델 원본의 열람용 사본이며 실제 구현/실행 검증이 아니다.', '']
    for path in files:
        result = json.loads(path.read_text())
        artifact = result.get('artifact')
        output += [f"## {path.relative_to(artifact_folder)}", '',
                   f"Worker: {result.get('worker')}; 상태: {result['status']}",
                   f"[원본 JSON]({os.path.relpath(path, documents)})", '']
        if artifact:
            output += [artifact['summary'], '', '### Facts', '', '```json',
                       json.dumps(artifact['facts'], ensure_ascii=False, indent=2), '```', '', '### Evidence', '']
            output += [f'{index}. {value}' for index, value in enumerate(artifact['evidence'], 1)]
        else:
            output += [result.get('error', '산출물 없음'), '']
    if not files:
        output += ['완료 산출물 없음. 원본 로그에 실패를 보존했다.', '']
    with target.open('x') as stream:
        stream.write('\n'.join(output))
    lines += [f"## {metric['case_id']} / {metric['condition']} — {review['rating']}", '',
              f"facts {metric['checks_passed']}/{metric['checks_total']}. 검토 범위: {review['review_scope']}.", '',
              '> ' + review['evidence'].replace('\n', '\n> '), '', review['notes'], '',
              f"[열람용 문서](documents/{run}.md) / [원본 로그]({os.path.relpath(ROOT / 'logs' / (run + '.jsonl'), FOLDER)})", '']
with (FOLDER / 'QUALITY_REVIEW.md').open('x') as stream:
    stream.write('\n'.join(lines))
print(json.dumps({'recovery_reviews': len(notes), 'ratings': dict(Counter(row['rating'] for row in notes))}, ensure_ascii=False))
