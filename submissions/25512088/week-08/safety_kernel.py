"""Non-adaptive safety limits for capital, bankruptcy and recovery."""
from __future__ import annotations

import json
import time

from adaptive_control import Control


class SafetyKernel:
    def __init__(self,state='.market',control=None):
        self.control=control or Control(state);self.owns_control=control is None
        self.control.db.executescript('''
        CREATE TABLE IF NOT EXISTS bankruptcy_events(
          event_id TEXT PRIMARY KEY, ts REAL, source TEXT, reference_hash TEXT);
        CREATE TABLE IF NOT EXISTS financial_snapshots(
          id INTEGER PRIMARY KEY, ts REAL, cash REAL, liabilities REAL,
          inventory_cost REAL, inventory_slots INTEGER, body TEXT);
        ''')

    def _get(self,key,default):
        row=self.control.db.execute('SELECT value FROM safety_state WHERE key=?',(key,)).fetchone()
        return json.loads(row[0]) if row else default

    def _put(self,key,value):
        with self.control.db:
            self.control.db.execute('INSERT OR REPLACE INTO safety_state VALUES(?,?,?)',
                (key,json.dumps(value,sort_keys=True),time.time()))

    def record_bankruptcy(self,event_id,source,reference_hash):
        if source not in {'official_bankruptcy','forced_reset'}:return False
        with self.control.db:
            cursor=self.control.db.execute('INSERT OR IGNORE INTO bankruptcy_events VALUES(?,?,?,?)',
                (event_id,time.time(),source,reference_hash))
        count=self.control.db.execute('SELECT COUNT(*) FROM bankruptcy_events').fetchone()[0]
        if count>=1:self._put('risk_exposure_allowed',False)
        self.control.event('safety','bankruptcy_recorded',{'event_id':event_id,'source':source,'count':count})
        return cursor.rowcount==1

    def recovery_check(self,checkpoint_ok,hash_ok,sqlite_ok,commit_ok,safety_ok):
        passed=all((checkpoint_ok,hash_ok,sqlite_ok,commit_ok,safety_ok))
        failures=0 if passed else self._get('consecutive_recovery_failures',0)+1
        self._put('consecutive_recovery_failures',failures)
        if failures>=3:self._put('mutation_gate','PRESERVE_ONLY')
        elif passed:self._put('mutation_gate','OPEN')
        self.control.event('safety','recovery_check',{
            'passed':passed,'consecutive_failures':failures,
            'mutation_gate':self._get('mutation_gate','OPEN')})
        return passed

    def snapshot(self,cash,liabilities,inventory_cost,inventory_slots,**body):
        values=(float(cash),float(liabilities),float(inventory_cost),int(inventory_slots))
        if any(v<0 for v in values):raise ValueError('negative financial value')
        with self.control.db:
            self.control.db.execute('INSERT INTO financial_snapshots VALUES(?,?,?,?,?,?,?)',
                (None,time.time(),*values,json.dumps(body,sort_keys=True)))

    def allow(self,*,cash,liabilities,incremental_cost,inventory_slots,slot_limit=250,
              defend=False,parameter_change=0.0):
        reasons=[]
        if self._get('mutation_gate','OPEN')!='OPEN':reasons.append('preserve_only')
        if not self._get('risk_exposure_allowed',True) and incremental_cost>0:reasons.append('bankruptcy_limit')
        if inventory_slots>slot_limit:reasons.append('inventory_slots')
        if cash-incremental_cost<liabilities:reasons.append('protected_cash')
        if defend and abs(parameter_change)>.10:reasons.append('defend_change')
        self.snapshot(cash,liabilities,max(0,incremental_cost),inventory_slots,
            allowed=not reasons,reasons=reasons)
        return not reasons,reasons

    def close(self):
        if self.owns_control:self.control.close()
