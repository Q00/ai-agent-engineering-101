"""Record successful ACP lines from Market Kit v4, without changing market state."""
import argparse, json, re, sqlite3
from pathlib import Path


PAID = re.compile(r'\[acp\] paid (\d+) through (.+?), settled at ledger #(\d+)(?::.*)?$')
SOLD = re.compile(r'\[acp\] settled (.+) at (\d+), ledger #(\d+)(?:, unit (\S+) grade (\S+))?$')

def parse(line):
    m = PAID.search(line)
    if m: return ('buyer', int(m[3]), int(m[1]), None, None, None)
    m = SOLD.search(line)
    if m: return ('seller', int(m[3]), int(m[2]), m[1], m[4], m[5])
    return None

def connect(path):
    db = sqlite3.connect(path, timeout=10)
    db.execute('''CREATE TABLE IF NOT EXISTS observations (market_run TEXT NOT NULL, role TEXT NOT NULL, ledger INTEGER NOT NULL, price INTEGER NOT NULL, item TEXT, unit TEXT, grade TEXT, source TEXT NOT NULL, line INTEGER NOT NULL, round INTEGER, PRIMARY KEY(market_run,role,ledger))''')
    db.commit()
    return db

def ingest(db, path, run):
    if not run.strip(): raise ValueError('market-run must not be empty')
    result = {'added':0,'duplicates':0,'conflicts':0}
    with Path(path).open(encoding='utf-8') as f, db:
        for num, line in enumerate(f, 1):
            p = parse(line.rstrip('\r\n'))
            if not p: continue
            role, ledger, price, item, unit, grade = p
            old = db.execute('SELECT price,item,unit,grade FROM observations WHERE market_run=? AND role=? AND ledger=?',(run,role,ledger)).fetchone()
            if old is not None:
                result['duplicates' if old == (price,item,unit,grade) else 'conflicts'] += 1
            else:
                db.execute('INSERT INTO observations VALUES (?,?,?,?,?,?,?,?,?,NULL)',(run,role,ledger,price,item,unit,grade,str(path),num))
                result['added'] += 1
    return result

def main():
    p=argparse.ArgumentParser(); p.add_argument('--db',default='settlements.sqlite3'); subs=p.add_subparsers(dest='cmd',required=True)
    x=subs.add_parser('import-log'); x.add_argument('--log',required=True); x.add_argument('--market-run',required=True)
    x=subs.add_parser('report'); x.add_argument('--market-run',required=True)
    a=p.parse_args()
    with connect(a.db) as db:
        if a.cmd=='import-log': print(json.dumps(ingest(db,a.log,a.market_run)))
        else:
            rows=db.execute('SELECT role,COUNT(*),SUM(price) FROM observations WHERE market_run=? GROUP BY role',(a.market_run,)).fetchall()
            print(json.dumps({'market_run':a.market_run,'observed':[{'role':r,'count':n,'total_price':p} for r,n,p in rows],'note':'Observed ACP log lines only; round, profit and ledger reconciliation unverified.'},ensure_ascii=False,indent=2))


if __name__=='__main__': main()
