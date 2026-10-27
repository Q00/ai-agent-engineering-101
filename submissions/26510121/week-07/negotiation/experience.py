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

POLICY = 'memory-v1'
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
                   'sequence','signature_verified','epoch')
        data = {k: record.get(k) for k in allowed}
        data.update(round=round_number, policy=policy)
        identity = str(data.get('task_id') or f"{data['name']}:{data['epoch']}:{data['sequence']}")
        return self.put('buyer_observation', identity, data, item=data['item'],
                        task=data['task_id'], counterparty=data['name'], at=data['epoch'])

    def buyer_feedback(self, *, task, order, verified, settled, round_number, ledger=None):
        if not task:
            return False
        return self.put('buyer_feedback', str(order),
                        {'verified': verified is True, 'settled': settled is True,
                         'round': round_number, 'ledger': ledger}, task=task)

    def seller_decision(self, *, task, item, reserve, turn, action, price, buyer_offer,
                        round_number=None, policy=POLICY, at=None):
        return self.put('seller_decision', f'{task}:{turn}',
                        {'reserve':reserve,'turn':turn,'action':action,'price':price,
                         'buyer_offer':buyer_offer,'round':round_number if round_number is not None else self.current_round(),
                         'policy':policy}, item=item, task=task, at=at)

    def seller_end(self, task, item, outcome, *, at=None):
        return self.put('seller_end', f'{task}:{outcome}', {'outcome':outcome}, item=item,task=task,at=at)

    def seller_settlement(self, *, task, item, price, order, ledger):
        return self.put('seller_settled', str(order), {'price':price,'ledger':ledger,'round':3}, item=item,task=task)

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
            wins = settled if rnd >= 3 else verified
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
