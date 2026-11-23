"""Single-flight free dislikes, at most three distinct owners per rolling hour."""
import json,time
from pathlib import Path
from optimizer import read,atomic

def tick(md,enabled=True,posts=None,now=None):
    md=Path(md);now=time.time() if now is None else now
    if not enabled:return {'status':'disabled','queued':0}
    inv_path=md/'inventory.json';inv=read(inv_path,{})
    if not inv_path.exists() or not 0<=now-inv_path.stat().st_mtime<20:
        return {'status':'awaiting_fresh_fee','queued':0}
    if inv.get('next_dislike_fee')!=0:return {'status':'fee_not_zero','queued':0}
    path=md/'free-dislike-state.json';state=read(path,{'attempts':[],'owners':[]})
    attempts=state['attempts'];recent=[a for a in attempts if now-a['at']<3600]
    if len(recent)>=3:return {'status':'hourly_limit','queued':0}
    pending=[];box=md/'outbox.jsonl'
    if box.exists():
        for line in box.read_text(encoding='utf-8').splitlines():
            try:pending.append(json.loads(line))
            except ValueError:continue
    if any('vote' in p for p in pending) or (md/'outbox.taking').exists():
        return {'status':'pending_vote','queued':0}
    if attempts:
        last=attempts[-1];ack=None;sent=md/'outbox.sent.jsonl'
        if sent.exists():
            for line in sent.read_text(encoding='utf-8').splitlines():
                try:r=json.loads(line)
                except ValueError:continue
                if r.get('sent',{}).get('vote')==last['post']:ack=r
        if ack is None or inv_path.stat().st_mtime<=last['at']:
            return {'status':'awaiting_vote_ack','queued':0}
        try:answer=json.loads(ack.get('answer','{}'))
        except ValueError:answer={}
        if ack.get('status')!=200 or answer.get('fee')!=0:
            return {'status':'vote_guard_stopped','queued':0}
    posts=read(md/'forum.json',[]) if posts is None else posts
    eligible=[p for p in posts if p.get('role')=='buyer' and p.get('owner')
              and p['owner'].casefold()!='totato2203' and p.get('author')!='totato2203-r3'
              and p['owner'].casefold() not in state['owners'] and isinstance(p.get('id'),int)]
    from strategy_policy import load
    rank_targets={}
    if load(md).get('forum_stance_objective_enabled'):
        targets=read(md/'score-targets.json',{})
        if not 0<=now-targets.get('at',0)<900:return {'status':'awaiting_fresh_class_targets','queued':0}
        rank_targets={r['owner'].casefold():r for r in targets.get('competitive_forum_targets',[]) if isinstance(r.get('forum'),(int,float)) and 0<r['gap']<.3*r['forum']}
        eligible=[p for p in eligible if p['owner'].casefold() in rank_targets]
        eligible.sort(key=lambda p:rank_targets[p['owner'].casefold()]['gap'])
    if not rank_targets:eligible.sort(key=lambda p:(-p.get('likes',0),-p.get('ts',0)))
    if not eligible:return {'status':'no_new_owner','queued':0}
    post=eligible[0];request={'vote':post['id'],'value':-1,'as':'buyer'}
    attempts.append({'at':now,'post':post['id'],'owner':post['owner']})
    if rank_targets:attempts[-1].update(prior_forum=rank_targets[post['owner'].casefold()]['forum'],class_gap=rank_targets[post['owner'].casefold()]['gap'],effect='pending_observation_not_assumed')
    state['owners'].append(post['owner'].casefold());atomic(path,state)
    with box.open('a',encoding='utf-8') as out:out.write(json.dumps(request)+'\n')
    return {'status':'queued_free_dislike','queued':1,'post':post['id'],'owner':post['owner']}
