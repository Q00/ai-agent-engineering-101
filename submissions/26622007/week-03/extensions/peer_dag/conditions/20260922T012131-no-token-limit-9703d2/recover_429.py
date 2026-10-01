"""One new attempt for each exhausted-429 run; original 45-run results stay intact."""
import asyncio
import json
from pathlib import Path
import subprocess
import sys

B = Path(__file__).resolve().parent
ROOT = B.parent.parent
sys.path.insert(0, str(ROOT))
import suite_study as suite


def selected_failure(metric):
    if metric['passed']:
        return False
    if metric['error'] == 'CallError: HTTP 429':
        return True
    rejections = metric.get('proposal_rejections', [])
    def all_rate_limited(rows):
        return bool(rows) and all(row['error'] in ('HTTP 429', 'CallError: HTTP 429') for row in rows)
    if metric['error'] == 'ValueError: no valid bids':
        return all_rate_limited(rejections)
    propagated = 'ValueError: child failed or blocked; partial results retained'
    if metric['error'] != propagated:
        return False
    # The root can report only a dependency failure while the actual 429 is below it.
    causes = [row for row in metric.get('task_failures', []) if row['error'] != propagated]
    return bool(causes) and all(
        row['error'] == 'CallError: HTTP 429' or (
            row['error'] == 'ValueError: no valid bids' and all_rate_limited([
                rejection for rejection in rejections if rejection.get('task_id') == row['task_id']]))
        for row in causes)


async def run(folder, manifest):
    metrics = []
    for item in manifest['schedule']:
        await asyncio.sleep(suite.GAP_SECONDS)
        if suite.frozen_sources() != manifest['source_hashes']:
            raise ValueError('source changed; existing recovery attempts retained')
        attempt = await suite.attempt_run(folder, item)
        metric = suite.inspect_run(attempt)
        suite.save(folder / (item['run_id'] + '.metrics.json'), metric)
        metrics.append(metric)
        print(json.dumps({'event': 'recovery_end', 'source_run_id': item['source_run_id'],
                          **{key: metric[key] for key in ('run_id', 'passed', 'status', 'http_429')}}, ensure_ascii=False), flush=True)
    caps, finish_reasons, response_count = [], {}, 0
    for metric in metrics:
        for row in map(json.loads, (ROOT / 'logs' / (metric['run_id'] + '.jsonl')).read_text().splitlines()):
            if row['event'] == 'http_request' and {'max_tokens', 'max_completion_tokens'} & row['payload'].keys():
                caps.append({'run_id': metric['run_id'], 'task_id': row['task_id'], 'phase': row['phase']})
            elif row['event'] == 'http_response':
                response_count += 1
                reason = json.loads(row['raw_response'])['choices'][0].get('finish_reason')
                finish_reasons[reason] = finish_reasons.get(reason, 0) + 1
    summary = {'scope': 'Supplementary recovery attempts; never replace or pool into original 45-run comparison.',
               'manifest': manifest, 'total': suite.group_metrics(metrics), 'runs': metrics,
               'all_requests_omit_token_limits': not caps, 'token_limit_requests': caps,
               'http_responses': response_count, 'finish_reasons': finish_reasons,
               'all_request_checks_passed': all(m['request_checks_passed'] for m in metrics),
               'all_response_checks_passed': all(m['response_checks_passed'] for m in metrics),
               'all_trace_checks_passed': all(m['trace']['passed'] for m in metrics)}
    suite.save(folder / 'summary.json', summary)
    lines = ['# 429 실패의 별도 복구 확인', '',
             f"본 45회 종료 후 429로 실패한 {len(metrics)}회를 각각 새 ID로 한 번씩 실행했다. 통과 {summary['total']['passed']}/{len(metrics)}.",
             '원래 실패와 본 실험의 45회 성공률은 변경하지 않았다. 아래 실행은 추가적인 복구 확인이다.',
             '[규약](../HTTP429_RECOVERY_PROTOCOL.md), [상세 지표](summary.json), [최초 45회 결과](../summary.json).', '',
             '|원래 실행|새 실행|결과|facts|429|', '|---|---|---|---:|---:|']
    for metric in metrics:
        lines.append(f"|{metric['source_run_id']}|{metric['run_id']}|{metric['status']} / passed={metric['passed']}|{metric['checks_passed']}/{metric['checks_total']}|{metric['http_429']}|")
    lines += ['', f"토큰 상한 부재={not caps}, 요청 검사={summary['all_request_checks_passed']}, 응답 검사={summary['all_response_checks_passed']}, 실행 추적 검사={summary['all_trace_checks_passed']}.",
              f"HTTP 요청 {summary['total']['http_requests']}회, 응답 {response_count}개, 종료 사유 {finish_reasons}.",
              f"응답 보고 비용 합계 ${summary['total']['reported_cost_usd']:.8f}. 없는 응답의 비용은 추정하지 않는다.",
              'facts 자동 검사는 산출물의 모든 설계·정책 내용이 맞다는 뜻이 아니다.', '']
    with (folder / 'REPORT.md').open('x') as stream:
        stream.write('\n'.join(lines))
    print(json.dumps({'event': 'recovery_complete', 'total': summary['total']}, ensure_ascii=False), flush=True)


def main():
    source = json.loads((B / 'summary.json').read_text())
    original = source['manifest']
    assert len(source['runs']) == original['planned_runs'] == 45
    assert suite.load_config()[0] == original['config']
    assert suite.frozen_sources() == original['source_hashes']
    selected = [m for m in source['runs'] if selected_failure(m)]
    schedule = [{key: m[key] for key in ('case_id', 'condition', 'block')} | {
        'source_run_id': m['run_id'],
        'run_id': original['batch_id'] + '-recovery-' + m['run_id'].removeprefix(original['batch_id'] + '-')}
        for m in selected]
    folder = B / 'recovery_429'
    folder.mkdir()
    manifest = {'source_batch': original['batch_id'], 'source_commit': original['git_commit'],
                'runner_commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
                'source_hashes': original['source_hashes'], 'config': original['config'], 'schedule': schedule,
                'protocol': '../HTTP429_RECOVERY_PROTOCOL.md', 'planned_runs': len(schedule),
                'deadline_seconds': suite.DEADLINE_SECONDS, 'inter_run_delay_seconds': suite.GAP_SECONDS}
    suite.save(folder / 'manifest.json', manifest)
    asyncio.run(run(folder, manifest))


if __name__ == '__main__':
    main()
