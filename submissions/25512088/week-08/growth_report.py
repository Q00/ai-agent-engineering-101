"""Read-only evaluation. No synthetic purchases and no private keys in output."""
import argparse
import json
from pathlib import Path
import sqlite3
import time


def report(state):
    db=sqlite3.connect(f'file:{Path(state).resolve()}/referrals.sqlite3?mode=ro',uri=True)
    db.row_factory=sqlite3.Row
    try:
        row=db.execute("SELECT value FROM meta WHERE key='growth_started_at'").fetchone()
        if not row:return {'status':'not_started','message':'Restart seller; measurement begins with its first request.'}
        start=json.loads(row[0])
        events={r[0]:r[1] for r in db.execute('SELECT event,count(*) FROM growth_events WHERE ts>=? GROUP BY event',(start,))}
        sales=db.execute('SELECT * FROM sales WHERE settled>=?',(start,)).fetchall()
        referrals=[s for s in sales if s['referrer'] and s['reward']>0 and not s['refunded']]
        repeats=db.execute("""SELECT count(DISTINCT repeat.task) FROM sales repeat
          JOIN sales referred ON referred.referrer=repeat.member
          WHERE repeat.settled>=? AND repeat.spent>0 AND repeat.refunded=0
          AND referred.settled>=? AND referred.awarded=1 AND referred.reward>0 AND referred.refunded=0
          AND repeat.settled>=referred.settled+21600""",(start,start)).fetchone()[0]
        contribution=sum((0 if s['refunded'] else s['amount'])-s['cost']-
                         (0 if s['refunded'] else s['reward']) for s in sales)
        return dict(status='observing',started_at=start,hours=round((time.time()-start)/3600,2),
          events=events,settled_sales=len(sales),referral_settlements=len(referrals),
          referral_rewards_paid=sum(s['awarded']==1 for s in referrals),
          credited_referrers_repurchase=repeats,refunds=sum(s['refunded']==1 for s in sales),
          contribution_after_reserved_rewards=contribution,
          note='Contribution excludes storage, unsold inventory and other fees; not official profit or causal uplift. Recheck after two supply windows and after 6h. No inquiries: distribution problem; inquiries without settlement: product/price/conditions problem; paid rewards without repeat: redemption problem.')
    finally:db.close()


if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--state',default='.market')
    print(json.dumps(report(ap.parse_args().state),ensure_ascii=False,indent=2))
