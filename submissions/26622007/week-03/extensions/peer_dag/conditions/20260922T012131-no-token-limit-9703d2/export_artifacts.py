"""Create readable copies without editing original model artifacts."""
import json
from pathlib import Path

FOLDER = Path(__file__).resolve().parent
ROOT = FOLDER.parent.parent


def export():
    output = FOLDER / 'documents'
    output.mkdir(exist_ok=True)
    count = 0
    for path in sorted(FOLDER.glob('*.metrics.json')):
        metric = json.loads(path.read_text())
        run_id, case_id = metric['run_id'], metric['case_id']
        target = output / f'{run_id}.md'
        if target.exists():
            continue
        artifacts = ROOT / 'runs' / run_id / 'artifacts'
        main = artifacts / f'{case_id}.json'
        lines = [f"# {case_id} / {metric['condition']} / {metric['block']}회", '',
                 f"상태: {metric['status']}. 필수 facts: {metric['checks_passed']}/{metric['checks_total']}.",
                 '모델 원본의 열람용 사본이다. 실제 구현/실행 검증을 뜻하지 않는다.', '']
        files = ([main] if main.exists() else []) + sorted(p for p in artifacts.rglob('*.json') if p != main)
        for item in files:
            original = json.loads(item.read_text())
            artifact = original.get('artifact')
            relative = item.relative_to(artifacts).as_posix()
            lines += [f'## {relative}', '', f"Worker: {original.get('worker')}; 상태: {original['status']}",
                      f'[원본 JSON](../../../runs/{run_id}/artifacts/{relative})', '']
            if not artifact:
                lines += [original.get('error', '산출물 없음'), '']
                continue
            lines += [artifact['summary'], '', '### Facts', '', '```json',
                      json.dumps(artifact['facts'], ensure_ascii=False, indent=2), '```', '', '### Evidence', '']
            for index, evidence in enumerate(artifact['evidence'], 1):
                lines += [f'**{index}.** {evidence}', '']
        if not files:
            lines += ['산출물 없음. 원본 실행 로그를 확인한다.', '']
        with target.open('x', encoding='utf-8') as stream:
            stream.write('\n'.join(lines))
        count += 1
    print(json.dumps({'created_readable_documents': count}))


if __name__ == '__main__':
    export()
