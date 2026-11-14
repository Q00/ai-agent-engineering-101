"""Shop-owned sale ledger; legacy member promises are honored but enrollment is closed.

Membership credentials are bearer secrets, NOT authenticated market identities.
Never put them in receipt artifacts, forum posts, or logs. Discounts use one frozen
public quote; founder discounts do not silently shrink to protect a margin.
"""
import hashlib
import json
import secrets
import sqlite3
import time
from datetime import datetime
from pathlib import Path

RESERVATION_LEASE_SECONDS = 30


def policy():
    return json.loads(Path(__file__).with_name('referral_policy.json').read_text())


class Program:
    def __init__(self, state, config=None):
        self.p = config or policy()
        Path(state).mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(Path(state) / 'referrals.sqlite3', timeout=15)
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS members(
          id TEXT PRIMARY KEY, secret_hash TEXT UNIQUE, code TEXT UNIQUE,
          buyer TEXT, active INTEGER DEFAULT 0, founder INTEGER DEFAULT 0,
          credit INTEGER DEFAULT 0);
        CREATE TABLE IF NOT EXISTS sales(
          task TEXT PRIMARY KEY, unit TEXT UNIQUE, sku TEXT, amount INTEGER,
          cost INTEGER, member TEXT, referrer TEXT, reward INTEGER,
          spent INTEGER, ledger INTEGER UNIQUE, buyer TEXT, settled REAL,
          refunded INTEGER DEFAULT 0, reviewed INTEGER DEFAULT 0,
          posted INTEGER DEFAULT 0, awarded INTEGER DEFAULT 0);
        CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE IF NOT EXISTS growth_events(
          task TEXT, event TEXT, ts REAL, PRIMARY KEY(task,event));
        CREATE TABLE IF NOT EXISTS failed_sales(
          task TEXT PRIMARY KEY, unit TEXT, sku TEXT, amount INTEGER, cost INTEGER,
          result_seq INTEGER UNIQUE, result_ts REAL, reason TEXT);
        CREATE TABLE IF NOT EXISTS expired_sales(
          task TEXT PRIMARY KEY, unit TEXT, sku TEXT, amount INTEGER, cost INTEGER,
          member TEXT, referrer TEXT, reward INTEGER, spent INTEGER,
          expired REAL, reason TEXT, late_ledger INTEGER UNIQUE,
          late_buyer TEXT, late_settled REAL);
        CREATE TABLE IF NOT EXISTS warranties(task TEXT PRIMARY KEY, amount INTEGER, issued REAL);
        ''')
        frozen = self.get('policy_snapshot')
        if frozen is not None and frozen != self.p:
            self.db.close()
            raise ValueError('Campaign policy is frozen. Honor existing promises; use a new campaign/state for new terms.')
        if frozen is None:
            self.put('policy_snapshot', self.p)

    def close(self):
        self.db.close()

    def growth_event(self, task, event):
        with self.db:
            self.db.execute('INSERT OR IGNORE INTO growth_events VALUES(?,?,?)', (task,event,time.time()))

    def purchase_code(self, task):
        row=self.db.execute('SELECT m.code FROM members m JOIN sales s ON s.member=m.id WHERE s.task=?',(task,)).fetchone()
        return row[0] if row else None

    def live(self, now=None):
        return (time.time() if now is None else now) < datetime.fromisoformat(self.p['ends_at']).timestamp()

    def get(self, key, default=None):
        r = self.db.execute('SELECT value FROM meta WHERE key=?', (key,)).fetchone()
        return json.loads(r[0]) if r else default

    def put(self, key, value):
        self.db.execute('INSERT OR REPLACE INTO meta VALUES(?,?)', (key, json.dumps(value)))
        self.db.commit()

    def member(self, token):
        h = hashlib.sha256(token.encode()).hexdigest()
        return self.db.execute('SELECT * FROM members WHERE secret_hash=? AND active=1', (h,)).fetchone()

    def quote(self, base, cost, token='', ref='', now=None):
        """Quote only; monetary reservations happen atomically in accept()."""
        if type(base) is not int or type(cost) is not int or base < 1 or cost < 0:
            raise ValueError('Invalid price or cost')
        m = self.member(token)
        parent = self.db.execute('SELECT * FROM members WHERE code=? AND active=1', (ref,)).fetchone()
        result = dict(base=base, price=base, member=m['id'] if m else None,
                      referrer=None, reward=0, spent=0, benefit='none')
        if not self.live(now):
            return result
        # Existing customers never generate a new-customer reward. A public code
        # is not a membership credential and cannot unlock the founder price.
        margin = max(0, base - cost)
        budget = margin * self.p['acquisition_margin_percent'] // 100
        if m and m['founder']:
            result.update(price=max(1, base - base * self.p['founder_percent'] // 100), benefit='founder')
        elif m:
            held = self.db.execute('SELECT COALESCE(SUM(spent),0) FROM sales WHERE member=? AND ledger IS NULL',
                                   (m['id'],)).fetchone()[0]
            use = min(max(0, m['credit'] - held), budget, self.p['reward_cap'])
            result.update(price=base-use, spent=use, benefit='credit' if use else 'none')
        elif parent:
            discount = min(base * self.p['friend_percent'] // 100, self.p['reward_cap'], budget)
            reward = min(base * self.p['referrer_percent'] // 100, self.p['reward_cap'], budget-discount)
            result.update(price=base-discount, referrer=parent['id'], reward=reward, benefit='referral')
        # Founder members may prefer a larger earned credit, but benefits never stack.
        if m and m['founder']:
            held = self.db.execute('SELECT COALESCE(SUM(spent),0) FROM sales WHERE member=? AND ledger IS NULL',
                                   (m['id'],)).fetchone()[0]
            use = min(max(0, m['credit'] - held), budget, self.p['reward_cap'])
            if use > base-result['price']:
                result.update(price=base-use, spent=use, benefit='credit')
        return result

    def accept(self, task, unit, sku, cost, q, inventory=None, enroll=True):
        """Reserve stock/credit for a short checkout lease before signing.

        The signed receipt remains auditable after the lease, but an abandoned
        checkout must not let an unauthenticated caller hide stock forever.
        """
        secret = None
        now = time.time()
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            self._expire_stale_locked(now)
            self.db.execute('CREATE TABLE IF NOT EXISTS warranties(task TEXT PRIMARY KEY, amount INTEGER, issued REAL)')
            if q.get('warranty_hours'):
                if inventory is None:raise ValueError('Missing warranty cash evidence')
                amounts=[row[0] for row in self.db.execute(
                    'SELECT w.amount FROM warranties w JOIN sales s ON s.task=w.task '
                    'WHERE s.refunded=0 AND (s.settled IS NULL OR s.settled>?)',
                    (time.time()-21600,))]
                from seller_procurement import reserve_cash,warranty_risk_reserve
                reserve=reserve_cash(inventory)
                warranty_reserve=warranty_risk_reserve(amounts+[q['price']])
                other_liabilities=self.db.execute(
                    'SELECT COALESCE(SUM(credit),0) FROM members').fetchone()[0]
                other_liabilities+=self.db.execute(
                    'SELECT COALESCE(SUM(reward),0) FROM sales '
                    'WHERE awarded=0 AND refunded=0').fetchone()[0]
                if inventory.get('balance',0)-warranty_reserve-other_liabilities<reserve:
                    raise ValueError('Warranty reserve unavailable')
                self.db.execute('INSERT INTO warranties VALUES(?,?,?)',(task,q['price'],time.time()))
            mid = q['member']
            if mid and q['spent']:
                credit = self.db.execute('SELECT credit FROM members WHERE id=?', (mid,)).fetchone()[0]
                held = self.db.execute('SELECT COALESCE(SUM(spent),0) FROM sales WHERE member=? AND ledger IS NULL',
                                       (mid,)).fetchone()[0]
                if credit-held < q['spent']:
                    raise ValueError('Credit is already reserved; request a fresh quote')
            if not mid and enroll:
                mid, secret = secrets.token_hex(12), secrets.token_urlsafe(32)
                self.db.execute('INSERT INTO members(id,secret_hash,code) VALUES(?,?,?)',
                                (mid, hashlib.sha256(secret.encode()).hexdigest(), 'REF-'+secrets.token_hex(6).upper()))
            self.db.execute('''INSERT INTO sales(task,unit,sku,amount,cost,member,referrer,reward,spent)
              VALUES(?,?,?,?,?,?,?,?,?)''',
              (task, unit, sku, q['price'], cost, mid, q['referrer'], q['reward'], q['spent']))
            self.db.execute('INSERT OR IGNORE INTO growth_events VALUES(?,?,?)',(task,'signed',now))
        return secret

    def _archive_sale_locked(self, sale, now, reason):
        self.db.execute('''INSERT OR IGNORE INTO expired_sales
          (task,unit,sku,amount,cost,member,referrer,reward,spent,expired,reason)
          VALUES(?,?,?,?,?,?,?,?,?,?,?)''',(sale['task'],sale['unit'],sale['sku'],sale['amount'],
          sale['cost'],sale['member'],sale['referrer'],sale['reward'],sale['spent'],now,reason))
        self.db.execute('DELETE FROM sales WHERE task=? AND ledger IS NULL',(sale['task'],))

    def _expire_stale_locked(self, now):
        stale=self.db.execute('''SELECT s.* FROM sales s LEFT JOIN growth_events g
          ON g.task=s.task AND g.event='signed' WHERE s.ledger IS NULL
          AND (g.ts IS NULL OR g.ts<=?)''',(now-RESERVATION_LEASE_SECONDS,)).fetchall()
        for sale in stale:self._archive_sale_locked(sale,now,'checkout_lease_expired')
        return len(stale)

    def expire_stale(self, now=None):
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            return self._expire_stale_locked(time.time() if now is None else now)

    def reserved_units(self, now=None):
        # Signed receipts exclusively reserve a unit only during checkout lease.
        self.expire_stale(now)
        return {r[0] for r in self.db.execute('SELECT unit FROM sales WHERE ledger IS NULL')}

    def release_failed(self, row, shop_urls):
        """Release only a receipt the official ledger finalized as not settled."""
        d=row.get('data') or {};checks=d.get('checks') or {};receipt=d.get('receipt') or {}
        if (row.get('kind')!='result' or row.get('round')!=3 or d.get('shop') not in shop_urls
                or d.get('verified') is not False or checks.get('settled') is not False
                or not isinstance(receipt,dict)):
            return False
        task=receipt.get('task');unit=receipt.get('unit')
        if not task or not unit:return False
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            sale=self.db.execute('SELECT * FROM sales WHERE task=? AND ledger IS NULL',(task,)).fetchone()
            expired=False
            if sale is None:
                sale=self.db.execute('SELECT * FROM expired_sales WHERE task=?',(task,)).fetchone()
                expired=sale is not None
            if (not sale or sale['unit']!=unit or sale['sku']!=receipt.get('item')
                    or sale['amount']!=receipt.get('price')):
                return False
            self.db.execute('''INSERT OR IGNORE INTO failed_sales
                (task,unit,sku,amount,cost,result_seq,result_ts,reason)
                VALUES(?,?,?,?,?,?,?,?)''',(sale['task'],sale['unit'],sale['sku'],sale['amount'],sale['cost'],
                    row.get('seq'),row.get('ts'),'official_result_not_settled'))
            self.db.execute('DELETE FROM warranties WHERE task=?',(task,))
            self.db.execute('DELETE FROM expired_sales WHERE task=?',(task,)) if expired else self.db.execute(
                'DELETE FROM sales WHERE task=? AND ledger IS NULL',(task,))
        return True

    def settle(self, row, shop_urls):
        d = row.get('data', {})
        if row.get('kind') != 'settle' or row.get('round') != 3 or d.get('shop') not in shop_urls:
            return False
        if not isinstance(d.get('buyer'), str) or not d['buyer']:
            return False
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            s = self.db.execute('''SELECT * FROM sales WHERE unit=? AND ledger IS NULL
                AND amount=? AND sku=?''',(d.get('unit'),d.get('amount'),d.get('item'))).fetchone()
            if s is None:
                old=self.db.execute('''SELECT * FROM expired_sales WHERE unit=? AND amount=? AND sku=?
                    ORDER BY expired DESC LIMIT 1''',(d.get('unit'),d.get('amount'),d.get('item'))).fetchone()
                if old is None:return False
                conflict=self.db.execute('SELECT * FROM sales WHERE unit=? AND ledger IS NULL',(d.get('unit'),)).fetchone()
                if conflict:self._archive_sale_locked(conflict,row['ts'],'unit_settled_by_prior_receipt')
                self.db.execute('''INSERT INTO sales(task,unit,sku,amount,cost,member,referrer,reward,spent)
                    VALUES(?,?,?,?,?,?,?,?,?)''',(old['task'],old['unit'],old['sku'],old['amount'],old['cost'],
                    old['member'],old['referrer'],old['reward'],old['spent']))
                self.db.execute('DELETE FROM expired_sales WHERE task=?',(old['task'],))
                s=self.db.execute('SELECT * FROM sales WHERE task=?',(old['task'],)).fetchone()
            existing = self.db.execute('SELECT id FROM members WHERE buyer=? AND active=1', (d['buyer'],)).fetchone()
            member = self.db.execute('SELECT * FROM members WHERE id=?', (s['member'],)).fetchone() if s['member'] else None
            # A transferable bearer token cannot prove an account identity. A token
            # used by another account loses eligibility for further member rewards.
            mismatch = bool(member and member['buyer'] and member['buyer'] != d['buyer'])
            duplicate = bool(member and existing is not None and existing['id'] != s['member'])
            parent = self.db.execute('SELECT buyer FROM members WHERE id=?', (s['referrer'],)).fetchone()
            invalid = existing is not None or mismatch or (parent and parent['buyer'] == d['buyer'])
            self.db.execute('UPDATE sales SET ledger=?,buyer=?,settled=?,reward=? WHERE task=?',
                            (row['seq'], d['buyer'], row['ts'], 0 if invalid else s['reward'], s['task']))
            if member:
                self.db.execute('UPDATE members SET active=?,buyer=COALESCE(buyer,?),credit=credit-? WHERE id=?',
                                (0 if mismatch or duplicate else 1, d['buyer'], s['spent'], s['member']))
        return True

    def review(self, row, shop_urls):
        d = row.get('data', {})
        if row.get('kind') != 'result' or not d.get('verified') or d.get('checks', {}).get('settled') is not True:
            return
        if d.get('shop') not in shop_urls or not str(d.get('review', '')).strip():
            return
        rec = d.get('receipt') or {}
        with self.db:
            self.db.execute('UPDATE sales SET reviewed=1 WHERE task=? AND buyer=? AND ledger IS NOT NULL',
                            (rec.get('task'), d.get('buyer')))

    def forum(self, posts):
        with self.db:
            for post in posts:
                if post.get('role') != 'buyer' or not str(post.get('text', '')).strip():
                    continue
                for ref in post.get('refs', []):
                    # Live API enriches outgoing integer refs into objects.
                    seq = ref.get('seq') if isinstance(ref, dict) and ref.get('found') is True and ref.get('kind') == 'settle' else ref
                    if type(seq) is int:
                        self.db.execute('UPDATE sales SET posted=1 WHERE ledger=? AND buyer=?', (seq, post.get('author')))

    def refund_unit(self, unit):
        """Adapter only: exact unit attribution required; never parse names heuristically."""
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            s = self.db.execute('SELECT * FROM sales WHERE unit=?', (unit,)).fetchone()
            if not s or s['refunded']:
                return
            if s['awarded'] and s['referrer']:
                self.db.execute('UPDATE members SET credit=credit-? WHERE id=?', (s['reward'], s['referrer']))
            self.db.execute('UPDATE sales SET refunded=1 WHERE unit=?', (unit,))

    def reconcile(self, trusted_through, now=None):
        """Do not mature rewards beyond the successfully synchronized ledger watermark."""
        now = time.time() if now is None else now
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            for s in self.db.execute('SELECT * FROM sales WHERE ledger IS NOT NULL AND refunded=0 AND awarded=0').fetchall():
                if s['settled'] + self.p['reward_hold_seconds'] > min(now, trusted_through):
                    continue
                if s['referrer'] and s['reward']:
                    self.db.execute('UPDATE members SET credit=credit+? WHERE id=?', (s['reward'], s['referrer']))
                self.db.execute('UPDATE sales SET awarded=1 WHERE task=?', (s['task'],))
            if self.live(now):
                left = self.p['founder_limit'] - self.db.execute('SELECT COUNT(*) FROM members WHERE founder=1').fetchone()[0]
                eligible = self.db.execute('''SELECT DISTINCT m.id FROM members m JOIN sales s ON s.member=m.id
                  WHERE m.active=1 AND m.founder=0 AND s.reviewed=1 AND s.posted=1
                  AND s.refunded=0 AND s.awarded=1 ORDER BY s.ledger''').fetchall()
                for m in eligible[:max(0, left)]:
                    self.db.execute('UPDATE members SET founder=1 WHERE id=?', (m['id'],))

    def status(self, token):
        m = self.member(token)
        if not m:
            return {'active': False, 'message': '결제 확인 대기 또는 유효하지 않은 회원 키'}
        return dict(active=True, referral_code=m['code'], founder=bool(m['founder']),
                    credit=max(0, m['credit']), ends_at=self.p['ends_at'],
                    founder_percent=self.p['founder_percent'])
