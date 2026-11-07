"""Explicit one-off goods purchase authorization. Durable spend; never retry unknown POSTs."""
import argparse,fcntl,importlib.util,json,math,os,sqlite3,time
from pathlib import Path
from network_reads import retry_read
from strategic_market import snapshot
from market_intel import read_json

ROOT=Path(__file__).resolve().parent

def settings():return json.loads((ROOT/'procurement_policy.json').read_text())

def reserve_cash(inv,exposure=0):
    return max(settings()['cash_floor'],inv.get('bankrupt_line',500000))+exposure

def capital_available(policy,inv,exposure=0,pending=0):
    """Scale deployable capital with wealth while preserving cash and liabilities."""
    inventory_cost=sum(u.get('cost',0) for u in inv.get('units',[]))
    capital=max(0,inv.get('balance',0))+inventory_cost
    inventory_ceiling=math.floor(capital*policy.get('max_inventory_share',.7))
    liquid=max(0,inv.get('balance',0)-reserve_cash(inv,exposure)-pending)
    capacity=max(0,inventory_ceiling-inventory_cost-pending)
    return max(0,min(liquid,capacity))

def choose(policy,catalog,spent,reserved,balance,exposure,turn,blocked=()):
    # spent is current inventory principal, not lifetime procurement spend.
    remaining=min(policy['budget']-spent-reserved,balance-policy['cash_floor']-exposure)
    priorities=policy['priority'];ordered=priorities[turn%len(priorities):]+priorities[:turn%len(priorities)]
    for sku in ordered:
        product=catalog.get(sku,{})
        if sku in blocked or product.get('kind')!='goods':continue
        upper=math.ceil(product['list_price']*.75)
        if upper<=remaining:return sku,upper
    return None

def allocation_plan(ranked, inventory, available, blocked=(), available_slots=None):
    """Allocate by sell-through, holdings, next-window need and contribution.

    There is no fixed four-SKU loop or unit cap. Quantity is bounded by the
    evidence forecast and protected capital, not an arbitrary constant.
    """
    holding={}
    for unit in inventory.get('units',[]):holding[unit['sku']]=holding.get(unit['sku'],0)+1
    choices=[]
    for row in ranked:
        if row.get('sku') in blocked or row.get('expected_net',0)<=0:continue
        velocity=row.get('sales_1h',0)+row.get('sales_6h',0)/6
        unmet=row.get('own_stockouts_6h',0)*.5+row.get('own_inquiries_6h',0)*.2
        forecast=(velocity+unmet)*row.get('estimated_sale_likelihood',0)
        need=max(0,math.ceil(forecast)-holding.get(row['sku'],0))
        if need<=0:continue
        choices.append((-(row.get('score',0)*row['expected_net']/max(1,row['upper_cost'])),row['sku'],need,row))
    plan=[];slots=(250-len(inventory.get('units',[]))) if available_slots is None else available_slots
    for _,sku,need,row in sorted(choices):
        qty=min(need,int(available//row['upper_cost']),max(0,slots))
        if qty<=0:continue
        plan.append({'sku':sku,'qty':qty,'upper_unit_cost':row['upper_cost'],
                     'capital':qty*row['upper_cost'],'evidence':row})
        available-=qty*row['upper_cost']
        slots-=qty
    return plan

def supply_result(status, body, upper):
    """Classify a received response; no response is handled as uncertain."""
    if status>=500:return 'uncertain',None
    if status!=200:return 'refused',None
    cost=body.get('total') if isinstance(body,dict) else None
    if type(cost) is not int or not 0<cost<=upper:return 'reconcile',None
    return 'paid',cost

def own_registration(body, name='25512088'):
    rows=body.get('shops',[]) if isinstance(body,dict) else body
    me=next((s for s in rows if s.get('name')==name),None)
    if not me:raise ConnectionError('own shop absent from registry snapshot')
    return me

def retry_join(operation, report=None, attempts=None, sleep=time.sleep):
    def guarded():
        try:return operation()
        except SystemExit as exc:raise ConnectionError('market join/card check failed') from exc
    return retry_read(guarded,attempts=attempts,report=report,sleep=sleep)

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--state',default='.market');args=ap.parse_args()
    state=Path(args.state).resolve();policy=settings();state.mkdir(exist_ok=True)
    lock=(state/'procurement.lock').open('a')
    try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BlockingIOError:raise SystemExit('Another campaign worker owns the lock')
    db=sqlite3.connect(state/'procurement.sqlite3');db.row_factory=sqlite3.Row
    db.execute('CREATE TABLE IF NOT EXISTS purchases(id INTEGER PRIMARY KEY,campaign TEXT,sku TEXT,upper_cost INTEGER,cost INTEGER,status TEXT,created REAL,detail TEXT)');db.commit()
    spec=importlib.util.spec_from_file_location('kit',ROOT.parents[2]/'scripts/market_kit.py');kit=importlib.util.module_from_spec(spec);spec.loader.exec_module(kit)
    assert (ROOT/'shop_key.pem').exists()
    key=kit.load_key(ROOT/'shop_key.pem');market=kit.Market('https://market.ourolabs.ai',os.environ.get('MARKET_OWNER'))
    def log(**data):print(json.dumps(dict(ts=time.time(),**data),ensure_ascii=False),flush=True)
    def get_registration():
        response=market.c.get(market.base+'/api/shops');response.raise_for_status()
        return own_registration(response.json())
    me=retry_read(get_registration, attempts=None, report=log)
    retry_join(lambda:market.join('shop','25512088',key,me['url']),report=log)
    catalog=json.loads((state/'catalog.json').read_text());blocked=set();window=None
    def log(**data):print(json.dumps(dict(ts=time.time(),**data),ensure_ascii=False),flush=True)
    log(event='started',campaign=policy['campaign'],capital_policy=policy['budget_basis'])
    while True:
        rows=db.execute('SELECT * FROM purchases WHERE campaign=?',(policy['campaign'],)).fetchall()
        spent=sum(r['cost'] or 0 for r in rows if r['status']=='paid');pending=sum(r['upper_cost'] for r in rows if r['status'] in ('sent','uncertain'))
        if pending:log(event='uncertain_stop',spent=spent,reserved=pending);break
        def get_inventory():
            response=market.call('GET','/api/inventory','shop');response.raise_for_status();return response.json()
        inv=retry_read(get_inventory, attempts=None, report=log)
        if inv.get('round')!=3:log(event='wrong_round');break
        exposure=0
        with sqlite3.connect(state/'referrals.sqlite3') as referrals:
            if referrals.execute("SELECT 1 FROM sqlite_master WHERE name='warranties'").fetchone():
                exposure=referrals.execute('SELECT COALESCE(SUM(w.amount),0) FROM warranties w JOIN sales s ON s.task=w.task WHERE s.refunded=0 AND (s.settled IS NULL OR s.settled>?)',(time.time()-21600,)).fetchone()[0]
        with sqlite3.connect(state/'referrals.sqlite3') as referrals:
            exposure+=referrals.execute('SELECT COALESCE(SUM(credit),0) FROM members').fetchone()[0]
            exposure+=referrals.execute('SELECT COALESCE(SUM(reward),0) FROM sales WHERE awarded=0 AND refunded=0').fetchone()[0]
        inventory_cost=sum(u['cost'] for u in inv['units'])
        restock=inv.get('restock',{})
        if window!=restock.get('opens_at'):window=restock.get('opens_at');blocked.clear()
        opening=restock.get('opens_at')
        strategy=read_json(state/'strategy.json',{})
        if not isinstance(opening,(int,float)):
            log(event='missing_supply_window');time.sleep(20);continue
        if (strategy.get('opens_at')!=opening or strategy.get('version')!=3) and time.time()>=opening-180:
            try:strategy=snapshot(state,catalog,opening,market.base) or {}
            except Exception as exc:
                log(event='selection_retry',error=type(exc).__name__);time.sleep(20);continue
        if strategy.get('opens_at')!=opening or strategy.get('version')!=3:
            log(event='waiting_selection',select_at=opening-180,spent=spent)
            time.sleep(min(20,max(1,opening-180-time.time())));continue
        ranked=strategy.get('top10',[])
        if not ranked:
            log(event='no_qualified_top10');time.sleep(20);continue
        ranked=[r for r in ranked if r.get('expected_net',0)>0]
        available=capital_available(policy,inv,exposure,pending)
        slots=max(0,policy.get('max_exposed_units',250)-len(inv.get('units',[])))
        plan=allocation_plan(ranked,inv,available,blocked,slots)
        if not plan:
            if blocked:time.sleep(10);continue
            log(event='evidence_or_capacity_wait',lifetime_spent=spent,inventory_cost=inventory_cost,
                deployable_capital=available,available_slots=slots,protected_liabilities=exposure)
            time.sleep(60);continue
        if not restock.get('open'):
            log(event='waiting_supply',opens_at=restock.get('opens_at'),spent=spent)
            time.sleep(min(20,max(2,restock.get('opens_at',time.time()+20)-time.time())));continue
        selected=plan[0];sku=selected['sku'];qty=selected['qty'];upper=selected['capital']
        with db:
            cursor=db.execute('INSERT INTO purchases(campaign,sku,upper_cost,status,created) VALUES(?,?,?,?,?)',(policy['campaign'],sku,upper,'sent',time.time()));rid=cursor.lastrowid
        try:
            result=market.call('POST','/api/supply','shop',json={'sku':sku,'qty':qty})
        except Exception as exc:
            with db:db.execute("UPDATE purchases SET status='uncertain',detail=? WHERE id=?",(type(exc).__name__,rid))
            log(event='uncertain_stop',sku=sku);break
        body=result.json() if result.status_code==200 else {}
        outcome,cost=supply_result(result.status_code,body,upper)
        if outcome=='paid':
            if cost is None:
                log(event='unexpected_cost_stop',sku=sku);break
            with db:db.execute("UPDATE purchases SET status='paid',cost=?,detail=? WHERE id=?",(cost,json.dumps({'balance':body.get('balance')}),rid))
            log(event='purchased',sku=sku,qty=qty,cost=cost,spent=spent+cost,balance=body.get('balance'),
                allocation={'holding':sum(u['sku']==sku for u in inv['units']),
                            'sales_1h':selected['evidence'].get('sales_1h'),
                            'next_window_need':qty,'expected_net':selected['evidence'].get('expected_net')})
        elif outcome in ('uncertain','reconcile'):
            with db:db.execute("UPDATE purchases SET status='uncertain',detail=? WHERE id=?",(str(result.status_code),rid))
            log(event='uncertain_stop' if outcome=='uncertain' else 'reconciliation_stop',sku=sku,status=result.status_code);break
        else:
            with db:db.execute("UPDATE purchases SET status='refused',detail=? WHERE id=?",(str(result.status_code),rid))
            log(event='refused',sku=sku,status=result.status_code,reason=result.text[:180])
            if result.status_code==402:break
            if result.status_code!=429:blocked.add(sku)
            wait=3
            if result.status_code==429:
                try:wait=max(1,min(60,float(result.json().get('wait',3))+.25))
                except (TypeError,ValueError):pass
            time.sleep(wait)
        time.sleep(1)

if __name__=='__main__':main()
