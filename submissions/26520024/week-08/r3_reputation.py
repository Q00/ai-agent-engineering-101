"""Author-level vote memory and conservative quotes; never sends votes or spends."""
import hashlib
import math

from r3_common import parse_json, read_json, atomic_json
from r3_memory import packed


def vote_result(memory, row, location):
    sent = row.get('sent', {})
    if not isinstance(sent, dict) or 'vote' not in sent:
        return False
    answer = row.get('answer')
    try:
        answer = parse_json(answer) if isinstance(answer, str) else answer
    except ValueError:
        answer = None  # The kit truncates answers; never invent success or a zero fee.
    fields = ('fee', 'next_dislike_fee', 'dislike_power_unit', 'counted', 'error')
    safe_answer = {k: answer[k] for k in fields if k in answer} if isinstance(answer, dict) else {}
    value = sent.get('value')
    if type(value) is not int or not (value == 1 or value <= -1):
        return False
    data = {'post': sent['vote'], 'value': value, 'as': sent.get('as'),
            'status': row.get('status'), 'answer': safe_answer,
            'answer_complete': isinstance(answer, dict), 'time_basis': 'local_ingestion_not_vote_time'}
    return memory.record('forum_vote_result', location, data)


def dislike_quote(inventory, power, *, previous_power=1, new_buyer_stance=True, deals=None):
    if type(power) is not int or power < 1 or type(previous_power) is not int or previous_power < 1:
        raise ValueError('positive integer dislike power required')
    def valid(value):
        return type(value) in (int, float) and math.isfinite(value) and value >= 0
    unit = inventory.get('dislike_power_unit')
    fee = inventory.get('next_dislike_fee') if new_buyer_stance else 0
    delta = max(0, (power - 1) ** 2 - (previous_power - 1) ** 2)
    power_cost = delta * unit if valid(unit) else (0 if delta == 0 else None)
    total = power_cost + fee if power_cost is not None and valid(fee) else None
    return {'power': power, 'previous_power': previous_power, 'unit': unit,
            'incremental_power_cost': power_cost, 'new_stance_fee': fee, 'total_cost': total,
            'eligible': type(deals) is int and deals >= 50,
            'paid_budget': 0, 'auto_send': False,
            'reason': 'Observation only; no automatic free or paid voting. Fees require fresh authoritative fields.'}


def summarize(memory, inventory, now):
    posts = {}
    for event in memory.events('forum_claim', 10000):
        data = event['data']
        if type(data.get('id')) is int:
            posts.setdefault(data['id'], data)
    votes = {}
    unknown = []
    for event in memory.events('forum_vote_result', 10000):
        row = event['data']
        if type(row.get('status')) is not int or not 200 <= row['status'] < 300:
            continue
        votes.setdefault(row['post'], row)
    authors = {}
    for post, row in votes.items():
        author = posts.get(post, {}).get('author')
        if not isinstance(author, str):
            unknown.append(post)
            continue
        target = authors.setdefault(author, {'net': 0, 'paid_power_observed': 1, 'posts': [],
                                            'all_counted_confirmed': True})
        target['net'] += 1 if row['value'] > 0 else -1
        target['paid_power_observed'] = max(target['paid_power_observed'], -row['value'])
        target['posts'].append(post)
        target['all_counted_confirmed'] &= row.get('answer', {}).get('counted') is True
    for row in authors.values():
        row['stance'] = 'like' if row['net'] > 0 else 'dislike' if row['net'] < 0 else 'neutral'
        row['weight_estimate'] = (1 + math.log2(row['net']) if row['net'] > 0 else
                                 row['paid_power_observed'] if row['net'] < 0 else 0)
        row['confirmed_weight'] = row['weight_estimate'] if row['all_counted_confirmed'] else None
    snapshots = memory.events('score_snapshot', 1)
    own = snapshots[0]['data'].get('own', {}).get('buyer', {}) if snapshots else {}
    return {'observed_at': now, 'authors': authors, 'unresolved_posts': unknown[:100],
            'own_forum_rating': own.get('forum'),
            'rating_observed_at': snapshots[0]['ts'] if snapshots else None,
            'rating_source': 'server_author_rating_not_sum_of_post_ratings',
            'paid_voting_budget': 0, 'automatic_voting': False,
            'cost_examples': [dislike_quote(inventory, n, deals=own.get('deals')) for n in (1, 2, 4)],
            'limitations': ['Own account observed sends only; other devices/aliases may change the true stance.',
                           'Post disappearance does not revoke prior votes.',
                           'No claim of a complete voter graph or recomputed Wilson rating.']}


def maintain_reputation(memory, md, now):
    memory._consume('outbox.sent.jsonl', lambda row, key: vote_result(memory, row, key), located=True)
    inventory = read_json(md / 'inventory.json', {})
    report = summarize(memory, inventory, now)
    atomic_json(md / 'reputation-status.json', report)
    key = hashlib.sha256(packed(report).encode()).hexdigest()
    memory.record('reputation_snapshot', key, report, ts=now)
    return report
