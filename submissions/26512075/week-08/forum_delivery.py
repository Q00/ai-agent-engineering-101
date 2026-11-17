"""Response IDs and append-only reconciliation evidence; never posts to the market."""
import json
import math
from pathlib import Path
import time


def positive_id(value):
    return type(value) is int and value > 0


def extract_post_id(payload):
    if not isinstance(payload, dict):
        return None
    values = [payload[k] for k in ('id', 'post_id') if k in payload]
    post = payload.get('post')
    if isinstance(post, dict):
        values.extend(post[k] for k in ('id', 'post_id') if k in post)
    elif post is not None:
        values.append(post)
    # Conflicting/malformed candidate fields are evidence problems, not guesses.
    return values[0] if values and all(positive_id(v) and v == values[0] for v in values) else None


def resolved(state, history):
    latest = {p.get('id', f'legacy:{i}'):p for i,p in enumerate(history)}
    for key, entry in list(latest.items()):
        if not isinstance(key,str) or len(key) != 64 or any(c not in '0123456789abcdef' for c in key):
            continue
        path = Path(state)/'forum-reconciled'/f'{key}.json'
        if not path.exists():
            continue
        recovery = json.loads(path.read_text())
        if (recovery.get('delivery_id') != key or recovery.get('market') != entry.get('market')
                or recovery.get('role') != entry.get('as')
                or recovery.get('account') != entry.get(entry.get('as'))
                or not positive_id(recovery.get('post_id'))):
            raise ValueError('invalid forum reconciliation identity')
        latest[key] = {**entry, 'state':'sent', 'post_id':recovery['post_id'],
                       'sent_at':entry.get('sent_at') or recovery['posted_at'],
                       'post_id_status':'reconciled',
                       'next_at':max(entry.get('next_at',0),recovery['posted_at']+300)}
    return list(latest.values())


def reconcile(state, market, delivery_id, snapshot):
    state = Path(state)
    history = [json.loads(l) for l in (state/'forum-delivery.jsonl').read_text().splitlines() if l.strip()]
    matches = [p for p in resolved(state,history) if p.get('id') == delivery_id and p.get('market') == market]
    if len(matches) != 1:
        raise ValueError('delivery not found for this market')
    entry = matches[0]
    if positive_id(entry.get('post_id')):
        return {'status':'already_known','post_id':entry['post_id']}
    if entry.get('state') not in ('sent','unknown','inflight','held_unknown'):
        raise ValueError('delivery is not eligible for reconciliation')
    posts = json.loads(Path(snapshot).read_text())
    if not isinstance(posts,list):
        raise ValueError('forum snapshot must be an array')
    candidates = {}
    account = entry[entry['as']]
    end = entry.get('sent_at') or entry['at']+120
    for p in posts:
        ts = p.get('ts')
        refs = p.get('refs',[])
        ref_ids = [r.get('seq') if isinstance(r,dict) else r for r in refs] if isinstance(refs,list) else None
        if (positive_id(p.get('id')) and p.get('role') == entry['as'] and p.get('author') == account
                and p.get('text') == entry['post'] and p.get('thread') is None
                and ref_ids == entry.get('refs',[]) and type(ts) in (int,float) and math.isfinite(ts)
                and entry['at']-5 <= ts <= end+5):
            candidates[p['id']] = p
    if len(candidates) != 1:
        raise ValueError(f'expected one exact matching post; found {len(candidates)}; no repost or journal change')
    post = next(iter(candidates.values()))
    recovery = {'delivery_id':delivery_id,'market':market,'role':entry['as'],'account':account,
                'post_id':post['id'],'posted_at':post['ts'],'reconciled_at':time.time(),
                'source_snapshot':str(Path(snapshot).resolve()),'matched_post':post}
    directory = state/'forum-reconciled'
    directory.mkdir(exist_ok=True)
    with (directory/f'{delivery_id}.json').open('x') as stream:
        json.dump(recovery,stream,ensure_ascii=False,indent=2)
    return {'status':'reconciled','post_id':post['id']}
