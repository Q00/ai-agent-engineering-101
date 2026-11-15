"""Shop strategist + inventory-analysis stage, isolated from the transaction path.

Default proposes only. All commands are fixed authenticated envelopes; all
policy writes go through PolicyStore. Run --once --no-model for a local audit.
"""
import argparse
from datetime import datetime
import fcntl
import json
import os
from pathlib import Path
import signal
import threading
import time
import uuid

from shop_ops_policy import (ROOT, PolicyStore, atomic_write, baseline_hash, config_at,
    current_state, digest, immutable_record, numeric, read_json, state_dir, validate_snapshot)
from shop_ops_evidence import collect_snapshot
from shop_ops_model import analyse
from shop_ops_notifications import LocalOutbox
import shop_ops_commands as commands


def rules_at(root, now):
    for path in (state_dir(root)/'rules.json', Path(root)/'shop-ops-rules.json'):
        try:
            row = read_json(path, 8192)
            if (isinstance(row, dict) and row.get('version') == '3.6' and row.get('round') == 3
                    and numeric(row.get('verified_at')) and 0 <= now-row['verified_at'] <= 86400):
                return row
        except (OSError, ValueError):
            pass
    return None


def refresh_public(root, now):
    """Two bounded fixed-origin GETs at most every 6h; no market writes."""
    import urllib.request
    import re
    folder = state_dir(root)
    control_path = folder/'public-control.json'
    try:
        previous = read_json(control_path)
    except (OSError, ValueError):
        previous = {}
    if numeric(previous.get('attempt_at')) and 0 <= now-previous['attempt_at'] < 21600:
        return
    atomic_write(control_path, {'attempt_at': now})
    for kind, url in (('rules', 'https://market.ourolabs.ai/rules'), ('board', 'https://market.ourolabs.ai/api/board')):
        try:
            with urllib.request.urlopen(url, timeout=5) as response:
                if response.geturl() != url:
                    raise ValueError('unexpected public redirect')
                data = response.read(1_000_001)
            if len(data) > 1_000_000:
                raise ValueError('oversize public evidence')
            if kind == 'rules':
                from html import unescape
                text = unescape(re.sub('<[^>]+>', ' ', data.decode()))
                text = ' '.join(text.split())
                latest = re.search(r'Patch\s+(\d+\.\d+)', text)
                if (not latest or latest[1] != '3.6' or '(list - price) / (list - cost)' not in text
                        or 'Nov 17, 23:59 KST' not in text or 'Stock still counts at cost when the round ends' not in text):
                    # A new/unknown rule blocks applying; do not silently use older rules.
                    atomic_write(folder/'rules-block.json', {'at': now, 'reason': 'rules_changed_or_unrecognized'})
                    continue
                rules = {'version': '3.6', 'round': 3, 'ends_at': '2026-11-17T23:59:00+09:00',
                         'verified_at': now, 'source': url, 'source_sha256': __import__('hashlib').sha256(data).hexdigest()}
                atomic_write(folder/'rules.json', rules)
                atomic_write(folder/'rules-block.json', {'at': now, 'reason': None})
            else:
                value = json.loads(data)
                atomic_write(folder/'board.json', board_metrics(value, now))
        except Exception as exc:
            immutable_record(root, 'observations', uuid.uuid4().hex,
                {'kind': kind, 'time': now, 'status': 'public_unavailable', 'error': type(exc).__name__})


def board_metrics(board, now):
    rows = board.get('shops') if isinstance(board, dict) else None
    if board.get('round') != 3 or not isinstance(rows, list) or len(rows) > 1000:
        raise ValueError('unsupported board')
    ranked = [r for r in rows if isinstance(r, dict) and numeric(r.get('all'))]
    own = next((r for r in ranked if r.get('name') == 'dyishappy'), None)
    if not own:
        raise ValueError('shop absent from board')
    components = {}
    for metric, weight in (('book', .5), ('revenue', .3), ('stars_rank', .2)):
        if not numeric(own.get(metric)) or any(not numeric(r.get(metric)) for r in ranked):
            components[metric] = {'status': 'unavailable'}
            continue
        # Strictly below matches rank-based opportunity without inventing class ranking.
        pct = sum(r[metric] < own[metric] for r in ranked)/max(1, len(ranked)-1)
        components[metric] = {'value': own[metric], 'percentile_strict_below': pct,
                              'weight': weight, 'remaining_weighted_room': weight*(1-pct)}
    return {'generated_at': now, 'round': 3, 'name': 'dyishappy', 'all': own['all'],
            'components': components, 'population': len(ranked),
            'limits': 'Everyone scored shops including house; ties use strict-below estimate, not a reconstruction of class ALL.'}


def snapshot(root, now, collector=collect_snapshot):
    rules = rules_at(root, now)
    result = collector(root, now=now, rules=rules)
    try:
        block = read_json(state_dir(root)/'rules-block.json')
        if block.get('reason'):
            result['application_ready'] = False
            result['blockers'].append('rules_changed_or_unrecognized')
    except (OSError, ValueError):
        pass
    if rules and numeric(result.get('market_now')):
        remaining = datetime.fromisoformat(rules['ends_at']).timestamp() - result['market_now']
        result['remaining_hours'] = remaining/3600
        # Allow the last experimental sale's maximum supported warranty to mature.
        # This guard affects new experiments only, never active transaction terms.
        if remaining < config_at(root)['experiment_seconds'] + 6 * 3600:
            result['application_ready'] = False
            result['blockers'].append('insufficient_time_for_experiment')
    try:
        board = read_json(state_dir(root)/'board.json')
        if numeric(board.get('generated_at')) and 0 <= now-board['generated_at'] <= 21600:
            result['public_score'] = board
    except (OSError, ValueError):
        pass
    result.pop('snapshot_id', None)
    result['snapshot_id'] = digest(result)
    path = state_dir(root)/'snapshots'/(result['snapshot_id']+'.json')
    if not path.exists():
        immutable_record(root, 'snapshots', result['snapshot_id'], result)
    return result


def target_key(row):
    return f"{row['sku']}:{row['grade']}:{row['warranty_hours']}"


def model_context(obs, state):
    candidates = [r for r in obs.get('candidates', []) if r.get('eligible') is True][:12]
    context = {'snapshot_id': obs['snapshot_id'], 'rules_version': obs.get('rules_version'),
        'blockers': obs['blockers'], 'remaining_hours': obs.get('remaining_hours'),
        'active_experiment': bool(state['changes'] and state['expires_at'] > obs['generated_at']),
        'diagnostics': {k: obs.get('diagnostics', {}).get(k) for k in ('tasks', 'response_timing', 'checkout_pipeline')},
        'current': {k: v for k,v in obs.get('current', {}).items() if k != 'groups'},
        'previous': {k: v for k,v in obs.get('previous', {}).items() if k != 'groups'},
        'eligible_targets': {target_key(r): r for r in candidates},
        'inventory': obs.get('inventory_analysis'), 'public_score': obs.get('public_score'),
        'limits': obs.get('limits', [])}
    # Remove optional detail, never truncate JSON or mandatory guards.
    if len(json.dumps(context, ensure_ascii=False)) > 24000:
        context['inventory'] = {k:v for k,v in (obs.get('inventory_analysis') or {}).items() if k != 'targets'}
    if len(json.dumps(context, ensure_ascii=False)) > 24000:
        context['inventory'] = {'status': 'details_omitted_for_budget'}
        context['eligible_targets'] = dict(list(context['eligible_targets'].items())[:2])
    return context


def validate_decision(raw, context):
    if isinstance(raw, str):
        if len(raw) > 8192: raise ValueError('oversize model answer')
        raw = json.loads(raw)
    if not isinstance(raw, dict) or set(raw) != {'action','targets','diagnosis','expected_effect'}:
        raise ValueError('invalid operations decision')
    if raw['action'] not in ('hold','experiment') or not isinstance(raw['targets'], list) or len(raw['targets']) > 2:
        raise ValueError('invalid operations action')
    if any(not isinstance(v,str) for v in raw['targets']) or len(set(raw['targets'])) != len(raw['targets']):
        raise ValueError('invalid target list')
    for field, limit in (('diagnosis',800),('expected_effect',500)):
        if not isinstance(raw[field],str) or not 1 <= len(raw[field]) <= limit:
            raise ValueError('invalid explanation')
    if raw['action'] == 'hold' and raw['targets'] or raw['action'] == 'experiment' and not raw['targets']:
        raise ValueError('action/targets mismatch')
    if raw['action'] == 'experiment' and (context['blockers'] or context['active_experiment']):
        raise ValueError('experiment blocked')
    if any(key not in context['eligible_targets'] for key in raw['targets']):
        raise ValueError('unverified model target')
    return raw


def make_proposal(root, obs, state, decision, cfg, request_id=None):
    changes = []
    rows = {target_key(r):r for r in obs.get('candidates', [])}
    for k in decision['targets']:
        r = rows[k]
        changes.append({key:r[key] for key in ('sku','grade','warranty_hours')} | {'before':0,'after':-1})
    now = obs['generated_at']
    proposal = {'schema':1, 'decision_id':uuid.uuid4().hex, 'base_version':state['version'],
        'baseline_hash':baseline_hash(root), 'snapshot_id':obs['snapshot_id'], 'rules_version':obs.get('rules_version'),
        'created_at':now, 'expires_at':now+cfg['proposal_ttl_seconds'], 'action':decision['action'],
        'family':'opening_price' if changes else 'hold', 'changes':changes, 'diagnosis':decision['diagnosis'],
        'evidence':{'snapshot_id':obs['snapshot_id'],'targets':decision['targets'],'coverage':obs.get('sources',{})},
        'expected_effect':decision['expected_effect'],
        'evaluation':{'at':now+cfg['evaluation_seconds'],'min_tasks':cfg['min_tasks'],
                      'min_settlements':cfg['min_settlements'],
                      'metrics':['refund_adjusted_profit_per_visit','settled_revenue_per_hour',
                                 'confirmed_sales','response_latency','refund_rate'],
                      'success':'Comparable mature per-visit net estimate and revenue/hour both improve; no new technical faults.'},
        'stop_conditions':['matched experiment technical errors','expiry','operator rollback',
                           'evaluated deterioration requires comparable samples and authenticated restore'],
        'request_id':request_id}
    immutable_record(root,'decisions',proposal['decision_id'],proposal)
    return proposal


def notify(root, proposal, action):
    return LocalOutbox(root).notify(request_id=proposal.get('request_id'),decision_id=proposal['decision_id'],
        problem=proposal['diagnosis'], evidence={'snapshot_id':proposal['snapshot_id'],'targets':proposal['changes']},
        action=action,review_at=proposal['evaluation']['at'])


def evaluate(root, proposal, obs, *, now):
    """Version-tagged cohort only. Predictions missing their target do not trigger rollback."""
    state = current_state(root)
    evaluation_path = state_dir(root)/'evaluation-control'/(proposal['decision_id']+'.json')
    try: prior = read_json(evaluation_path)
    except (OSError,ValueError): prior = {}
    if prior.get('terminal') is True:
        return prior['result']
    result = {'decision_id':proposal['decision_id'],'request_id':proposal.get('request_id'),
        'evaluated_at':now,'snapshot_id':obs['snapshot_id'],'base_version':proposal['base_version'],
        'policy_version':state['version'],'status':'hold','next_judgment':'continue_observing'}
    if state['decision_id'] != proposal['decision_id']:
        result.update(status='superseded',next_judgment='do_not_overwrite_newer_policy')
    elif not state['changes']:
        if state.get('reason') == 'expired':
            experiment = read_json(state_dir(root)/'history'/(state['parent_version']+'.json'))
            maturity_at=experiment['expires_at']+max(c['warranty_hours'] for c in experiment['changes'])*3600
            if now < maturity_at:
                result.update(status='waiting_maturity',next_judgment='observe_fixed_experiment_window_after_warranty',
                              maturity_at=maturity_at,experiment_version=experiment['version'])
            else:
                from shop_ops_evidence import evaluate_windows
                fixed=evaluate_windows(root,version=experiment['version'],start=experiment['created_at'],
                    end=experiment['expires_at'],as_of=now,rules=rules_at(root,now))
                targets={target_key(c) for c in proposal['changes']}
                after=[g for g in fixed['experiment']['groups'] if target_key(g) in targets]
                before=[g for g in fixed['baseline']['groups'] if target_key(g) in targets]
                enough=(sum(g['tasks'] for g in after)>=proposal['evaluation']['min_tasks']
                    and sum(g['confirmed_sales'] for g in after)>=proposal['evaluation']['min_settlements']
                    and before and after and all(g.get('sufficient_maturity') for g in before+after))
                outcome='inconclusive_coverage_or_sample'
                if enough and fixed.get('comparable') is True:
                    profit=lambda rows:sum(g['net_estimate'] for g in rows)/max(1,sum(g['tasks'] for g in rows))
                    revenue=lambda rows:sum(g['settled_revenue_per_hour'] for g in rows)
                    outcome='observed_improvement' if profit(after)>profit(before) and revenue(after)>revenue(before) else 'review_required'
                result.update(status='completed',next_judgment=outcome,fixed_windows=fixed,
                    caution='Observational before/after; demand, buyer mix and supply can confound effects. No automatic performance rollback.')
        else:
            result.update(status='rolled_back' if state.get('reason') in ('operator_rollback','technical_error','evaluated_regression') else 'completed',
                          next_judgment=state.get('reason','baseline'))
    else:
        cohort = obs.get('policy_cohorts',{}).get(state['version'], {})
        target = {target_key(c) for c in proposal['changes']}
        groups = [g for g in cohort.get('groups',[]) if target_key(g) in target]
        tasks = sum(g.get('tasks',0) for g in groups)
        settlements = sum(g.get('confirmed_sales',0) for g in groups)
        technical = sum(g.get('errors',0)+g.get('settlement_refused',0) for g in groups)
        result['observed'] = {'tasks':tasks,'confirmed_sales':settlements,'technical_errors':technical,'groups':groups}
        if technical:
            restored = PolicyStore(root).restore(state['version'], now=now,reason='technical_error',authorized=True)
            result.update(status='rolled_back',restored_version=restored['version'],next_judgment='technical_investigation')
        elif now >= state['expires_at']:
            restored = PolicyStore(root).restore(state['version'], now=now,reason='expired',authorized=True)
            result.update(status='waiting_maturity',restored_version=restored['version'],
                maturity_at=state['expires_at']+max(c['warranty_hours'] for c in state['changes'])*3600,
                next_judgment='expiry_stops_new_quotes_not_proof_of_failure')
        elif now < proposal['evaluation']['at']:
            result.update(status='waiting',next_judgment='evaluation_not_due')
        elif (tasks < proposal['evaluation']['min_tasks'] or settlements < proposal['evaluation']['min_settlements']
              or not groups or any(not g.get('sufficient_maturity') for g in groups)):
            result.update(status='insufficient_sample',next_judgment='hold_until_more_mature_observations_or_expiry')
        else:
            # Before/after window metrics are descriptive and confounded by market
            # demand/supply and mix; require explicit review, never blind auto rollback.
            original = read_json(state_dir(root)/'snapshots'/(proposal['snapshot_id']+'.json'))
            before = [g for g in original.get('current',{}).get('groups',[]) if target_key(g) in target]
            result.update(status='review_required',next_judgment='compare_demand_supply_grade_mix_and_then_decide',
                before=before,after=groups,confounders={
                    'current_supply':obs.get('inventory_analysis',{}).get('next_window_seconds'),
                    'source_coverage':obs.get('sources',{}), 'market_demand_change':'not_causally_identified',
                    'grade_mix':'targets are matched by SKU, grade and warranty; buyer mix remains unknown'})
    immutable_record(root,'evaluations',uuid.uuid4().hex,result)
    if result['status'] not in ('waiting','hold') and result['status'] != prior.get('status'):
        LocalOutbox(root).notify(request_id=proposal.get('request_id'),decision_id=proposal['decision_id'],
            problem='Experiment evaluation', evidence=result,action=result['status'],review_at=now+1800)
    rid = proposal.get('request_id')
    if rid:
        restored = result['status']=='rolled_back' or result['status']=='completed' and state.get('reason') in ('operator_rollback','technical_error','evaluated_regression')
        state_name = 'rolled_back' if restored else 'completed' if result['status'] in ('completed','superseded') else 'verifying'
        commands.transition(root,rid,state_name,now=now,result=result['status'],decision_id=proposal['decision_id'])
    atomic_write(evaluation_path,{'status':result['status'], 'terminal':result['status'] in ('completed','rolled_back','superseded'),
                                 'result':result,'time':now})
    return result


def process_requests(root, obs, *, now):
    """Durable received/analysing requests resume after process restart."""
    for path in sorted((state_dir(root)/'requests').glob('*.json')):
        row = read_json(path)
        if row['state'] in ('proposed','applied') and row.get('decision_id'):
            # Crash recovery after proposal/apply acknowledgement. Never rerun a
            # model or silently reapply a proposal that was not committed.
            current=current_state(root)
            if current.get('decision_id')==row['decision_id']:
                atomic_write(state_dir(root)/'verification'/(row['decision_id']+'.json'),{'request_id':row['request_id']})
                commands.transition(root,row['request_id'],'verifying',now=now,result='recovered_committed_policy')
            else:
                commands.transition(root,row['request_id'],'verifying',now=now,result='proposal_only_or_interrupted')
                commands.transition(root,row['request_id'],'completed',now=now,result='proposal_not_applied')
            continue
        if row['state'] not in ('received','analysing'): continue
        rid, cmd, args = row['request_id'],row['command'],row['args']
        commands.transition(root,rid,'analysing',now=now)
        try:
            if cmd=='status':
                result={'version':current_state(root)['version'],'snapshot_id':obs['snapshot_id'],'blockers':obs['blockers']}
            elif cmd=='analyse':
                # Claim request for this pass; strategist decision follows below.
                return rid
            elif cmd=='rollback':
                result=PolicyStore(root).restore(args['expected_version'],now=now,authorized=True)
                commands.transition(root,rid,'rolled_back',now=now,result=result['version'])
                LocalOutbox(root).notify(request_id=rid,decision_id=result['decision_id'],problem='Operator rollback',
                    evidence={'expected_version':args['expected_version']},action='rolled_back',review_at=now)
                if result.get('decision_id'):
                    previous=read_json(state_dir(root)/'decisions'/(result['decision_id']+'.json'))
                    try: previous['request_id']=read_json(state_dir(root)/'verification'/(result['decision_id']+'.json'))['request_id']
                    except (OSError,ValueError): pass
                    evaluate(root,previous,obs,now=now)
                continue
            else:
                proposal=read_json(state_dir(root)/'decisions'/(args['decision_id']+'.json'))
                if cmd=='evaluate':
                    result=evaluate(root,proposal,obs,now=now)
                else:
                    if args['expected_version']!=proposal['base_version']: raise ValueError('request version conflict')
                    current = current_state(root)
                    if (current.get('decision_id') == proposal['decision_id']
                            and current.get('parent_version') == args['expected_version']):
                        result=current # recover crash after active pointer committed, before acknowledgement
                    else:
                        result=PolicyStore(root).apply(proposal,obs,now=now,authorized=True)
                    # Preserve immutable decision; associate this apply request with
                    # its verification in a separate mapping.
                    atomic_write(state_dir(root)/'verification'/(proposal['decision_id']+'.json'),{'request_id':rid})
                    commands.transition(root,rid,'applied',now=now,result=result['version'],decision_id=proposal['decision_id'])
                    commands.transition(root,rid,'verifying',now=now,result='pending_evaluation')
                    notify(root,{**proposal,'request_id':rid},'applied')
                    continue
            commands.transition(root,rid,'verifying',now=now,result='validated')
            commands.transition(root,rid,'completed',now=now,result=result)
            LocalOutbox(root).notify(request_id=rid,decision_id=row.get('decision_id'),problem='Operator request result',
                evidence={'snapshot_id':obs['snapshot_id']},action=result,review_at=now)
        except Exception as exc:
            commands.transition(root,rid,'completed',now=now,result={'status':'rejected','error':type(exc).__name__})
            LocalOutbox(root).notify(request_id=rid,decision_id=None,problem='Operator request rejected',
                evidence={'error':type(exc).__name__},action='no policy change',review_at=now)
    return None


def tick(root=ROOT, *, now=None, model=analyse, no_model=False, collector=collect_snapshot, allow_network=False):
    root, now = Path(root), time.time() if now is None else now
    cfg = config_at(root)
    if not cfg['enabled']: return {'status':'disabled'}
    folder = state_dir(root)
    folder.mkdir(parents=True,exist_ok=True,mode=0o700)
    fd=os.open(folder/'daemon.lock',os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'a') as lock:
        try: fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError: return {'status':'another_operations_cycle_running'}
        state = current_state(root)
        try: control=read_json(folder/'control.json')
        except (OSError,ValueError): control={}
        pending=any(read_json(p).get('state') in ('received','analysing','proposed','applied') for p in (folder/'requests').glob('*.json'))
        from payment_memory import recent_jsonl
        recent=recent_jsonl(root/'logs/inventory-seller-events.jsonl', limit=32768, rows=50)
        errors=[e for e in recent if numeric(e.get('time')) and control.get('last_observed',0)<e['time']<=now
                and e.get('reason_code') in ('model_error','internal_error','inventory_unavailable')]
        checkout=recent_jsonl(root/'.market/checkout-events.jsonl', limit=32768, rows=50)
        refusals=[e for e in checkout if numeric(e.get('time'))
                  and max(control.get('last_observed',0),now-cfg['interval_seconds'])<e['time']<=now
                  and e.get('stage')=='settlement_refused'
                  and isinstance(e.get('task'),str) and e['task']
                  and isinstance(e.get('unit'),str) and e['unit']]
        # Checkout rows have no policy/round tag. Wake only; evaluate() must join
        # task + unit to the current experiment before any technical rollback.
        technical=bool(refusals) or any(e.get('ops_policy_version')==state['version'] for e in errors)
        elapsed=now-control.get('last_decision',float('-inf'))
        event=bool(errors or refusals) and elapsed>=cfg['event_min_seconds']
        watching=bool(state['changes']) or state.get('reason')=='expired'
        evaluation_due=watching and (bool(state['changes']) and now>=state['expires_at']
            or now-control.get('last_evaluation',0)>=cfg['interval_seconds'] or technical)
        if not pending and not event and elapsed<cfg['interval_seconds'] and not evaluation_due:
            return {'status':'cadence_wait'}
        if allow_network: refresh_public(root,now)
        obs = snapshot(root,now,collector)
        control['last_observed']=now
        if state.get('decision_id') and (evaluation_due or pending):
            proposal=read_json(folder/'decisions'/(state['decision_id']+'.json'))
            try: proposal['request_id']=read_json(folder/'verification'/(proposal['decision_id']+'.json'))['request_id']
            except (OSError,ValueError): pass
            evaluation=evaluate(root,proposal,obs,now=now)
            control['last_evaluation']=now
            state=current_state(root)
        request_id=process_requests(root,obs,now=now)
        state=current_state(root) # an operator request may have changed policy
        if not request_id and elapsed<cfg['interval_seconds'] and not event:
            atomic_write(folder/'control.json',control)
            return {'status':'cadence_wait','snapshot_id':obs['snapshot_id']}
        context=model_context(obs,state)
        if state.get('reason')=='expired':
            try:
                pending_eval=read_json(folder/'evaluation-control'/(state['decision_id']+'.json'))
                if not pending_eval.get('terminal'):
                    context['active_experiment']=True
            except (OSError,ValueError): context['active_experiment']=True
        control.update(last_decision=now)
        attempts=[t for t in control.get('model_attempts',[]) if numeric(t) and 0<=now-t<86400]
        decision={'action':'hold','targets':[], 'diagnosis':'Hold: observations unavailable or model not requested.',
                  'expected_effect':'Keep validated trading policy; collect enough fresh evidence.'}
        if cfg['model_enabled'] and not no_model and not obs['blockers'] and not context['active_experiment'] and len(attempts)<cfg['max_calls_per_day']:
            control['model_attempts']=attempts+[now]
            atomic_write(folder/'control.json',control) # failed calls also consume the budget
            try: decision=validate_decision(model(context,root,cfg),context)
            except Exception as exc:
                decision['diagnosis']='Hold: operations model failed validation ('+type(exc).__name__+').'
        else:
            control['model_attempts']=attempts
            decision['diagnosis']='Hold: '+(', '.join(obs['blockers']) if obs['blockers'] else
                'existing experiment/evaluation pending' if context['active_experiment'] else 'model disabled/no-model or daily call budget')
        atomic_write(folder/'control.json',control)
        proposal=make_proposal(root,obs,state,decision,cfg,request_id)
        action='hold' if proposal['action']=='hold' else 'proposed'
        if request_id: commands.transition(root,request_id,'proposed',now=now,decision_id=proposal['decision_id'])
        if cfg['mode']=='auto' and proposal['action']=='experiment':
            try:
                fresh=snapshot(root,time.time() if abs(time.time()-now)<120 else now,collector)
                applied=PolicyStore(root).apply(proposal,fresh,now=fresh['generated_at'])
                action='applied'
                if request_id: commands.transition(root,request_id,'applied',now=now,result=applied['version'])
            except (OSError,ValueError,TypeError,KeyError) as exc:
                action='apply_rejected_'+type(exc).__name__
        immutable_record(root,'outcomes',proposal['decision_id'],{'decision_id':proposal['decision_id'],
            'request_id':request_id,'time':now,'result':action,'next_judgment':proposal['evaluation']})
        notify(root,proposal,action)
        if request_id:
            commands.transition(root,request_id,'verifying',now=now,result=action)
            if action!='applied': commands.transition(root,request_id,'completed',now=now,result=action)
        return {'status':action,'decision_id':proposal['decision_id'],'snapshot_id':obs['snapshot_id']}


def main(argv=None):
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--root',type=Path,default=ROOT)
    ap.add_argument('--once',action='store_true')
    ap.add_argument('--no-model',action='store_true')
    ap.add_argument('--refresh-public',action='store_true',help='Explicit read-only rules/board refresh')
    ap.add_argument('--init-key',type=Path,help='Create an operator-owned 0600 HMAC key; never prints it')
    ap.add_argument('--request',type=Path,help='Authenticated JSON request, never a shell command')
    ap.add_argument('--key-file',type=Path)
    ap.add_argument('--command',choices=sorted(commands.COMMANDS),help='Sign and process one fixed local operator command')
    ap.add_argument('--request-id',default=None)
    ap.add_argument('--decision-id')
    ap.add_argument('--expected-version')
    args=ap.parse_args(argv)
    if args.init_key:
        commands.init_key(args.init_key)
        print('Operator key created; keep it outside deployment sources.')
        return 0
    if args.request and args.command: ap.error('choose --request or --command')
    if args.request or args.command:
        if not args.key_file: ap.error('--request requires --key-file')
        envelope=read_json(args.request,8192) if args.request else commands.signed_request(
            args.command,key_path=args.key_file,request_id=args.request_id or 'operator-'+uuid.uuid4().hex,
            args={k:v for k,v in {'decision_id':args.decision_id,'expected_version':args.expected_version}.items() if v is not None})
        record=commands.accept(args.root,envelope,key_path=args.key_file)
        print(json.dumps({'request_id':record['request_id'],'state':record['state']}))
        # Keep processing to a final result/proposal; daemon later verifies an applied experiment.
    stop=threading.Event()
    for sig in (signal.SIGTERM,signal.SIGINT): signal.signal(sig,lambda *_:stop.set())
    while not stop.is_set():
        try:
            result=tick(args.root,no_model=args.no_model,allow_network=args.refresh_public or not args.once)
            print(json.dumps(result),flush=True)
        except Exception as exc:
            immutable_record(args.root,'errors',uuid.uuid4().hex,{'time':time.time(),'error':type(exc).__name__,
                'effect':'operations cycle failed; transaction process and baseline policy remain independent'})
            print('[shop-ops] cycle failed: '+type(exc).__name__,flush=True)
            if args.once: return 1
        if args.once: return 0
        stop.wait(60) # decisions remain 30m; error/expiry/requests checked between decisions
    return 0


if __name__=='__main__':
    raise SystemExit(main())
