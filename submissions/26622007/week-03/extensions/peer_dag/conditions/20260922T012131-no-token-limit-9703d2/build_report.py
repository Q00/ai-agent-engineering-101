"""Derive the final evidence report from immutable live traces and review notes."""
from collections import Counter, defaultdict
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
token_caps, completion_tokens = [], []
root_inputs = defaultdict(lambda: {'payloads': set(), 'replies': set(), 'request_runs': set(), 'reply_runs': set()})
for m in s['runs']:
    rows = list(map(json.loads, (ROOT / 'logs' / (m['run_id'] + '.jsonl')).read_text().splitlines()))
    elapsed_times += [datetime.fromisoformat(row['at']) for row in rows if row['event'] in ('run_start', 'run_end')]
    endings = []
    for row in rows:
        e = row['event']
        key = (m['run_id'], row.get('task_id'), row.get('contractor'), row.get('phase'))
        if e == 'http_request':
            if {'max_tokens', 'max_completion_tokens'} & row['payload'].keys():
                token_caps.append({'run_id': m['run_id'], 'task_id': row['task_id'], 'phase': row['phase']})
            if row['task_id'] == m['case_id'] and row['phase'] == 'propose':
                group = root_inputs[(m['case_id'], m['condition'], row['contractor'])]
                group['payloads'].add(fingerprint(row['payload']))
                group['request_runs'].add(m['run_id'])
        elif e == 'http_http_error':
            errors[str(row['status'])] += 1
            if row['status'] == 429:
                retry_calls.add(key)
        elif e == 'http_response':
            raw = json.loads(row['raw_response'])
            reason = raw['choices'][0].get('finish_reason')
            finishes[str(reason)] += 1
            completion_tokens.append(raw.get('usage', {}).get('completion_tokens'))
            if reason != 'stop':
                endings.append({'task_id': row['task_id'], 'phase': row['phase'], 'finish_reason': reason,
                                'completion_tokens': raw.get('usage', {}).get('completion_tokens')})
            if key in retry_calls:
                recovered.add(key)
        elif e == 'http_usage':
            providers[row.get('provider', 'unknown')] += 1
            models[row.get('model', 'unknown')] += 1
        elif e == 'model_reply' and row['task_id'] == m['case_id'] and row['phase'] == 'propose':
            group = root_inputs[(m['case_id'], m['condition'], row['worker'])]
            group['replies'].add(fingerprint(row['raw']))
            group['reply_runs'].add(m['run_id'])
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
         'token_limit_requests': token_caps,
         'all_requests_omit_token_limits': not token_caps,
         'maximum_completion_tokens': max((n for n in completion_tokens if n is not None), default=0),
         'responses_over_2200_tokens': sum(n is not None and n > 2200 for n in completion_tokens),
         'responses_missing_token_usage': completion_tokens.count(None),
         'rate_limited_logical_calls': len(retry_calls), 'rate_limited_calls_with_http_response': len(recovered),
         'providers': dict(providers), 'models': dict(models), 'score_tied_awards': dict(ties), 'failures': failures,
         'overconfident_root_exceptions': score_exceptions,
         'wall_span_seconds': (max(elapsed_times) - min(elapsed_times)).total_seconds(),
         'qualitative_ratings': dict(Counter(r['rating'] for r in reviews))}
extra['root_proposal_groups'] = [
    {'case_id': case, 'condition': condition, 'worker': worker,
     'request_runs': len(g['request_runs']), 'reply_runs': len(g['reply_runs']),
     'distinct_payloads': len(g['payloads']), 'distinct_raw_replies': len(g['replies']),
     'payload_hashes': sorted(g['payloads']), 'reply_hashes': sorted(g['replies'])}
    for (case, condition, worker), g in sorted(root_inputs.items())]
extra['identical_root_inputs_across_all_repeats'] = len(root_inputs) == 45 and all(
    len(g['payloads']) == 1 and len(g['request_runs']) == 3 for g in root_inputs.values())
extra['root_awards_by_condition'] = {c: dict(Counter(m['root_award'] or 'unawarded' for m in s['runs'] if m['condition'] == c))
                                     for c in s['conditions']}
with (B / 'supplementary_metrics.json').open('x') as f:
    json.dump(extra, f, ensure_ascii=False, indent=2); f.write('\n')

def link(m):
    target = ROOT / 'runs' / m['run_id'] / 'artifacts' / (m['case_id'] + '.json')
    original = (f"[원본](../../runs/{m['run_id']}/artifacts/{m['case_id']}.json)" if target.exists()
                else f"[원본 로그](../../logs/{m['run_id']}.jsonl)")
    return f"[문서](documents/{m['run_id']}.md) / {original}"

t = s['total']
old = json.loads((B.parent / '20260921T113158-suite-ab4e67/summary.json').read_text())
preflight = json.loads((B / 'comparison_preflight.json').read_text())
old_by_slot = {(m['case_id'], m['condition'], m['block']): m for m in old['runs']}
paired = Counter((old_by_slot[(m['case_id'], m['condition'], m['block'])]['passed'], m['passed']) for m in s['runs'])
lines = ['# 출력 토큰 상한 제거 후 45회 재실험 결과', '',
         f"5개 작업 × 3개 조건 × 3회, 총 **45회**를 실행했다. 필수 facts 전부 통과는 **{t['passed']}/45**, 개별 facts는 **{t['checks_passed']}/{t['checks_total']}**다.",
         '이 수치는 숫자·명시 제약 검사이며 설계 문서 완성도나 실제 구현·테스트 성공률이 아니다.', '',
         '## 이전 결과 정정과 비교', '',
         '이전 max_tokens=2200은 사용자가 요청한 조건이 아니라 구현 에이전트가 임의로 넣은 상한이었다. 이전 17회 JSON 잘림을 시스템 자체의 능력 한계로 해석해서는 안 된다.',
         '[기존 원본 결과](../20260921T113158-suite-ab4e67/FINAL_REPORT.md)는 보존했다. 이번에는 실패 17회만 고르지 않고 동일 45개 슬롯을 모두 재실행했다.',
         '[사전 대조](comparison_preflight.json)에서 설정의 유일한 변경은 max_tokens 삭제이며 작업/정답, 모델 프롬프트/역할, 스키마, 스케줄러, 실행 순서가 동일함을 확인했다.',
         '두 실험의 시간 차이와 모델 비결정성도 있으므로 차이 전부를 상한 제거의 인과 효과라고 단정하지 않는다.', '',
         '이번에는 Fireworks upstream_provider_shared_pool 429가 길게 이어진 구간이 있다. 전체 통과율과 소요 시간은 공급자 가용성에도 영향을 받으므로, 출력 잘림·응답 스키마 실패·내용 오답과 분리해서 해석한다.', '',
         '|조건|기존 2200 상한|이번 요청 상한 생략|', '|---|---:|---:|',
         *[f"|{c}|{old['conditions'][c]['passed']}/15|{s['conditions'][c]['passed']}/15|" for c in s['conditions']],
         f"|전체|{old['total']['passed']}/45|{t['passed']}/45|", '',
         f"동일 작업/조건/회차 대조: 실패→통과 {paired[(False, True)]}, 실패→실패 {paired[(False, False)]}, 통과→통과 {paired[(True, True)]}, 통과→실패 {paired[(True, False)]}.",
         f"실제 요청 토큰 상한 필드 없음: {extra['all_requests_omit_token_limits']}. 2200토큰 초과 응답 {extra['responses_over_2200_tokens']}개, 최장 {extra['maximum_completion_tokens']} completion tokens.",
         '생략 시 provider 기본값이 적용된다. [OpenRouter 문서](https://openrouter.ai/docs/api/reference/parameters)를 기준으로 API 요청에 애플리케이션 상한을 넣지 않은 실험이며, 제공업체 자체 한도가 무한이라는 주장은 아니다.', '',
         '## 고정 설정과 실행', '',
         f"실행 소스 `{s['manifest']['git_commit']}`. [사전 규약](../SUITE_NO_TOKEN_LIMIT_PROTOCOL.md), [manifest](manifest.json), [CSV](results.csv), [전체 지표](summary.json).",
         '모델 deepseek/deepseek-v4.1-flash / OpenRouter / Fireworks, temperature=0, max_tokens와 max_completion_tokens 생략, reasoning disabled.',
         '모든 단계 strict JSON Schema response_format. 공통 역할표 A/B/C 공유. 요청자는 상황별 역할이며 고정 관리자 없음. 장기 메모리 없음.',
         'max_depth=5, max_tasks=12, max_steps=4, max_calls=64, max_parallel=3. 외부 실험은 직렬, 사이 15초 대기, 실행당 600초 상한.',
         '소켓 30초, 응답 수신 기한 90초/2MB 및 기존 JSON Schema의 필드 크기/업무 검증은 유지했다.',
         '429는 동일 payload로 최대 6회, 기타 오류 최대 2회. 최초 45회 결과는 교체하지 않았다. 429 소진 실패만 별도 recovery batch에서 한 번씩 재실행했으며 아래 본 실험 통계에 합산하지 않는다.',
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
          f"응답 종료 사유: `{dict(finishes)}`. 이번 요청에는 2200토큰 상한이 없다. length 종료나 잘못된 JSON이 있으면 제공업체 기본 제한 및 응답 원문과 함께 별도로 기록한다.",
          'request_checks는 실제 요청의 메시지/스키마/provider 일치 여부이고 response_checks는 실제 응답의 JSON/Schema 준수 여부다. 두 검사를 혼동하지 않는다.',
          'HTTP 오류만 있고 모델 응답이 0개인 실행도 response_checks=false다. 이는 생성된 JSON이 스키마를 어긴 것과 다르므로 응답 수와 오류 원문을 함께 확인한다.',
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
          f"최초 루트 제안의 실제 요청 payload가 같은 작업·조건·Worker의 3회 반복에서 동일했는가: {extra['identical_root_inputs_across_all_repeats']}. 총 {len(root_inputs)}개 묶음의 입력/응답 hash와 응답 받은 반복 수를 supplementary_metrics.json에 보존했다. 재시도 요청을 별도 반복으로 세지 않는다.",
          f"루트 배정 분포(배정 전 실패 포함): `{extra['root_awards_by_condition']}`. 표의 C/전체 시도 비율과 실제 배정이 있었던 루트에서의 비율을 혼동하지 않는다.",
          '점수는 coverage/feasibility/verification 각 0~2로 평가하고 동점일 때 자기 확신도를 사용한다. 확신도를 가린 점수 평가만으로 과신의 영향을 제거하지 못할 수 있다.', '',
          '|조건|전체 C 배정|C의 95 이상 유효 입찰|최고 평가 점수가 동점인 배정|', '|---|---:|---:|---:|']
for c,g in s['conditions'].items():
    lines.append(f"|{c}|{g['awards'].get('C',0)}/{sum(g['awards'].values())}|{g['c_high_bids']}/{g['c_proposals']}|{ties[c]}|")
for item in score_exceptions:
    totals = {worker:sum(scores.values()) for worker,scores in item['scores'].items()}
    lines += ['', f"예외 `{item['run_id']}`: 평가 합계 {totals}, C 확신도 {item['c_confidence']}, 선정 {item['winner']}의 확신도 {item['winning_confidence']}. 점수가 낮은 과신 후보는 선정하지 않았다."]
lines += ['', '실행 전 오프라인 검사 96개(기본 30, peer 49, 중단된 research 호환성 17)를 통과했다. 직렬화 요청과 모든 단계/조건에서 토큰 상한 부재 및 strict response_format 유지를 검사했다.',
          '이전 batch의 [고정 응답 재생 10회](../20260921T113158-suite-ab4e67/replay_verification.json)는 당시 구현 검사 기록이며 이번 45회 결과에 합산하지 않는다. 이번 스케줄러 코드는 변경하지 않았다.', '',
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
recovery = json.loads((B / 'recovery_429/summary.json').read_text())
rg = recovery['total']
lines += ['', '## HTTP 429 실패의 별도 복구 실행', '',
          f"본 실험 종료 뒤 원래 429 실패 {rg['attempts']}개를 각각 한 번 더 실행했다. 복구 자동 통과는 {rg['passed']}/{rg['attempts']}다. 원래 45회 성공률이나 실패 기록은 바꾸지 않았다.",
          f"별도 복구까지 포함해 통과 산출물을 확보한 원래 슬롯은 {t['passed'] + rg['passed']}/45다. 이는 최초 시도 성공률이 아니라 복구를 포함한 산출물 확보 수다.",
          f"복구 실행의 실제 깊이 분포는 {rg['depths']}, 작업 중첩은 {rg['parallel_runs']}회, 동일 Worker 중첩은 {rg['same_worker_parallel_runs']}회다. 최초 45회와 분리한 수치다.",
          '[복구 규약](HTTP429_RECOVERY_PROTOCOL.md), [복구 실행 결과](recovery_429/REPORT.md), [복구 산출물 점검](recovery_429/QUALITY_REVIEW.md).',
          '복구에서는 공헌이익 facts 키가 기술 계획의 개발 여유 시간으로 바뀌는 의미 충돌도 관측했다. [원본 추적](recovery_429/FACT_KEY_COLLISION.md)에 48000/240000원과 16/-16시간의 합성 과정을 기록했다. 해당 결과는 14/16이며 JSON Schema만으로 키의 단위를 보장하지 못했다.',
          '복구 r1-growth-roadmap-overconfident에서는 운영 하위 결과의 고객 수 내림 누락을 통합 과정이 감지해 880000/3520000원으로 수정했다. [해당 산출물](recovery_429/documents/20260922T012131-no-token-limit-9703d2-recovery-r1-growth-roadmap-overconfident.md)에 성공한 교정 사례도 보존했다.',
          '다른 제공업체의 구조화 출력 호환성 진단도 별도 진행했다. [진단 원문](../../../../diagnostics/NO_TOKEN_LIMIT_PROVIDER_DIAGNOSTICS.md)은 본 45회 및 복구 통계에 포함하지 않았다. 모든 본 실험과 복구는 Fireworks를 유지했다.', '',
          '## 해석 범위', '',
          '- strict response_format와 로컬 검증은 형식 오류를 감지하며 설계 내용의 정확성을 보장하지 않는다.',
          '- 출력 토큰 상한을 API 요청에서 생략했으며 제공업체 자체 기본 한도와 기존 JSON Schema/전송/작업 자원 제약은 남아 있다.',
          '- 수치 검사는 코드 계산, 설계는 규칙-타입 대응·RNG 복원·상태 전이·원자성처럼 검증 가능한 계약으로 나누는 것이 다음 개선 후보이다. 이 batch에는 사후 수정을 적용하지 않았다.',
          '- 사례별 3회이고 작업별 분해 구조가 달라 조건의 일반적인 우월성이나 완전한 결정성을 주장할 수 없다. 과신 C의 배정 증가와 품질의 인과관계도 이 표만으로 단정하지 않는다.',
          '- 이 45회는 peer DAG 확장 실험이다. 고정 manager의 기본 강의 과제 results.csv와 합산하지 않는다. 기본 제출 체크 및 Smith 비교/해석은 별도 범위다.', '']
with (B / 'FINAL_REPORT.md').open('x') as f:
    f.write('\n'.join(lines))
print(json.dumps({'report': str(B/'FINAL_REPORT.md'), 'total': t, 'supplementary': extra},ensure_ascii=False,indent=2))
