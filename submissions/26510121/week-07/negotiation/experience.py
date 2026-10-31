"""Persistent, numeric trading experience shared by buyer, seller and kit.

The SQLite journal is append-only and keyed by observed event identity. Model
summaries and counterparty prose never become trusted facts or instructions.
The live database is ignored; source decisions and feedback remain in logs.
"""
from collections import Counter, defaultdict
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import sqlite3
from statistics import median
import time

from negotiation.config import ROOT

POLICY = 'ranking-v2'
DEFAULT_PATH = ROOT / '.market' / 'experience.sqlite3'
ITEM_CONTRACT = 'course-kit-v3:item-only'


class Experience:
    def __init__(self, path=DEFAULT_PATH):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            db.execute('PRAGMA journal_mode=WAL')
            db.executescript('''
                CREATE TABLE IF NOT EXISTS events (
                    id TEXT PRIMARY KEY, at REAL NOT NULL, kind TEXT NOT NULL,
                    item TEXT, task TEXT, counterparty TEXT, payload TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS event_retrieval ON events(kind,item,at);
                CREATE INDEX IF NOT EXISTS task_feedback ON events(task,kind);
                CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY,value TEXT NOT NULL);
            ''')

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=10)
        try:
            with db:
                yield db
        finally:
            db.close()

    def put(self, kind, identity, payload, *, item=None, task=None, counterparty=None, at=None):
        event_id = hashlib.sha256((kind + ':' + identity).encode()).hexdigest()
        with self.connection() as db:
            cursor = db.execute('INSERT OR IGNORE INTO events VALUES (?,?,?,?,?,?,?)',
                (event_id, time.time() if at is None else at, kind, item, task, counterparty,
                 json.dumps(payload, separators=(',', ':'), allow_nan=False)))
            return cursor.rowcount == 1

    def set_round(self, number):
        if type(number) is not int or not 0 <= number <= 100:
            raise ValueError('invalid_market_round')
        with self.connection() as db:
            db.execute('INSERT OR REPLACE INTO meta VALUES (?,?)', ('round', str(number)))

    def current_round(self):
        with self.connection() as db:
            row = db.execute("SELECT value FROM meta WHERE key='round'").fetchone()
        return int(row[0]) if row else 2

    def contract_scope(self):
        with self.connection() as db:
            row=db.execute("SELECT value FROM meta WHERE key='contract_scope'").fetchone()
        return row[0] if row else ITEM_CONTRACT

    def set_contract_scope(self, value):
        if not isinstance(value,str) or not 1<=len(value)<=160:
            raise ValueError('invalid_contract_scope')
        with self.connection() as db:
            db.execute('INSERT OR REPLACE INTO meta VALUES (?,?)',('contract_scope',value))

    def buyer_observation(self, record, *, round_number=2, policy=POLICY):
        allowed = ('name','item','budget','outcome','price','seconds','turns','task_id',
                   'sequence','signature_verified','epoch','pubkey','order_id','error_code','policy_arm')
        data = {k: record.get(k) for k in allowed}
        if type(data.get('price')) is float and data['price'].is_integer():
            data['price'] = int(data['price'])
        data.update(round=round_number, policy=policy,contract_scope=self.contract_scope())
        identity = str(data.get('order_id') or data.get('task_id') or f"{data['name']}:{data['epoch']}:{data['sequence']}")
        return self.put('buyer_observation', identity, data, item=data['item'],
                        task=data['task_id'], counterparty=data['name'], at=data['epoch'])

    def buyer_feedback(self, *, task, order, verified, settled, round_number, ledger=None,
                       total_seconds=None, processing_seconds=None, wait_seconds=None, failed_checks=None):
        if not task and not order:
            return False
        inserted=self.put('buyer_feedback', str(order),
                        {'verified': verified is True, 'settled': settled is True,
                         'round': round_number, 'ledger': ledger,'order':order,
                         'total_seconds':total_seconds,'processing_seconds':processing_seconds,
                         'wait_seconds':wait_seconds,'failed_checks':failed_checks or []}, task=task)
        if inserted:self.update_buyer_critic(str(order))
        return inserted

    def seller_decision(self, *, task, item, reserve, turn, action, price, buyer_offer,
                        round_number=None, policy=POLICY, at=None, policy_arm=None, elapsed_seconds=None):
        inserted=self.put('seller_decision', f'{task}:{turn}',
                        {'reserve':reserve,'turn':turn,'action':action,'price':price,
                         'buyer_offer':buyer_offer,'round':round_number if round_number is not None else self.current_round(),
                         'policy':policy,'policy_arm':policy_arm,'elapsed_seconds':elapsed_seconds,
                         'contract_scope':self.contract_scope()}, item=item, task=task, at=at)
        if inserted:self.update_shop_critic(task)
        return inserted

    def seller_end(self, task, item, outcome, *, at=None):
        inserted=self.put('seller_end', f'{task}:{outcome}', {'outcome':outcome}, item=item,task=task,at=at)
        if inserted:self.update_shop_critic(task)
        return inserted

    def seller_settlement(self, *, task, item, price, order, ledger):
        inserted=self.put('seller_settled', str(order), {'price':price,'ledger':ledger,'round':3}, item=item,task=task)
        if inserted:self.update_shop_critic(task)
        return inserted

    def event(self,kind,identity):
        event_id=hashlib.sha256((kind+':'+str(identity)).encode()).hexdigest()
        with self.connection() as db:
            row=db.execute('SELECT at,payload FROM events WHERE id=?',(event_id,)).fetchone()
        return ({**json.loads(row[1]),'_at':row[0]} if row else None)

    def buyer_prediction(self,order,shop,item,band,round_number,arm,forecast):
        if not order:return False
        inserted=self.put('buyer_prediction',str(order),{'order':order,'name':shop['name'],'pubkey':shop.get('pubkey'),
            'item':item,'budget_band':band,'round':round_number,'contract_scope':self.contract_scope(),
            'arm':arm,'forecast':forecast},item=item,counterparty=shop['name'])
        if inserted:self.update_buyer_critic(str(order))
        return inserted

    def update_buyer_critic(self,order):
        from negotiation.world_model import critique
        prediction=self.event('buyer_prediction',order)
        feedback=self.event('buyer_feedback',order)
        observation=self.event('buyer_observation',order)
        if not prediction or not feedback or not observation or prediction['round']!=feedback['round']:return None
        if prediction['contract_scope']!=observation.get('contract_scope',ITEM_CONTRACT):return None
        success=feedback['verified'] and (prediction['round']<3 or feedback['settled'])
        reward=max(0,observation['budget']-observation['price']) if success and observation.get('price') is not None else 0
        seconds=feedback.get('total_seconds') or observation.get('seconds') or 1
        value=critique(prediction['forecast'],reward,seconds,success,attribution_unknown=observation['outcome']=='local_failure')
        value.update(item=prediction['item'],round=prediction['round'],contract_scope=prediction['contract_scope'],
                     budget_band=prediction['budget_band'],arm=prediction['arm'],order=order,
                     reward_basis='market verified Saved' if prediction['round']<3 else 'market settled Saved')
        self.put('buyer_critic',order,value,item=prediction['item'],counterparty=prediction['name'])
        return value

    def buyer_critic_summary(self,item,band,round_number):
        scope=self.contract_scope()
        with self.connection() as db:
            pending=db.execute("SELECT payload FROM events WHERE kind='buyer_prediction' AND item=? ORDER BY at DESC LIMIT 200",(item,)).fetchall()
            evaluated={json.loads(v)['order'] for (v,) in db.execute("SELECT payload FROM events WHERE kind='buyer_critic' AND item=?",(item,))}
        missing=[json.loads(v)['order'] for (v,) in pending if json.loads(v)['order'] not in evaluated]
        for order in missing[:20]:self.update_buyer_critic(order)
        with self.connection() as db:
            rows=db.execute("SELECT payload FROM events WHERE kind='buyer_critic' AND item=? ORDER BY at DESC LIMIT 200",(item,)).fetchall()
        groups=defaultdict(lambda:{'samples':0,'reward':0,'seconds':0,'brier':[]})
        for (payload,) in rows:
            row=json.loads(payload)
            if row['round']!=round_number or row['budget_band']!=band or row['contract_scope']!=scope:continue
            value=groups[row['arm']];value['samples']+=1;value['reward']+=row['reward'];value['seconds']+=row['cycle_seconds']
            if row['brier_error'] is not None:value['brier'].append(row['brier_error'])
        arms={name:{'samples':v['samples'],'observed_saved_per_second':v['reward']/max(1,v['seconds']),
                    'mean_brier_error':sum(v['brier'])/len(v['brier']) if v['brier'] else None} for name,v in groups.items()}
        control=arms.get('v1-selection-control',{});candidate=arms.get('saved-rate-selection',{})
        prefer=control.get('samples',0)>=10 and candidate.get('samples',0)>=10 and candidate['observed_saved_per_second']<0.8*control['observed_saved_per_second']
        return {'arms':arms,'prefer_control':prefer,'control_share':0.5 if prefer else 0.10,
                'scope':'same item, budget band, Round and contract; guardrail, not causal proof'}

    def shop_prediction(self,task,item,arm,forecast):
        first=self.event('seller_decision',f'{task}:1')
        if not first:return False
        inserted=self.put('shop_prediction',task,{'task':task,'item':item,'arm':arm,'round':first['round'],
            'reserve':first['reserve'],'contract_scope':first.get('contract_scope',ITEM_CONTRACT),
            'began_at':first['_at']-(first.get('elapsed_seconds') or 0),'forecast':forecast},item=item,task=task)
        if inserted:self.update_shop_critic(task)
        return inserted

    def update_shop_critic(self,task,*,now=None):
        from negotiation.world_model import critique
        p=self.event('shop_prediction',task)
        if not p:return None
        now=time.time() if now is None else now
        with self.connection() as db:
            payments=db.execute("SELECT at,payload FROM events WHERE kind='seller_settled' AND task=? ORDER BY at DESC LIMIT 1",(task,)).fetchall()
            endings=db.execute("SELECT payload FROM events WHERE kind='seller_end' AND task=?",(task,)).fetchall()
            decisions=db.execute("SELECT payload FROM events WHERE kind='seller_decision' AND task=? ORDER BY at DESC",(task,)).fetchall()
        accepted=next((json.loads(v) for (v,) in decisions if json.loads(v).get('action')=='accept'),None)
        terminal=any(json.loads(v)['outcome'] in {'reject','cancel'} for (v,) in endings)
        if any(json.loads(v)['outcome']=='local_failure' for (v,) in endings):return None
        if payments and p['round']<3:return None
        success=bool(payments) if p['round']>=3 else bool(accepted)
        if not success and not terminal and now-p['began_at']<600:return None
        price=json.loads(payments[0][1])['price'] if payments else accepted['price'] if p['round']<3 and accepted else p['reserve']
        reward=max(0,price-p['reserve']) if success else 0
        elapsed=(payments[0][0]-p['began_at'] if payments else accepted.get('elapsed_seconds') if accepted and p['round']<3 else min(600,now-p['began_at']))
        value=critique(p['forecast'],reward,elapsed or 1,success)
        revision=('paid-'+str(json.loads(payments[0][1])['ledger'])) if payments else 'agreement' if success else 'fixed-horizon'
        value.update(task=task,item=p['item'],round=p['round'],contract_scope=p['contract_scope'],arm=p['arm'],
                     reward_basis='market settled Profit' if p['round']>=3 else 'signed agreement margin proxy, not market settlement',revision=revision)
        self.put('shop_critic',f'{task}:{revision}',value,item=p['item'],task=task)
        return value

    def capability(self, name, pubkey, available, *, at=None):
        stamp=time.time() if at is None else at
        scope=self.contract_scope()
        return self.put('shop_capability',f'{name}:{pubkey}:{stamp}:{scope}',
                        {'pubkey':pubkey,'available':available is True,'contract_scope':scope},counterparty=name,at=stamp)

    def buyer_evidence(self, item, budget, identities, *, round_number=None, now=None):
        """Comparable identity/Round/budget evidence, including failed order cycles."""
        from negotiation.ranking import budget_band
        now=time.time() if now is None else now
        rnd=self.current_round() if round_number is None else round_number
        scope=self.contract_scope()
        with self.connection() as db:
            final_rows=db.execute("SELECT task,payload FROM events WHERE kind='buyer_feedback' ORDER BY at").fetchall()
            observations=db.execute("SELECT at,payload FROM events WHERE kind='buyer_observation' ORDER BY at").fetchall()
            capabilities=db.execute("SELECT at,counterparty,payload FROM events WHERE kind='shop_capability' ORDER BY at").fetchall()
        by_task,by_order={},{}
        for task,payload in final_rows:
            data=json.loads(payload)
            if task:by_task[task]=data
            if data.get('order'):by_order[data['order']]=data
        grouped=defaultdict(list)
        for at,payload in observations:
            data=json.loads(payload);data['_at']=at
            grouped[data['name']].append(data)
        result={}
        for name,key in identities.items():
            all_rows=grouped[name]
            same_identity=[r for r in all_rows if r.get('pubkey')==key and key]
            same_key=[r for r in same_identity if r.get('item')==item and r.get('contract_scope',ITEM_CONTRACT)==scope]
            comparable=[r for r in same_key if r.get('round')==rnd and budget_band(item,r['budget'])==budget_band(item,budget)]
            finalized=[];wins=[];durations=[];failure_reasons=Counter();pending=local_failures=0
            for row in comparable[-200:]:
                feedback=by_order.get(row.get('order_id')) or by_task.get(row.get('task_id'))
                if row['outcome']=='local_failure':
                    local_failures+=1
                    continue
                if feedback is None:
                    pending+=1
                    continue
                finalized.append(row)
                success=feedback.get('verified') is True and (rnd<3 or feedback.get('settled') is True)
                if success and row.get('outcome')=='purchased' and isinstance(row.get('price'),(int,float)) and row['price']<=budget:
                    wins.append(row)
                duration=feedback.get('total_seconds')
                if not isinstance(duration,(int,float)):duration=row.get('seconds')
                if isinstance(duration,(int,float)) and duration>0:durations.append(duration)
                failure_reasons.update(feedback.get('failed_checks') or [])
            verified_prices=[]
            for row in same_key:
                feedback=by_order.get(row.get('order_id')) or by_task.get(row.get('task_id')) or {}
                if feedback.get('verified') is True and (row['round']<3 or feedback.get('settled') is True) and type(row.get('price')) in {int,float}:
                    verified_prices.append(row['price'])
            recent=[r for r in same_identity if now-r['_at']<1800 and r.get('contract_scope',ITEM_CONTRACT)==scope]
            last_outage=max([0]+[r['_at'] for r in recent if r['outcome']=='unavailable'])
            last_purchase=max([0]+[r['_at'] for r in recent if r['outcome']=='purchased'])
            result[name]={'pubkey':key,'item':item,'round':rnd,'contract_scope':scope,'budget_band':budget_band(item,budget),
                          'lifetime_observations':len(all_rows),'legacy_or_other_key_excluded':len(all_rows)-len(same_key),
                          'finalized':len(finalized),'successes':len(wins),'pending':pending,'local_failures_excluded':local_failures,
                          'mean_success_price':sum(r['price'] for r in wins)/len(wins) if wins else None,
                          'mean_cycle_seconds':sum(durations)/len(durations) if durations else None,
                          'historical_verified_median':median(verified_prices) if verified_prices else None,
                          'last_observed_epoch':same_key[-1]['_at'] if same_key else 0,
                          'recent_integrity_fault':any(r['outcome']=='integrity_error' for r in recent),
                          'recent_unavailable':last_outage>last_purchase and now-last_outage<120,
                          'failure_checks':dict(failure_reasons),'retrieval_limit':200}
        for at,name,payload in capabilities:
            data=json.loads(payload)
            if name in result and data.get('pubkey')==identities[name] and data.get('contract_scope',ITEM_CONTRACT)==scope and now-at<300:
                result[name]['acp_available']=data['available']
                result[name]['acp_checked_epoch']=at
        return result

    def seller_policy_summary(self, item, reserve, *, now=None):
        """Fixed-horizon rewards; no unobserved outcome becomes a rejection."""
        from negotiation.ranking import SELLER_ARMS
        now=time.time() if now is None else now
        rnd=self.current_round()
        scope=self.contract_scope()
        with self.connection() as db:
            predictions=db.execute("SELECT payload FROM events WHERE kind='shop_prediction' AND item=? ORDER BY at DESC LIMIT 200",(item,)).fetchall()
            evaluations=db.execute("SELECT payload FROM events WHERE kind='shop_critic' AND item=? ORDER BY at",(item,)).fetchall()
        latest={json.loads(v)['task']:json.loads(v) for (v,) in evaluations}
        missing=[json.loads(v) for (v,) in predictions if json.loads(v)['task'] not in latest]
        for prediction in missing[:20]:
            if now-prediction['began_at']>=600:
                value=self.update_shop_critic(prediction['task'],now=now)
                if value:latest[value['task']]=value
        result=self.seller_summary(item,reserve,now=now)
        with self.connection() as db:
            rows=db.execute("SELECT at,task,payload FROM events WHERE kind='seller_decision' AND item=? AND at>? ORDER BY at",(item,now-21600)).fetchall()
            payments={task:{**json.loads(payload),'_at':at} for at,task,payload in db.execute("SELECT at,task,payload FROM events WHERE kind='seller_settled' AND item=?",(item,))}
            endings={task:{**json.loads(payload),'_at':at} for at,task,payload in db.execute("SELECT at,task,payload FROM events WHERE kind='seller_end' AND item=? ORDER BY at",(item,))}
        tasks=defaultdict(list)
        for at,task,payload in rows:tasks[task].append((at,json.loads(payload)))
        arms={arm:{'arm':arm,'opportunities':0,'matured_opportunities':0,'paid':0,'accepted':0,'unresolved':0,'reward_sum':0,'cycle_seconds_sum':0} for arm in SELLER_ARMS}
        for task,decisions in tasks.items():
            first_at,first=decisions[0];arm=first.get('policy_arm')
            if first.get('turn')!=1 or first.get('round')!=rnd or first.get('policy')!=POLICY or arm not in arms or first.get('contract_scope',ITEM_CONTRACT)!=scope:continue
            row=arms[arm];row['opportunities']+=1
            accepted=next((d for _,d in decisions if d['action']=='accept'),None)
            paid=payments.get(task)
            ending=endings.get(task,{})
            matured=bool(paid) or ending.get('outcome') in {'reject','cancel'} or now-first_at>=600 or (rnd<3 and bool(accepted))
            if ending.get('outcome')=='local_failure':continue
            row['matured_opportunities']+=bool(matured)
            row['accepted']+=bool(accepted);row['paid']+=bool(paid)
            row['unresolved']+=not paid and not accepted and task not in endings
            # R2 uses signed agreements as an explicitly labeled pilot proxy.
            price=paid['price'] if rnd>=3 and paid else accepted['price'] if rnd<3 and accepted else None
            if price is not None:
                row['reward_sum']+=max(0,price-reserve)
            if matured:
                began=first_at-(first.get('elapsed_seconds') or 0)
                elapsed=(paid['_at']-began if paid else ending['_at']-began if ending else
                         accepted.get('elapsed_seconds') if rnd<3 and accepted else 600)
                if not isinstance(elapsed,(int,float)):elapsed=decisions[-1][0]-first_at
                row['cycle_seconds_sum']+=max(1,min(600,elapsed))
        result.update(arm_results=list(arms.values()),policy_version=POLICY,contract_scope=scope,
                      evaluation_window_seconds=21600,maturation_seconds=600,
                      reward_basis='settled margin' if rnd>=3 else 'R2 signed-margin proxy, not R3 payment')
        result['timing_note']='Arm cycle sums are scheduling proxies, not wall-clock uptime; concurrent negotiations overlap.'
        comparable=[v for v in latest.values() if v['round']==rnd and v['contract_scope']==scope]
        errors=[v['brier_error'] for v in comparable if v['brier_error'] is not None]
        result['critic']={'samples':len(comparable),'mean_brier_error':sum(errors)/len(errors) if errors else None,
                          'update_scope':'recalibrate exploration; safety and model weights unchanged'}
        # Sharing projects only observed prices; never buyer budgets or mandates.
        with self.connection() as db:
            observations=db.execute("SELECT task,payload FROM events WHERE kind='buyer_observation' AND item=? ORDER BY at DESC LIMIT 200",(item,)).fetchall()
            feedback={task:json.loads(payload) for task,payload in db.execute("SELECT task,payload FROM events WHERE kind='buyer_feedback' ORDER BY at")}
        prices=[]
        for task,payload in observations:
            row=json.loads(payload);f=feedback.get(task,{})
            if row.get('round')==rnd and row.get('contract_scope',ITEM_CONTRACT)==scope and f.get('verified') and (rnd<3 or f.get('settled')) and type(row.get('price')) in {int,float}:prices.append(row['price'])
        result['shared_observed_prices']={'samples':len(prices),'median':median(prices) if prices else None,
                                          'scope':'own confirmed purchases; no private budgets; not a merchant price guarantee'}
        return result

    def buyer_summary(self, item, budget, *, round_number=None):
        """Lifetime counts and prices; R3 success requires market payment evidence."""
        rnd = self.current_round() if round_number is None else round_number
        with self.connection() as db:
            feedback = {task:json.loads(payload) for task,payload in db.execute(
                "SELECT task,payload FROM events WHERE kind='buyer_feedback' ORDER BY at")}
            rows = db.execute("SELECT at,payload FROM events WHERE kind='buyer_observation' AND item=? ORDER BY at", (item,)).fetchall()
        grouped = defaultdict(list)
        for at, payload in rows:
            row = json.loads(payload)
            row['market'] = feedback.get(row.get('task_id'), {})
            grouped[row['name']].append(row)
        summaries = {}
        for name, history in grouped.items():
            verified = [r for r in history if r['outcome']=='purchased' and r['market'].get('verified') is True]
            settled = [r for r in verified if r['market'].get('settled') is True]
            wins = [r for r in (settled if rnd >= 3 else verified) if r['round']==rnd]
            # A smaller historical budget cannot establish infeasibility now.
            misses = [r for r in history if r['outcome'] in {'no_deal','integrity_error','unavailable'}
                      and isinstance(r.get('budget'),int) and r['budget'] >= budget
                      and (rnd < 3 or r['round'] >= 3)]
            prices = [r['price'] for r in wins if type(r.get('price')) is int]
            history_prices = [r['price'] for r in verified if type(r.get('price')) is int]
            durations = [r['seconds'] for r in wins if isinstance(r.get('seconds'),(int,float))]
            summaries[name] = {'observations':len(history),'verified_purchases':len(verified),
                               'settled_purchases':len(settled),'eligible_successes':len(wins),
                               'comparable_failures':len(misses),'median_price':median(prices) if prices else None,
                               'historical_verified_median':median(history_prices) if history_prices else None,
                               'affordable_successes':sum(p<=budget for p in prices),
                               'mean_seconds':sum(durations)/len(durations) if durations else None,
                               'last_observed_epoch':history[-1]['epoch']}
        return summaries

    def seller_summary(self, item, reserve, *, now=None):
        now = time.time() if now is None else now
        rnd = self.current_round()
        scope=self.contract_scope()
        with self.connection() as db:
            lifetime = db.execute("SELECT COUNT(*) FROM events WHERE kind='seller_decision' AND item=?",(item,)).fetchone()[0]
            rows = db.execute("SELECT at,task,payload FROM events WHERE kind='seller_decision' AND item=? ORDER BY at DESC LIMIT 2000",(item,)).fetchall()
            settled = {task:json.loads(payload) for task,payload in db.execute(
                "SELECT task,payload FROM events WHERE kind='seller_settled' AND item=?",(item,))}
            ended = {task:json.loads(payload)['outcome'] for task,payload in db.execute(
                "SELECT task,payload FROM events WHERE kind='seller_end' AND item=? ORDER BY at",(item,))}
        tasks = defaultdict(list)
        for at,task,payload in rows:
            tasks[task].append((at,json.loads(payload)))
        bands = defaultdict(lambda:Counter(opportunities=0,accepted=0,settled=0,rejected=0,canceled=0,unresolved=0,signed_margin=0,settled_margin=0))
        current_round_count = 0
        for task, decisions in tasks.items():
            decisions.sort(key=lambda x:x[1]['turn'])
            first_at, first = decisions[0]
            if first['turn'] != 1 or first['reserve'] != reserve or first.get('round') != rnd or first.get('contract_scope',ITEM_CONTRACT)!=scope:
                continue
            current_round_count += 1
            band = bands[first['price']]
            band['opportunities'] += 1
            accepted = next((d for _,d in decisions if d['action']=='accept'),None)
            if accepted:
                band['accepted'] += 1
                band['signed_margin'] += max(0,accepted['price']-reserve)
            if task in settled:
                band['settled'] += 1
                band['settled_margin'] += max(0,settled[task]['price']-reserve)
            if ended.get(task) in {'reject','cancel'}:
                band['rejected' if ended[task]=='reject' else 'canceled'] += 1
            elif not accepted:
                # Silence is censored evidence, never a fabricated rejection.
                band['unresolved'] += 1
        return {'market_round':rnd,'lifetime_decisions':lifetime,'current_round_opportunities':current_round_count,
                'retrieval_limit_decisions':2000,
                'opening_price_results':[{'opening_price':p,**dict(v)} for p,v in sorted(bands.items())][-12:],
                'note':'Accepted is a signed agreement; settled alone proves R3 payment. Unresolved is not rejection.'}

    def status(self):
        with self.connection() as db:
            return dict(db.execute('SELECT kind,COUNT(*) FROM events GROUP BY kind'))
