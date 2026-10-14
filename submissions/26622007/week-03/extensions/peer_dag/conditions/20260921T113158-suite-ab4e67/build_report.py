"""Derive the final evidence report from immutable live traces and review notes."""
from collections import Counter
from datetime import datetime
import json
from pathlib import Path
import sys

B = Path(__file__).resolve().parent
ROOT = B.parent.parent
sys.path.insert(0, str(ROOT))
from core import fingerprint
from export_artifacts import export

s = json.loads((B / 'summary.json').read_text())
reviews = [json.loads(line) for line in (B / 'qualitative_notes.jsonl').read_text().splitlines()]
by_review = {r['run_id']: r for r in reviews}
assert len(reviews) == len(by_review) == 45
assert set(by_review) == {m['run_id'] for m in s['runs']}
export()
errors, finishes, retry_calls, recovered, providers, models = Counter(), Counter(), set(), set(), Counter(), Counter()
failures, ties, elapsed_times, score_exceptions = [], Counter(), [], []
for m in s['runs']:
    rows = list(map(json.loads, (ROOT / 'logs' / (m['run_id'] + '.jsonl')).read_text().splitlines()))
    elapsed_times += [datetime.fromisoformat(row['at']) for row in rows if row['event'] in ('run_start', 'run_end')]
    endings = []
    for row in rows:
        e = row['event']
        key = (m['run_id'], row.get('task_id'), row.get('contractor'), row.get('phase'))
        if e == 'http_http_error':
            errors[str(row['status'])] += 1
            if row['status'] == 429:
                retry_calls.add(key)
        elif e == 'http_response':
            raw = json.loads(row['raw_response'])
            reason = raw['choices'][0].get('finish_reason')
            finishes[str(reason)] += 1
            if reason != 'stop':
                endings.append({'task_id': row['task_id'], 'phase': row['phase'], 'finish_reason': reason,
                                'completion_tokens': raw.get('usage', {}).get('completion_tokens')})
            if key in retry_calls:
                recovered.add(key)
        elif e == 'http_usage':
            providers[row.get('provider', 'unknown')] += 1
            models[row.get('model', 'unknown')] += 1
        elif e == 'award':
            if m['condition'] == 'overconfident' and row['task_id'] == m['case_id'] and row['worker'] != 'C':
                c_proposal = next((r['proposal'] for r in rows if r['event'] == 'proposal' and r['task_id'] == m['case_id'] and r['worker'] == 'C'), {})
                score_exceptions.append({'run_id': m['run_id'], 'winner': row['worker'], 'scores': row['scores'],
                                         'winning_confidence': row['confidence'], 'c_confidence': c_proposal.get('confidence')})
            eligible = {w: sum(v.values()) for w, v in row['scores'].items() if all(n >= 1 for n in v.values())}
            if eligible:
                high = max(eligible.values())
                if sum(v == high for v in eligible.values()) > 1:
                    ties[m['condition']] += 1
    if not m['passed']:
        failures.append({'run_id': m['run_id'], 'status': m['status'], 'wrong_or_missing': m['wrong_or_missing'],
                         'task_failures': m['task_failures'], 'response_endings': endings})
extra = {'script_sha': fingerprint(Path(__file__).read_text()), 'http_errors': dict(errors), 'response_finish_reasons': dict(finishes),
         'rate_limited_logical_calls': len(retry_calls), 'rate_limited_calls_with_http_response': len(recovered),
         'providers': dict(providers), 'models': dict(models), 'score_tied_awards': dict(ties), 'failures': failures,
         'overconfident_root_exceptions': score_exceptions,
         'wall_span_seconds': (max(elapsed_times) - min(elapsed_times)).total_seconds(),
         'qualitative_ratings': dict(Counter(r['rating'] for r in reviews))}
with (B / 'supplementary_metrics.json').open('x') as f:
    json.dump(extra, f, ensure_ascii=False, indent=2); f.write('\n')

def link(m):
    return f"[문서](documents/{m['run_id']}.md) / [원본](../../runs/{m['run_id']}/artifacts/{m['case_id']}.json)"

t = s['total']
lines = ['# 복합 작업 5개 최종 실험 결과', '',
         f"5개 작업 × 3개 조건 × 3회, 총 **45회**를 실행했다. 필수 facts 전부 통과는 **{t['passed']}/45**, 개별 facts는 **{t['checks_passed']}/{t['checks_total']}**다.",
         '이 수치는 숫자·명시 제약 검사이며 설계 문서 완성도나 실제 구현·테스트 성공률이 아니다.', '',
         '## 고정 설정과 실행', '',
         f"실행 소스 `{s['manifest']['git_commit']}`. [사전 규약](../SUITE_FINAL_PROTOCOL.md), [manifest](manifest.json), [CSV](results.csv), [전체 지표](summary.json).",
         '모델 deepseek/deepseek-v4.1-flash / OpenRouter / Fireworks, temperature=0, max_tokens=2200, reasoning disabled.',
         '모든 단계 strict JSON Schema response_format. 공통 역할표 A/B/C 공유. 요청자는 상황별 역할이며 고정 관리자 없음. 장기 메모리 없음.',
         'max_depth=5, max_tasks=12, max_steps=4, max_calls=64, max_parallel=3. 외부 실험은 직렬, 사이 15초 대기, 실행당 600초 상한.',
         '429는 동일 payload로 최대 6회, 기타 오류 최대 2회. 실패한 전체 작업은 다시 실행하거나 결과를 교체하지 않았다.',
         'release-review는 위임을 요구하며 새 4개 작업은 직접 실행/위임을 모델이 선택한다. 제공된 합성 자료만 분석하며 웹 검색·실제 코드 실행은 없다.', '',
         '## 조건별 결과', '', '|조건|자동 통과|facts|LLM 호출|실행 중첩 관측|동일 Worker 중첩|루트 C|보고 비용|', '|---|---:|---:|---:|---:|---:|---:|---:|']
for c,g in s['conditions'].items():
    lines.append(f"|{c}|{g['passed']}/{g['attempts']}|{g['checks_passed']}/{g['checks_total']}|{g['calls']}|{g['parallel_runs']}|{g['same_worker_parallel_runs']}|{g['c_root_awards']}/{g['attempts']}|${g['reported_cost_usd']:.6f}|")
lines += ['', '|작업|baseline|homogeneous|overconfident|', '|---|---:|---:|---:|']
for case, groups in s['cases'].items():
    lines.append('|' + case + '|' + '|'.join(f"{groups[c]['passed']}/3" for c in ('baseline','homogeneous','overconfident')) + '|')
lines += ['', f"호출 {t['calls']}회, HTTP {t['http_requests']}회. 반환된 비용 합계 ${t['reported_cost_usd']:.6f}. 실행 간 대기를 포함한 첫 시작~마지막 종료 {(extra['wall_span_seconds']/60):.1f}분.",
          '응답 없는 요청의 실제 청구액은 알 수 없다. 기간 전체 계정 비용이나 사람 작업 시간과 동일하지 않다.', '',
          '## 실패·재시도·응답 검증', '',
          f"HTTP 오류: `{dict(errors)}`. 429 영향을 받은 논리 호출 {len(retry_calls)}개 중 {len(recovered)}개에서 이후 HTTP 응답을 받았다.",
          f"응답 종료 사유: `{dict(finishes)}`. length 응답은 2200토큰 상한에서 잘린 것이며 형식 검증 실패로 보존했다.",
          'request_checks는 실제 요청의 메시지/스키마/provider 일치 여부이고 response_checks는 실제 응답의 JSON/Schema 준수 여부다. 두 검사를 혼동하지 않는다.',
          f"실제 provider `{dict(providers)}`, model `{dict(models)}`.", '', '|검사|결과|', '|---|---|']
for k,v in s['integrity'].items():
    lines.append(f'|{k}|{v}|')
lines += ['', '아래 실패는 산출물 없는 실행도 0개 정답으로 포함한다. 부분 하위 산출물은 원본 runs 폴더와 열람용 문서에 남는다.', '',
          '|작업/조건/회차|상태|검사|원인/오답|', '|---|---|---:|---|']
for m in s['runs']:
    if not m['passed']:
        reason = '; '.join(x['task_id'] + ': ' + x['error'] for x in m['task_failures']) or ', '.join(m['wrong_or_missing'])
        lines.append(f"|{m['case_id']}/{m['condition']}/{m['block']}|{m['status']}|{m['checks_passed']}/{m['checks_total']}|{reason.replace('|','/')}|")
lines += ['', '## 재귀와 실제 병렬 실행', '', f"실제 최대 깊이 분포: `{t['depths']}`. 깊이 5는 허용 상한이며 실측 깊이와 다르다.",
          '병렬성은 execute/synthesize의 call_start~call_end 중첩으로 센다. 동시에 제안만 한 것은 병렬 작업으로 세지 않는다. HTTP 이벤트는 버퍼 기록이므로 그 시각으로 중첩을 계산하지 않는다.',
          '중첩이 있어도 작업이 성공했다는 뜻은 아니다. 표의 both succeeded는 해당 두 하위 작업만의 상태이며 최종 루트 성공과 별개다.', '',
          '|실행|동시 작업|Worker|초|두 작업 성공|', '|---|---|---|---:|---|']
for m in s['runs']:
    for o in m['overlaps']:
        lines.append(f"|{m['case_id']}/{m['condition']}/{m['block']}|{o['left']} + {o['right']}|{' + '.join(o['workers'])}|{o['seconds']:.3f}|{o['both_tasks_succeeded']}|")
lines += ['', '## 결정성 및 배정 쏠림', '', '|작업/조건|facts 있는 실행|서로 다른 facts 벡터|서로 다른 전체 산출물|서로 다른 루트 계획|', '|---|---:|---:|---:|---:|']
for case, groups in s['cases'].items():
    for c,g in groups.items():
        lines.append(f"|{case}/{c}|{g['runs_with_facts']}/3|{g['distinct_fact_vectors']}|{g['distinct_full_artifacts']}|{g['distinct_root_plans']}|")
lines += ['', 'facts 일치에 실패 실행은 포함되지 않으므로 항상 전체 통과율과 함께 본다. 전체 산출물/계획 hash는 표현과 작업 ID 차이도 반영하며 의미상 차이 크기를 재는 지표는 아니다.',
          '점수는 coverage/feasibility/verification 각 0~2로 평가하고 동점일 때 자기 확신도를 사용한다. 확신도를 가린 점수 평가만으로 과신의 영향을 제거하지 못할 수 있다.', '',
          '|조건|전체 C 배정|C의 95 이상 유효 입찰|최고 평가 점수가 동점인 배정|', '|---|---:|---:|---:|']
for c,g in s['conditions'].items():
    lines.append(f"|{c}|{g['awards'].get('C',0)}/{sum(g['awards'].values())}|{g['c_high_bids']}/{g['c_proposals']}|{ties[c]}|")
for item in score_exceptions:
    totals = {worker:sum(scores.values()) for worker,scores in item['scores'].items()}
    lines += ['', f"예외 `{item['run_id']}`: 평가 합계 {totals}, C 확신도 {item['c_confidence']}, 선정 {item['winner']}의 확신도 {item['winning_confidence']}. 점수가 낮은 과신 후보는 선정하지 않았다."]
replay = json.loads((B/'replay_verification.json').read_text())
lines += ['', f"[보조 재생 검증](replay_verification.json): {len(replay['runs'])}회, 전체 일치={replay['passed']}, 추가 API 요청 0.",
          '각 사례의 첫 baseline을 성공 여부와 무관하게 선택했다. 기록된 응답 고정 후 max_parallel=1/3, 인위적 100ms 지연으로 재생했다. 성공·실패 상태, 평가, 모든 하위 산출물, 호출 수를 비교했다.',
          '이는 동일 모델 응답이 주어진 실행기의 재현성을 확인하는 사후 구현 검사다. 실제 LLM 응답이 다시 같을 것이라는 보장은 아니다.', '',
          '## 산출물 품질 점검', '',
          '사전에 정한 cases/README.md 기준을 코딩 보조 에이전트가 성공한 루트의 summary/facts/evidence와 필요한 원본 로그에 대조했다. 별도의 맹검 심사나 객관적인 성능 척도는 아니다. 실패한 루트는 완성 산출물 없음으로 미충족이다.',
          f"판정 수: `{extra['qualitative_ratings']}`. 자동 facts 검사와 별개다. [45회 정성 점검과 산출물 모음](QUALITY_REVIEW.md), [기계 판독 기록](qualitative_notes.jsonl).", '',
          '|작업|충족|부분 충족|미충족|', '|---|---:|---:|---:|']
for case in s['cases']:
    counts = Counter(by_review[m['run_id']]['rating'] for m in s['runs'] if m['case_id'] == case)
    lines.append(f"|{case}|{counts['충족']}|{counts['부분 충족']}|{counts['미충족']}|")
quality = ['# 산출물 품질 점검과 원본 모음', '', '필수 facts 자동 검사와 별개인 정성 점검이다. 산출물은 수정하지 않았다.',
           '판정은 명시된 요구사항 대비 충족/부분 충족/미충족이며 실제 프로그램 검증 점수가 아니다.', '']
for m in s['runs']:
    q = by_review[m['run_id']]
    quality += [f"## {m['case_id']} / {m['condition']} / {m['block']}회 — {q['rating']}", '',
                f"필수 facts {m['checks_passed']}/{m['checks_total']}. 검토 범위: {q['review_scope']}.", '',
                '> ' + q['evidence'].replace('\n', '\n> '), '', q['notes'], '', link(m), '']
with (B / 'QUALITY_REVIEW.md').open('x') as f:
    f.write('\n'.join(quality))
lines += ['', '## 해석 범위와 다음 설계에 필요한 것', '',
          '- strict response_format와 로컬 검증은 형식 오류를 감지하지만, 토큰 상한에서 잘린 JSON을 완성하거나 설계 내용을 증명하지 않는다.',
          '- 독립 작업의 실행과 재귀 위임은 구현됐어도, 분해한 일의 범위와 통합 문서 길이는 별도로 제어해야 한다. 하위 작업이 원래 큰 요청을 반복하는지 점검할 필요가 있다.',
          '- 수치 검사는 코드 계산, 설계는 규칙-타입 대응·RNG 복원·상태 전이·원자성처럼 검증 가능한 계약으로 나누는 것이 다음 개선 후보이다. 이 batch에는 사후 수정을 적용하지 않았다.',
          '- 사례별 3회이고 작업별 분해 구조가 달라 조건의 일반적인 우월성이나 완전한 결정성을 주장할 수 없다. 과신 C의 배정 증가와 품질의 인과관계도 이 표만으로 단정하지 않는다.',
          '- 이 45회는 peer DAG 확장 실험이다. 고정 manager의 기본 강의 과제 results.csv와 합산하지 않는다. 기본 제출 체크 및 Smith 비교/해석은 별도 범위다.', '']
with (B / 'FINAL_REPORT.md').open('x') as f:
    f.write('\n'.join(lines))
print(json.dumps({'report': str(B/'FINAL_REPORT.md'), 'total': t, 'supplementary': extra},ensure_ascii=False,indent=2))
