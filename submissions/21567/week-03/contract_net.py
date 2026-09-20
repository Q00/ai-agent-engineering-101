"""Contract Net negotiation, followed by actual execution."""
import json
import math
from agent import execute

ROLES = {
    'arithmetic': 'You specialize in arithmetic calculations.',
    'extraction': 'You specialize in extracting dates from text.',
    'sorting': 'You specialize in sorting numeric lists.'
}
CONDITIONS = ('baseline', 'homogeneous', 'overconfident')
BID = ('Decide whether to bid on this task based on your specialty. '
       'Return only JSON with participate (boolean), confidence (number 0 to 1), '
       'reason (string). Do not solve the task. Use confidence below 1 unless certain.')


def role_for(name, condition):
    return 'You are a generalist for arithmetic, date extraction, and numeric sorting.' if condition == 'homogeneous' else ROLES[name]


def parse_bid(raw):
    data = json.loads(raw)
    if not isinstance(data, dict) or type(data.get('participate')) is not bool:
        raise ValueError('invalid participate')
    c = data.get('confidence')
    if type(c) not in (int, float) or not math.isfinite(c) or not 0 <= c <= 1:
        raise ValueError('invalid confidence')
    if not isinstance(data.get('reason'), str):
        raise ValueError('invalid reason')
    return {key: data[key] for key in ('participate', 'confidence', 'reason')}


def choose(bids):
    eligible = [b for b in bids if b['participate']]
    return min(eligible, key=lambda b: (-b['confidence'], b['contractor'])) if eligible else None


def matches(answer, expected):
    try:
        actual = json.loads(answer)
    except (ValueError, TypeError):
        return False
    # bool is a subclass of int in Python; do not accept true as the number 1.
    def equal(a, b):
        if isinstance(b, list):
            return isinstance(a, list) and len(a) == len(b) and all(equal(x, y) for x, y in zip(a, b))
        return type(a) in (int, float) and a == b if type(b) in (int, float) else type(a) is type(b) and a == b
    return equal(actual, expected)


def run(tasks, condition, chat, emit):
    if condition not in CONDITIONS:
        raise ValueError('unknown condition')
    metrics = dict(tasks=len(tasks), correct=0, messages=0, unassigned=0, misawards=0)
    execution = dict(success=0, failed=0, invalid_bids=0)
    for task in tasks:
        public = {'id': task['id'], 'desc': task['desc']}
        bids = []
        for name in ROLES:
            emit('announcement', contractor=name, task=public)
            metrics['messages'] += 1
            prompt = role_for(name, condition) + '\n' + BID
            if condition == 'overconfident' and name == 'arithmetic':
                prompt += '\nAlways participate in every task with confidence 1.0, regardless of specialty.'
            msg = chat([{'role': 'system', 'content': prompt}, {'role': 'user', 'content': json.dumps(public)}])
            metrics['messages'] += 1
            raw = msg.get('content') or ''
            try:
                bid = parse_bid(raw)
            except (ValueError, TypeError):
                execution['invalid_bids'] += 1
                bid = dict(participate=False, confidence=0, reason='invalid_json_bid')
            bid['contractor'] = name
            emit('bid', task=task['id'], raw=raw, **bid)
            bids.append(bid)
        winner = choose(bids)
        if winner is None:
            metrics['unassigned'] += 1
            emit('unassigned', task=task['id'])
            continue
        name = winner['contractor']
        metrics['messages'] += 1
        correct = name == task['gold']
        metrics['correct'] += int(correct)
        metrics['misawards'] += int(not correct)
        emit('award', task=task['id'], contractor=name)
        result = execute(chat, role_for(name, condition), public, emit)
        success = result['status'] == 'completed' and matches(result['answer'], task['expected'])
        execution['success' if success else 'failed'] += 1
        emit('evaluation', task=task['id'], contractor=name, gold=task['gold'], expected=task['expected'], success=success, **result)
    return metrics, execution
