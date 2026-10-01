"""Verify actual allocation requests, response schemas, counts, and condition controls."""
from collections import Counter
import json
from pathlib import Path
import sys
from jsonschema import Draft202012Validator

HERE = Path(__file__).resolve().parent
BASE = HERE.parent.parent
sys.path.insert(0, str(BASE))
from contract_net import bid_response_format, load_tasks, make_team, messages_for
from run import digest

folder = Path(sys.argv[1]).resolve()
assert folder.parent == HERE
manifest = json.loads((folder/'manifest.json').read_text())
results = json.loads((folder/'results.json').read_text())
tasks, golds = load_tasks((BASE/'tasks.json').read_text())
tasks = {task.id:task for task in tasks}
errors, metrics = [], []
for result in results:
    path = BASE/'logs'/f"{result['run']}-{result['condition']}.log"
    rows = list(map(json.loads,path.read_text().splitlines()))
    state = rows[0]['settings']
    if state != manifest['state']:errors.append({'run':result['run'],'kind':'controls'})
    team = {c.name:c for c in make_team(result['condition'])}
    requests, responses = {}, 0
    for row in rows:
        if row['event']=='request':
            key=(row['task_id'],row['contractor'],row['attempt'])
            requests[key]=row['payload']
            valid=(row['payload']['messages']==messages_for(tasks[row['task_id']],team[row['contractor']]) and
                   row['payload'].get('response_format')==bid_response_format() and
                   all(row['payload'].get(k)==state['config'][k] for k in ('model','temperature','max_tokens','reasoning','provider')))
            if not valid:errors.append({'run':result['run'],'kind':'request','key':key})
        elif row['event']=='response':
            responses+=1
            key=(row['task_id'],row['contractor'],row['attempt'])
            try:
                content=json.loads(json.loads(row['raw_response'])['choices'][0]['message']['content'])
                Draft202012Validator(requests[key]['response_format']['json_schema']['schema']).validate(content)
            except Exception as exc:errors.append({'run':result['run'],'kind':'response','key':key,'error':str(exc)})
    note=json.loads(result['note'])
    awards={row['task_id']:row['contractor'] for row in rows if row['event']=='award'}
    counts=Counter(row['event'] for row in rows)
    if note['status']=='completed':
        expected={'tasks':len(tasks),'correct':sum(awards.get(t)==golds[t] for t in tasks),
                  'messages':counts['announcement']+counts['bid']+counts['award'],
                  'unassigned':counts['unassigned'],'misawards':sum(t in awards and awards[t]!=golds[t] for t in tasks)}
        if any(int(result[k])!=v for k,v in expected.items()):errors.append({'run':result['run'],'kind':'counts'})
    metrics.append({'run':result['run'],'condition':result['condition'],'status':note['status'],
                    'requests':len(requests),'responses':responses,'http_429':sum(r['event']=='http_error' and r['status']==429 for r in rows),
                    'awards':awards,'reported_cost_usd':note['reported_cost_usd'],
                    'c_bids':[(r['confidence'],r['event']=='bid') for r in rows if r['event'] in ('bid','refusal') and r['contractor']=='C']})
if [r['condition'] for r in results]!=manifest['schedule']:errors.append({'kind':'schedule'})
report={'passed':not errors,'errors':errors,'script_sha':digest(Path(__file__).read_bytes()),'runs':metrics}
with (folder/'verification.json').open('x') as f:
    json.dump(report,f,ensure_ascii=False,indent=2);f.write('\n')
lines=['# 동일 복합 작업 5개 기본 배정 실험', '',
       'peer DAG의 실제 업무 수행 45회와 별개로, 원래 강의 Contract Net에서 역할에 맞는 담당자를 고르는 9회를 실행했다.',
       f"실행 커밋 `{manifest['state']['git_commit']}`, 설정 ID `{manifest['state']['experiment_id']}`. [설정](../config.json), [규약](../PROTOCOL.md), [검증](verification.json).",
       '모델/프롬프트/temperature/입력/gold는 세 조건에서 동일하며 조건에서 지정한 역할/과신 지시만 바뀐다. gold는 결과물 품질이 아니라 역할 적합성이다.', '',
       '|조건/회차|정답 배정|메시지|미배정|오배정|원본 로그|', '|---|---:|---:|---:|---:|---|']
seen=Counter()
for r in results:
    seen[r['condition']]+=1
    lines.append(f"|{r['condition']}/{seen[r['condition']]}|{r['correct']}/{r['tasks']}|{r['messages']}|{r['unassigned']}|{r['misawards']}|[로그](../../../logs/{r['run']}-{r['condition']}.log)|")
lines+=['',f"실제 요청/응답/집계 검증: {report['passed']}. HTTP {sum(r['requests'] for r in metrics)}회, 응답 {sum(r['responses'] for r in metrics)}개, 429 {sum(r['http_429'] for r in metrics)}건.",
        f"응답 보고 비용 합계 ${sum(r['reported_cost_usd'] for r in metrics):.6f}. 응답 없는 요청 비용은 알 수 없다.",
        '메시지는 공고+bid=true 입찰+낙찰 수이며 거절 응답은 별도다. API 요청/응답 수와 동일한 단위가 아니다.',
        '과거 6개 작업 결과와 crashed 행은 root results.csv에 보존했으며 위 표에는 섞지 않았다.',
        'Smith 논문과의 비교 및 최종 해석 문단은 사용자가 검토하여 작성해야 한다.', '']
with (folder/'REPORT.md').open('x') as f:f.write('\n'.join(lines))
print(json.dumps({'passed':report['passed'],'errors':errors,'runs':metrics},ensure_ascii=False,indent=2))
