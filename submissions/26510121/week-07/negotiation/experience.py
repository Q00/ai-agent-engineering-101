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

    def buyer_observation(self, record, *, round_number=2, policy=POLICY):
        allowed = ('name','item','budget','outcome','price','seconds','turns','task_id',
                   'sequence','signature_verified','epoch','pubkey','order_id','error_code','policy_arm')
        data = {k: record.get(k) for k in allowed}
        if type(data.get('price')) is float and data['price'].is_integer():
            data['price'] = int(data['price'])
        data.update(round=round_number, policy=policy)
        identity = str(data.get('order_id') or data.get('task_id') or f"{data['name']}:{data['epoch']}:{data['sequence']}")
        return self.put('buyer_observation', identity, data, item=data['item'],
                        task=data['task_id'], counterparty=data['name'], at=data['epoch'])

    def buyer_feedback(self, *, task, order, verified, settled, round_number, ledger=None,
                       total_seconds=None, processing_seconds=None, wait_seconds=None, failed_checks=None):
        if not task and not order:
            return False
        return self.put('buyer_feedback', str(order),
                        {'verified': verified is True, 'settled': settled is True,
                         'round': round_number, 'ledger': ledger,'order':order,
                         'total_seconds':total_seconds,'processing_seconds':processing_seconds,
                         'wait_seconds':wait_seconds,'failed_checks':failed_checks or []}, task=task)

    def seller_decision(self, *, task, item, reserve, turn, action, price, buyer_offer,
                        round_number=None, policy=POLICY, at=None, policy_arm=None, elapsed_seconds=None):
        return self.put('seller_decision', f'{task}:{turn}',
                        {'reserve':reserve,'turn':turn,'action':action,'price':price,
                         'buyer_offer':buyer_offer,'round':round_number if round_number is not None else self.current_round(),
                         'policy':policy,'policy_arm':policy_arm,'elapsed_seconds':elapsed_seconds}, item=item, task=task, at=at)

    def seller_end(self, task, item, outcome, *, at=None):
        return self.put('seller_end', f'{task}:{outcome}', {'outcome':outcome}, item=item,task=task,at=at)

    def seller_settlement(self, *, task, item, price, order, ledger):
        return self.put('seller_settled', str(order), {'price':price,'ledger':ledger,'round':3}, item=item,task=task)

    def capability(self, name, pubkey, available, *, at=None):
        stamp=time.time() if at is None else at
        return self.put('shop_capability',f'{name}:{pubkey}:{stamp}',
                        {'pubkey':pubkey,'available':available is True},counterparty=name,at=stamp)

    def buyer_evidence(self, item, budget, identities, *, round_number=None, now=None):
        """Comparable identity/Round/budget evidence, including failed order cycles."""
        from negotiation.ranking import budget_band
        now=time.time() if now is None else now
        rnd=self.current_round() if round_number is None else round_number
        with self.connection() as db:
            final_rows=db.execute("SELECT task,payload FROM events WHERE kind='buyer_feedback' ORDER BY at").fetchall()
            observations=db.execute("SELECT at,payload FROM events WHERE kind='buyer_observation' AND item=? ORDER BY at",(item,)).fetchall()
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
            same_key=[r for r in all_rows if r.get('pubkey')==key and key]
            comparable=[r for r in same_key if r.get('round')==rnd and budget_band(item,r['budget'])==budget_band(item,budget)]
            finalized=[];wins=[];durations=[];failure_reasons=Counter()
            for row in comparable[-200:]:
                feedback=by_order.get(row.get('order_id')) or by_task.get(row.get('task_id'))
                if feedback is None or row['outcome']=='local_failure':continue
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
            recent=[r for r in same_key if now-r['_at']<1800]
            last_outage=max([0]+[r['_at'] for r in recent if r['outcome']=='unavailable'])
            last_purchase=max([0]+[r['_at'] for r in recent if r['outcome']=='purchased'])
            result[name]={'pubkey':key,'item':item,'round':rnd,'budget_band':budget_band(item,budget),
                          'lifetime_observations':len(all_rows),'legacy_or_other_key_excluded':len(all_rows)-len(same_key),
                          'finalized':len(finalized),'successes':len(wins),'pending':len(comparable[-200:])-len(finalized),
                          'mean_success_price':sum(r['price'] for r in wins)/len(wins) if wins else None,
                          'mean_cycle_seconds':sum(durations)/len(durations) if durations else None,
                          'historical_verified_median':median(verified_prices) if verified_prices else None,
                          'last_observed_epoch':same_key[-1]['_at'] if same_key else 0,
                          'recent_integrity_fault':any(r['outcome']=='integrity_error' for r in recent),
                          'recent_unavailable':last_outage>last_purchase and now-last_outage<120,
                          'failure_checks':dict(failure_reasons),'retrieval_limit':200}
        for at,name,payload in capabilities:
            data=json.loads(payload)
            if name in result and data.get('pubkey')==identities[name] and now-at<300:
                result[name]['acp_available']=data['available']
                result[name]['acp_checked_epoch']=at
        return result

    def seller_policy_summary(self, item, reserve, *, now=None):
        """Fixed-horizon rewards; no unobserved outcome becomes a rejection."""
        from negotiation.ranking import SELLER_ARMS
        now=time.time() if now is None else now
        rnd=self.current_round()
        result=self.seller_summary(item,reserve,now=now)
        with self.connection() as db:
            rows=db.execute("SELECT at,task,payload FROM events WHERE kind='seller_decision' AND item=? AND at>? ORDER BY at",(item,now-21600)).fetchall()
            payments={task:json.loads(payload) for task,payload in db.execute("SELECT task,payload FROM events WHERE kind='seller_settled' AND item=?",(item,))}
            endings={task:json.loads(payload)['outcome'] for task,payload in db.execute("SELECT task,payload FROM events WHERE kind='seller_end' AND item=? ORDER BY at",(item,))}
        tasks=defaultdict(list)
        for at,task,payload in rows:tasks[task].append((at,json.loads(payload)))
        arms={arm:{'arm':arm,'opportunities':0,'matured_opportunities':0,'paid':0,'accepted':0,'unresolved':0,'reward_sum':0} for arm in SELLER_ARMS}
        for task,decisions in tasks.items():
            first_at,first=decisions[0];arm=first.get('policy_arm')
            if first.get('turn')!=1 or first.get('round')!=rnd or first.get('policy')!=POLICY or arm not in arms:continue
            row=arms[arm];row['opportunities']+=1
            accepted=next((d for _,d in decisions if d['action']=='accept'),None)
            paid=payments.get(task)
            matured=paid or endings.get(task) in {'reject','cancel'} or now-first_at>=600
            if endings.get(task)=='local_failure':continue
            row['matured_opportunities']+=bool(matured)
            row['accepted']+=bool(accepted);row['paid']+=bool(paid)
            row['unresolved']+=not paid and not accepted and task not in endings
            # R2 uses signed agreements as an explicitly labeled pilot proxy.
            price=paid['price'] if rnd>=3 and paid else accepted['price'] if rnd<3 and accepted else None
            if price is not None:
                row['reward_sum']+=max(0,price-reserve)
                if not matured:row['matured_opportunities']+=1
        result.update(arm_results=list(arms.values()),policy_version=POLICY,
                      evaluation_window_seconds=21600,maturation_seconds=600,
                      reward_basis='settled margin' if rnd>=3 else 'R2 signed-margin proxy, not R3 payment')
        # Sharing projects only observed prices; never buyer budgets or mandates.
        with self.connection() as db:
            observations=db.execute("SELECT task,payload FROM events WHERE kind='buyer_observation' AND item=? ORDER BY at DESC LIMIT 200",(item,)).fetchall()
            feedback={task:json.loads(payload) for task,payload in db.execute("SELECT task,payload FROM events WHERE kind='buyer_feedback' ORDER BY at")}
        prices=[]
        for task,payload in observations:
            row=json.loads(payload);f=feedback.get(task,{})
            if row.get('round')==rnd and f.get('verified') and (rnd<3 or f.get('settled')) and type(row.get('price')) in {int,float}:prices.append(row['price'])
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
            if first['turn'] != 1 or first['reserve'] != reserve or first.get('round') != rnd:
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
