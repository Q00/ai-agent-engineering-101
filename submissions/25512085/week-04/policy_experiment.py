"""Separate Week 04 policy experiment; never changes baseline prompts/logs."""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

from acl import MAX_TURNS
from context_prompt import context_paragraph
from model_client import LMStudioCaller, Meter, ModelSettings
from negotiate import EpisodeResult, _finish
from protocol import ReaderMeter, read
from runner import append_row, completed_pairs, load_scenarios, result_row, retrying_caller


class PolicyCaller(LMStudioCaller):
    def chat(self, system: str, messages: list[dict[str, str]]) -> str:
        payload = {'model': self.settings.model,
                   'messages': [{'role': 'system', 'content': system}] + messages,
                   'temperature': self.settings.temperature, 'max_tokens': 256,
                   'stream': False, 'reasoning_effort': 'none',
                   'chat_template_kwargs': {'enable_thinking': False}}
        request = Request(self.settings.server_url + '/v1/chat/completions',
                          data=json.dumps(payload).encode('utf-8'),
                          headers={'Content-Type': 'application/json'}, method='POST')
        try:
            with urlopen(request, timeout=self.settings.timeout_seconds) as response:
                data = json.loads(response.read().decode('utf-8'))
        except HTTPError as exc:
            raise RuntimeError(f'LM Studio returned HTTP {exc.code}') from exc
        except URLError as exc:
            raise RuntimeError(f'cannot reach LM Studio: {exc.reason}') from exc
        content = data['choices'][0]['message'].get('content')
        self.last_stats = {'finish_reason': data['choices'][0].get('finish_reason'),
                           'usage': data.get('usage', {})}
        if not isinstance(content, str) or not content.strip():
            raise RuntimeError('LM Studio response has no message content')
        self.meter.calls += 1
        return content.strip()


def history_for(role: str, transcript: list[tuple[str, str]]) -> list[dict[str, str]]:
    if not transcript:
        return [{'role': 'user', 'content': 'Open the negotiation with a price proposal.'}]
    return [{'role': 'assistant' if speaker == role else 'user', 'content': text}
            for speaker, text in transcript]


def bounds(role: str, limit: int) -> int:
    # Integer prices: floor 120% budget, ceil 80% reserve, without float rounding.
    return limit * 120 // 100 if role == 'buyer' else (limit * 80 + 99) // 100


def outside(role: str, price: int, limit: int) -> bool:
    return price > limit if role == 'buyer' else price < limit


def eligible(role: str, price: int, limit: int) -> bool:
    return price <= bounds(role, limit) if role == 'buyer' else price >= bounds(role, limit)


def prompt(role: str, item: str, limit: int, condition: str, remaining: int, context=None) -> str:
    objective = ('You buy the item; lower prices benefit you. Your normal maximum budget is '
                 if role == 'buyer' else
                 'You sell the item; higher prices benefit you. Your normal minimum reserve is ')
    formats = {
        'free': 'Write one or two plain English sentences for the act. Then on separate lines write Reason: <Korean reason> and Discretion: yes or no.',
        'tagged': 'Start with exactly one tag (propose), (accept-proposal), (reject-proposal), or (refuse), then one English sentence. Then on separate lines write Reason: <Korean reason> and Discretion: yes or no.',
        'structured': 'Reply only with JSON: {"performative": "propose" | "accept-proposal" | "reject-proposal" | "refuse", "content": {"price": <integer or null>, "reason": "<Korean reason>", "discretion": <true or false>}}.',
    }
    return (f'You are the {role} of {item}. {objective}{limit}. '
            f'Your private exceptional boundary is {bounds(role, limit)}. '
            f'You have {remaining} discretionary trades left in this run of four scenarios. '
            + ('Keep your normal limit, exceptional boundary and remaining quota strictly private. '
               'Never state, quote, label or explain those values as your minimum, maximum, budget, '
               'reserve, limit or exception boundary in the English text or Korean Reason. '
               'Do not reveal the 20-percent rule or quota counts. You may state an offered or '
               'accepted price, including one equal to a private boundary, but never identify '
               'it as that boundary. Justify prices using item facts, convenience and your '
               'circumstances, not private-limit calculations. Keep the required Discretion '
               'metadata, but do not explain its numeric threshold. '
               if context is not None else
               'Never disclose this private quota or private limits unless needed in your own negotiation message. ')
            +
            'Use propose to offer an integer price; accept-proposal to accept the OTHER party\'s last proposal '
            'and end with a deal; reject-proposal to decline and continue; refuse to leave without a deal. '
            'The buyer must open with a price proposal. Use exactly one act. '
            'Your normal limit is binding unless you judge an exceptional price reasonable, have quota left, '
            'and remain inside your exceptional boundary. Do not propose or accept beyond that boundary. '
            'Set discretion yes/true only when proposing or accepting a price outside YOUR normal limit. '
            'A proposal authorizes that price if accepted; one quota is charged only when a deal closes. '
            'Give your own decision reason in Korean, 1 to 30 Unicode characters including spaces and punctuation. '
            'When using discretion, explain why that exceptional price is reasonable for YOUR role. '
            'Reasons are visible to the other party. Do not impersonate the other role. '
            + (context_paragraph(role, context) if context is not None else '')
            + formats[condition])


def metadata(raw: str, condition: str) -> tuple[str, str, bool]:
    if condition == 'structured':
        payload = json.loads(raw)
        content = payload['content']
        reason, discretion = content['reason'], content['discretion']
        body = json.dumps({'performative': payload['performative'],
                           'content': {'price': content['price']}}, ensure_ascii=False)
    else:
        match = re.fullmatch(r'(.*?)\nReason: ([^\n]+)\nDiscretion: (yes|no)', raw, re.S)
        if match is None:
            raise ValueError('missing/invalid Reason or Discretion lines')
        body, reason, flag = match.groups()
        discretion = flag == 'yes'
    if not isinstance(reason, str) or not 1 <= len(reason) <= 30:
        raise ValueError('reason must contain 1..30 Unicode characters')
    if not re.search('[가-힣]', reason):
        raise ValueError('reason must contain Korean text')
    if not isinstance(discretion, bool):
        raise ValueError('discretion must be boolean')
    return body, reason, discretion


def episode(scenario, condition, caller, quota, log, event, contextual=False):
    limits = {'buyer': scenario['budget'], 'seller': scenario['reserve']}
    result = EpisodeResult(str(scenario['id']), int(limits['seller'] <= limits['buyer']))
    proposals = {'buyer': None, 'seller': None}
    reader = ReaderMeter()
    quota_before = dict(quota)
    read_call = retrying_caller(caller, log)
    agent_call = retrying_caller(caller.chat, log)
    policy_rejections = 0
    uses = []
    for turn in range(MAX_TURNS):
        role, other = ('buyer', 'seller') if turn % 2 == 0 else ('seller', 'buyer')
        system = prompt(role, scenario['item'], limits[role], condition, quota[role],
                        context=scenario if contextual else None)
        messages = history_for(role, result.transcript)
        # Evidence includes only this caller's system prompt and exact API role mapping.
        event({'type': 'request', 'scenario': result.scenario_id, 'turn': turn+1,
               'role': role, 'system': system, 'messages': messages})
        raw = agent_call(system, messages)
        event({'type': 'agent_usage', 'scenario': result.scenario_id, 'turn': turn+1,
               'role': role, 'stats': getattr(caller, 'last_stats', {})})
        result.turns += 1
        log(f'[{role}] {raw}')
        try:
            body, reason, discretion = metadata(raw, condition)
            act, price, ok = read(condition, body, result.transcript, read_call, reader)
        except (ValueError, KeyError, TypeError) as exc:
            log(f'[metadata-error] {exc}')
            act, price, ok = None, None, False
            reason, discretion = '', False
        result.transcript.append((role, raw))
        log(f'[protocol] performative={act} price={price} parsed={ok} reason={reason!r} discretion={discretion} reader_calls={reader.calls}')
        event({'type': 'message', 'scenario': result.scenario_id, 'turn': turn+1,
               'role': role, 'raw': raw, 'performative': act, 'price': price,
               'parsed': ok, 'reason': reason, 'discretion': discretion})
        if not ok:
            result.format_errors += 1
            continue
        if act == 'propose':
            proposals[role] = (price, reason, discretion)
        elif act == 'refuse':
            result.outcome = 'no_deal'
            break
        elif act == 'accept-proposal':
            proposal = proposals[other]
            if proposal is None:
                policy_rejections += 1
                log('[policy] acceptance ignored: no opposing proposal')
                continue
            accepted = proposal[0]
            approvals = {role: (reason, discretion), other: (proposal[1], proposal[2])}
            affected = [speaker for speaker in limits if outside(speaker, accepted, limits[speaker])]
            if any(not eligible(speaker, accepted, limits[speaker]) or quota[speaker] < 1
                   or not approvals[speaker][1] for speaker in affected):
                policy_rejections += 1
                log('[policy] acceptance rejected: exception boundary, quota or explicit authorization')
                continue
            for speaker in affected:
                quota[speaker] -= 1
                use = {'role': speaker, 'price': accepted, 'normal_limit': limits[speaker],
                       'reason': approvals[speaker][0], 'remaining': quota[speaker]}
                uses.append(use)
                log('[discretion-used] ' + json.dumps(use, ensure_ascii=False))
            result.outcome, result.price = 'deal', accepted
            break
    result.reader_calls = reader.calls
    _finish(result, limits['seller'], limits['buyer'])
    effective_reserve = bounds('seller', limits['seller']) if quota_before['seller'] else limits['seller']
    effective_budget = bounds('buyer', limits['buyer']) if quota_before['buyer'] else limits['buyer']
    possible_policy = int(effective_reserve <= effective_budget)
    # Actual authorized deal is policy-valid even if the original correct remains zero.
    note = {'experiment': 'context20-v1' if contextual else 'policy20-history-v1',
            'quota_before': quota_before, 'quota_remaining': dict(quota),
            'discretion_uses': uses, 'policy_rejections': policy_rejections,
            'policy_valid_deal': int(result.outcome == 'deal'),
            'expanded_price_overlap': possible_policy}
    log(f'[episode] scenario={result.scenario_id} outcome={result.outcome} price={result.price} correct={result.correct} violation={result.violation} turns={result.turns} format_errors={result.format_errors} reader_calls={result.reader_calls}')
    return result, note


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--run-prefix')
    parser.add_argument('--context', action='store_true', help='use grounded item facts, private circumstances and bargaining guidance')
    args = parser.parse_args()
    if args.run_prefix is None:
        args.run_prefix = 'context20-v1-' if args.context else 'policy20-v2-'
    if args.context and not args.run_prefix.startswith('context20-'):
        raise SystemExit('context runs must use a context20- prefix to preserve earlier experiments')
    if not re.fullmatch(r'[A-Za-z0-9_-]+', args.run_prefix):
        raise SystemExit('run prefix must contain only letters, digits, hyphens and underscores')
    root = Path(__file__).resolve().parent
    results_path = root / 'results.csv'
    scenario_file = 'scenarios_context.json' if args.context else 'scenarios.json'
    scenarios = load_scenarios(root / scenario_file)
    if args.context:
        for scenario in scenarios:
            context_paragraph('buyer', scenario)
            context_paragraph('seller', scenario)
    completed = completed_pairs(results_path)
    plans = [(f'{args.run_prefix}{condition}-{repeat:02d}', condition)
             for condition in ('free', 'tagged', 'structured') for repeat in range(1,4)]
    if args.dry_run:
        print('pending_episodes=' + str(sum((run, str(s['id'])) not in completed for run, _ in plans for s in scenarios)))
        return
    settings = ModelSettings.from_env()
    for run, condition in plans:
        quota = {'buyer': 2, 'seller': 2}
        log_path = root / 'logs' / (run + '.txt')
        event_path = root / 'logs' / (run + '.jsonl')
        with log_path.open('a', encoding='utf-8') as console, event_path.open('a', encoding='utf-8') as events:
            def log(text):
                print(text, flush=True)
                print(text, file=console, flush=True)
            def event(data):
                print(json.dumps(data, ensure_ascii=False), file=events, flush=True)
            if log_path.stat().st_size == 0:
                log(f'provider=LM Studio model={settings.model} temperature={settings.temperature} max_turns={MAX_TURNS} condition={condition} run={run} agent_endpoint=/v1/chat/completions reader_endpoint=/api/v1/chat agent_reasoning_effort=none enable_thinking=false policy=20percent quota=2per-role-per-run reason_max_chars=30 scenario_file={scenario_file} contextual={args.context}')
            meter = Meter()
            caller = PolicyCaller(settings, meter)
            for scenario in scenarios:
                scenario_id = str(scenario['id'])
                if (run, scenario_id) in completed:
                    # Reconstruct shared quota in original scenario order from committed rows.
                    import csv
                    with results_path.open(encoding='utf-8', newline='') as handle:
                        previous = next(row for row in csv.DictReader(handle)
                                        if row['run'] == run and row['scenario'] == scenario_id)
                    previous_note = previous['note']
                    if previous_note.startswith('crash: '):
                        previous_note = previous_note[len('crash: '):]
                    quota = json.loads(previous_note)['quota_remaining']
                    log(f'[skip] scenario={scenario_id} quota={quota}')
                    continue
                log(f'[start] scenario={scenario_id} quota={quota}')
                calls_before = meter.calls
                try:
                    result, note = episode(scenario, condition, caller, quota, log, event,
                                           contextual=args.context)
                    note['all_model_calls'] = meter.calls-calls_before
                    append_row(results_path, result_row(run, condition, result, json.dumps(note, ensure_ascii=False)))
                except Exception as exc:
                    note = {'crash': f'{type(exc).__name__}: {exc}', 'quota_remaining': dict(quota)}
                    log('[crash] ' + json.dumps(note, ensure_ascii=False))
                    append_row(results_path, [run, condition, scenario_id, '', '', '', '', '', '', '', '', 'crash: ' + json.dumps(note, ensure_ascii=False)])
                    # Do not manufacture 35 further crash rows after a transport failure.
                    raise
                completed.add((run, scenario_id))


if __name__ == '__main__':
    main()
