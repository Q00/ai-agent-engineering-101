"""Cost-protected pricing and evidence-based supply decisions."""
import argparse
from collections import Counter
import json
import math
from pathlib import Path
from statistics import median
import time
from decimal import Decimal, ROUND_CEILING
from market_intel import Intel, read_json, WINDOW
from seller_procurement import settings


RATES={'goods':{'A':95,'B':80,'C':60},'food':{'A':95,'B':75,'C':45}}
LOSS_LEADER_RATE=.03
LOSS_LEADER_BUDGET=100_000
LOSS_LEADER_TRIALS_PER_SKU=5


def receipt_grade(unit,catalog=None,now=None):
    """Grade at issuance; omit uncertain food claims near a transition."""
    now=time.time() if now is None else now;product=(catalog or {}).get(unit['sku'],{})
    kind=unit.get('kind') or product.get('kind') or ('food' if unit.get('expires_at') else 'goods')
    grade=unit.get('grade')
    if grade not in ('A','B','C'):return None
    if kind=='goods':return grade
    if kind!='food':return None
    expiry=unit.get('expires_at');shelf=product.get('shelf_hours')
    if (not isinstance(expiry,(int,float)) or not math.isfinite(expiry) or
        not isinstance(shelf,(int,float)) or not math.isfinite(shelf) or shelf<=0 or expiry<=now+120):return None
    def at(ts):
        fraction=(expiry-ts)/(3600*shelf)
        return 'A' if fraction>=.8 else 'B' if fraction>=.45 else 'C'
    current=at(now)
    if current<grade or at(now+120)!=current:return None
    return current


def public_price(unit,catalog=None,now=None):
    """Initial grade and freshness price; the protected floor is applied later."""
    now=time.time() if now is None else now;product=(catalog or {}).get(unit['sku'],{})
    kind=unit.get('kind') or product.get('kind') or ('food' if unit.get('expires_at') else 'goods')
    grade=unit.get('grade');price=unit['list_price']
    if kind not in RATES or grade not in RATES[kind]:raise ValueError('Unknown product kind or grade: cannot quote')
    if not isinstance(price,(int,float)) or isinstance(price,bool) or not math.isfinite(price) or price<=0:
        raise ValueError('Invalid list price')
    freshness=Decimal(1)
    if kind=='food':
        expiry=unit.get('expires_at')
        if not isinstance(expiry,(int,float)) or not math.isfinite(expiry) or expiry<=now:
            raise ValueError('Expired food or missing expiry: cannot quote')
        hours=(Decimal(str(expiry))-Decimal(str(now)))/3600;shelf=product.get('shelf_hours')
        if isinstance(shelf,(int,float)) and math.isfinite(shelf) and shelf>0:
            fraction=hours/Decimal(str(shelf));calculated='A' if fraction>=Decimal('.8') else 'B' if fraction>=Decimal('.45') else 'C'
            grade=max(grade,calculated)
        freshness=Decimal('.2')+Decimal('.8')*min(Decimal(1),hours/24)
    amount=Decimal(str(price))*RATES[kind][grade]/100*freshness
    return max(1,int(amount.to_integral_value(rounding=ROUND_CEILING)))


def cost_floor(cost, policy):
    # Net after benefits retains 8% of cost, plus 1% storage allowance.
    reserve=math.ceil(cost*.09)
    founder=policy.get('founder_percent',5)/100
    acquisition=policy.get('acquisition_margin_percent',25)/100
    return max(math.ceil((cost+reserve)/(1-founder)), cost+math.ceil(reserve/(1-acquisition)))


def warranty_hours(unit, program=None):
    """Expand warranty only after settled, non-refunded cohorts mature."""
    if unit.get('kind')!='goods':return 0
    if program is None:return 6
    settled=program.db.execute(
        'SELECT COUNT(*) FROM sales WHERE sku=? AND ledger IS NOT NULL',(unit.get('sku'),)).fetchone()[0]
    refunded=program.db.execute(
        'SELECT COUNT(*) FROM sales WHERE sku=? AND ledger IS NOT NULL AND refunded=1',(unit.get('sku'),)).fetchone()[0]
    if refunded:return 6
    if settled>=30:return 72
    if settled>=15:return 48
    if settled>=5:return 24
    return 6


def protected_floor(unit, policy, hazard=.15):
    # Cost + storage + profit / (1-breakage), using a matured public cohort
    # when available and the conservative 15% prior otherwise.
    floor=cost_floor(unit['cost'],policy)
    if not warranty_hours(unit):return floor
    cost=unit['cost'];reserve=math.ceil(cost*.09)
    acquisition=policy.get('acquisition_margin_percent',25)/100
    founder=policy.get('founder_percent',5)/100
    survival=max(.5,1-max(0,min(.49,hazard)))
    return max(floor,math.ceil((cost+reserve)/(survival*(1-founder))),
               math.ceil((cost*(1-acquisition)+reserve)/(survival-acquisition)))


def observed_hazard(intel,shop_name='25512088'):
    if intel:
        board=intel.source('board',{},max_age=900) or {}
        row=next((x for x in board.get('shops',[]) if str(x.get('name'))==str(shop_name)),{})
        claims=row.get('claims');broken=row.get('broken')
        if isinstance(claims,int) and isinstance(broken,int) and claims>=50 and 0<=broken<=claims:
            return (broken+2)/(claims+4),f'public-matured:{claims}'
    return .15,'conservative-unmeasured'


def liquidation_discount(unit, holdings, recent_sales_6h, now=None, failed_quotes_6h=0):
    """Bounded markdown for capital trapped in evidenced excess stock.

    A small or selling cohort is never discounted.  The ladder changes only
    the asking margin; ``protected_floor`` below still pays every obligation.
    """
    now=time.time() if now is None else now
    supplied=unit.get('supplied_at')
    if holdings<8 or recent_sales_6h>0 or not isinstance(supplied,(int,float)):
        return 0.0
    age=max(0,now-supplied)
    if age<6*3600:return 0.0
    # Do not wait for the time ladder when buyers have already rejected the
    # marked-down cohort.  The protected floor below remains the hard bound.
    if failed_quotes_6h>=2:return .25
    if age<12*3600:return .05
    if age<24*3600:return .10
    if age<48*3600:return .15
    return .25


def negotiate(unit, current, offered, catalog, program, token='', ref=''):
    hazard=current.get('hazard_probability',.15)
    floor=protected_floor(unit,program.p,hazard)
    minimum=program.quote(floor,unit['cost'],token=token,ref=ref)
    if offered>=minimum['price']:
        lo,hi=floor,current['base']
        while lo<hi:
            mid=(lo+hi+1)//2
            if program.quote(mid,unit['cost'],token=token,ref=ref)['price']<=offered:lo=mid
            else:hi=mid-1
        q=program.quote(lo,unit['cost'],token=token,ref=ref)
    else:
        q=program.quote(max(floor,(current['base']+floor)//2),unit['cost'],token=token,ref=ref)
    q['warranty_hours']=warranty_hours(unit,program)
    if q['price']*((1-hazard) if q['warranty_hours'] else 1)-q['reward'] < unit['cost']+math.ceil(unit['cost']*.09):
        return current
    q['hazard_probability']=hazard;q['hazard_basis']=current.get('hazard_basis','conservative-unmeasured')
    return q if q['price']<current['price'] else current


def sale_quote(unit, catalog, program, intel=None, token='', ref='', *, record=True, holdings=1):
    base=public_price(unit,catalog)  # Also validates expiry and grade.
    hours=(unit['expires_at']-time.time())/3600 if unit.get('expires_at') else None
    warranty=warranty_hours(unit,program)
    comp=intel.comparable(unit['sku'],unit['grade'],hours,warranty) if intel else {'count':0,'median':None,'p10':None}
    acceptance_first=comp.get('p10') is not None
    listed=unit.get('list_price') or catalog.get(unit['sku'],{}).get('list_price')
    if listed and comp.get('buyer_median') is not None:
        comp['buyer_median_discount_pct']=round((1-comp['buyer_median']/listed)*100,1)
    if listed and comp.get('p10') is not None:
        comp['p90_discount_pct']=round((1-comp['p10']/listed)*100,1)
    if acceptance_first:
        # A static buyer population has already demonstrated willingness to
        # pay at or above this exact-condition lower-tail price.
        base=math.ceil(comp['p10'])
    elif comp['median']:
        base=math.ceil(comp['median']*.99)
    adjustment=1.0;competitors=0;substitutes=0;sold=0;quotes=0
    if intel:
        shops=intel.source('shops',None,max_age=180)
        if shops:
            rows=shops.get('shops',[]) if isinstance(shops,dict) else shops
            category=catalog.get(unit['sku'],{}).get('category');listed=unit.get('list_price') or catalog.get(unit['sku'],{}).get('list_price',0)
            for shop in rows:
                if shop.get('name')==program.p['shop_name']:continue
                offers=shop.get('offers') or [{'sku':sku,'qty':qty} for sku,qty in (shop.get('stock') or {}).items()]
                for offer in offers:
                    qty=offer.get('qty',0);sku=offer.get('sku');product=catalog.get(sku,{})
                    if type(qty) not in (int,float) or qty<=0:continue
                    if sku==unit['sku']:competitors+=qty
                    elif product.get('category')==category and listed*.5<=product.get('list_price',0)<=listed*1.5:
                        substitutes+=qty
            sold=program.db.execute('SELECT COUNT(*) FROM sales WHERE sku=? AND settled>? AND refunded=0',(unit['sku'],time.time()-21600)).fetchone()[0]
            for row in intel.db.execute("SELECT body FROM decisions WHERE role='seller_quote' AND ts>?",(time.time()-21600,)):
                if json.loads(row[0]).get('sku')==unit['sku']:quotes+=1
            # Scarcity is never sufficient: an increase also requires observed
            # quote conversion and actual sales. Substitute stock counts as competition.
            if (not acceptance_first and quotes>=5 and sold>=2 and sold/quotes>=.2
                    and competitors+substitutes*.35<=1):
                adjustment=1.02
            elif quotes>=5 and sold==0 and competitors+substitutes>0:
                adjustment=.98
            base=math.ceil(base*adjustment)
    recent_sales=program.db.execute(
        'SELECT COUNT(*) FROM sales WHERE sku=? AND settled>? AND refunded=0',
        (unit['sku'],time.time()-21600)).fetchone()[0]
    markdown=liquidation_discount(unit,holdings,recent_sales,failed_quotes_6h=quotes)
    hazard,hazard_basis=observed_hazard(intel,program.p.get('shop_name','25512088'))
    protected=protected_floor(unit,program.p,hazard);floor=protected;loss_leader=False
    projected_loss=max(0,unit['cost']-(comp.get('p10') or unit['cost']))
    loss_used=program.db.execute(
        'SELECT COALESCE(SUM(CASE WHEN cost>amount THEN cost-amount ELSE 0 END),0) FROM sales WHERE refunded=0').fetchone()[0]
    loss_trials=program.db.execute(
        'SELECT COUNT(*) FROM sales WHERE sku=? AND amount<cost',(unit['sku'],)).fetchone()[0]
    cohort_floor=math.ceil(unit['cost']*(1-LOSS_LEADER_RATE))
    if (acceptance_first and not token and not ref and comp['p10']>=cohort_floor
            and loss_used+projected_loss<=LOSS_LEADER_BUDGET
            and (not projected_loss or loss_trials<LOSS_LEADER_TRIALS_PER_SKU)):
        # A bounded public cohort may trade up to 3% raw principal for direct
        # conversion evidence. Legacy benefits never stack with this experiment.
        floor=comp['p10'];loss_leader=floor<protected
    if markdown:
        base=max(floor,math.ceil(base*(1-markdown)))
    q=program.quote(max(base,floor),unit['cost'],token=token,ref=ref)
    q['warranty_hours']=warranty;q['hazard_probability']=hazard;q['hazard_basis']=hazard_basis
    q['liquidation']=bool(markdown);q['liquidation_discount']=markdown
    q['liquidation_basis']='excess_stock_no_own_sale_6h' if markdown else None
    q['loss_leader']=loss_leader;q['loss_budget_remaining']=max(0,LOSS_LEADER_BUDGET-loss_used-projected_loss)
    safe_margin=q['price']*((1-hazard) if q['warranty_hours'] else 1)-q['reward'] >= unit['cost']+math.ceil(unit['cost']*.09)
    bounded_trial=(loss_leader and q['reward']==0 and q['price']>=cohort_floor)
    if not (safe_margin or bounded_trial):
        raise ValueError('Net margin guard rejected quote')
    if intel and record:
        intel.decision('seller_quote',{'sku':unit['sku'],'cost':unit['cost'],'price':q['price'],
                       'reward':q['reward'],'floor':floor,'protected_floor':protected,
                       'comparable':comp,'market_adjustment':adjustment,
                       'pricing_basis':'exact_condition_p10' if acceptance_first else
                                       ('settlement_median' if comp['median'] else 'grade_formula'),
                       'same_competitor_units':competitors,'substitute_competitor_units':substitutes,
                       'quotes_6h':quotes,'sales_6h':sold,
                       'warranty_hours':warranty,
                       'hazard_probability':hazard,'hazard_basis':hazard_basis,
                       'holdings':holdings,'recent_sales_6h':recent_sales,
                       'liquidation':bool(markdown),'liquidation_discount':markdown,
                       'loss_leader':loss_leader,'projected_raw_loss':projected_loss,
                       'loss_budget_remaining':q['loss_budget_remaining'],
                       'source':'exact_condition_p10' if acceptance_first else
                                ('settlement_median' if comp['median'] else 'grade_formula_cost_protected')})
    return q


def graded_demand(sku, product, intel, now):
    """Unknown arrival grade: separate scenarios and exclude unsupported warranties."""
    food=product.get('kind')=='food'
    probabilities={'A':.25,'B':.50,'C':.25} if food else {'A':.70,'B':.20,'C':.10}
    upper_cost={'A':.75,'B':.60,'C':.45}
    rows=intel.db.execute('SELECT * FROM trades WHERE sku=? AND ts>?',(sku,now-WINDOW)).fetchall()
    scenarios=[]; supported=0; demand=0
    for grade,probability in probabilities.items():
        comparable=[r for r in rows if r['grade']==grade and
                    ((food and r['hours'] is not None and r['hours']>0) or
                     (not food and r['hours'] is None and r['warranty']==(6 if product.get('kind')=='goods' else 0)))]
        shelf=product.get('shelf_hours')
        if food and isinstance(shelf,(int,float)) and shelf>0:
            lo,hi={'A':(.8,1),'B':(.45,.8),'C':(0,.45)}[grade]
            comparable=[r for r in comparable if lo*shelf<=r['hours']<=hi*shelf]
        cost=math.ceil(product['list_price']*upper_cost[grade])
        price=int(median([r['price'] for r in comparable])) if comparable else None
        floor=protected_floor(dict(cost=cost,kind=product.get('kind')),{'founder_percent':5,'acquisition_margin_percent':25})
        viable=len(comparable)>=3 and price>=floor
        scenarios.append(dict(grade=grade,sales=len(comparable),median=price,
                              upper_cost=cost,required_price=floor,viable=viable))
        if viable:supported+=probability;demand+=len(comparable)
    return dict(sku=sku,grade_scenarios=scenarios,supported_arrival_probability=round(supported,2),
                compatible_sales=demand,uncertainty='Other arrival grades may remain unsold.')


def restock_plan(inv,catalog,intel,now=None):
    now=time.time() if now is None else now
    if settings().get('external_campaign'):
        return {'buy':[], 'reason':'authorized_adaptive_campaign_worker'}
    if inv.get('round')!=3 or not inv.get('restock',{}).get('open'):
        return {'buy':[], 'reason':'supply_closed'}
    stock=inv.get('units',[])
    reserve=max(500000,inv.get('bankrupt_line',500000))
    exposure=0
    db=intel.root/'referrals.sqlite3'
    if db.exists():
        import sqlite3
        with sqlite3.connect(str(db)) as conn:
            if conn.execute("SELECT 1 FROM sqlite_master WHERE name='warranties'").fetchone():
                exposure=conn.execute('SELECT COALESCE(SUM(w.amount),0) FROM warranties w JOIN sales s ON s.task=w.task WHERE s.refunded=0 AND (s.settled IS NULL OR s.settled>?)',(now-21600,)).fetchone()[0]
    reserve+=exposure
    inventory_cost=sum(u.get('cost',0) for u in stock)
    from seller_procurement import capital_available
    budget=capital_available(settings(),inv,exposure)
    if not budget:return {'buy':[],'reason':'inventory_or_cash_limit'}
    if now-intel.get('ledger_through',0)>900:
        return {'buy':[],'reason':'market_evidence_stale'}
    have=Counter(u['sku'] for u in stock)
    volume={r['sku']:r['n'] for r in intel.db.execute('SELECT sku,COUNT(*) n FROM trades WHERE ts>? GROUP BY sku',(now-WINDOW,))}
    registry=(intel.source('shops',{}) or {}).get('shops',[])
    supply=Counter()
    for shop in registry:
        for sku,qty in shop.get('stock',{}).items():
            if type(qty) is int and qty>0:supply[sku]+=qty
    demand=intel.source('demand',[]) or []
    categories={d.get('category') for d in demand if isinstance(d,dict)} if isinstance(demand,list) else set()
    # Forum refs are only evidence when they point to observed settled trades.
    posts=intel.source('forum',[]) or []
    if isinstance(posts,dict):posts=posts.get('posts',[])
    endorsed=Counter()
    for post in posts:
        if post.get('likes',0)<=post.get('dislikes',0):continue
        for ref in post.get('refs',[]):
            seq=ref.get('seq') if isinstance(ref,dict) else ref
            if type(seq) is not int:continue
            row=intel.db.execute('SELECT sku FROM trades WHERE seq=? AND ts>?',(seq,now-WINDOW)).fetchone()
            if row:endorsed[row[0]]+=1
    options=[]
    for sku,n in volume.items():
        product=catalog.get(sku)
        if not product:continue
        if categories and product.get('category') not in categories:continue
        evidence=graded_demand(sku,product,intel,now)
        if evidence['supported_arrival_probability']<.25:continue
        # Cash budget uses worst acquisition cost; demand comparisons use each grade.
        worst=math.ceil(product['list_price']*.75)
        score=evidence['compatible_sales']/(1+supply[sku])*evidence['supported_arrival_probability']
        score*=1+min(2,endorsed[sku])*.05
        evidence.update(score=round(score,4),competing_units=supply[sku])
        options.append((score,sku,worst,evidence))
    options.sort(key=lambda x:(x[0],x[1]),reverse=True)
    buys=[];selected=[]
    for score,sku,worst,evidence in options:
        proven=False;sold_recent=0
        db=intel.root/'referrals.sqlite3'
        if db.exists():
            import sqlite3
            with sqlite3.connect(str(db)) as conn:
                sold_recent=conn.execute('SELECT COUNT(*) FROM sales WHERE sku=? AND settled>? AND refunded=0',(sku,now-3600)).fetchone()[0]
                proven=sold_recent>0
        # Forecast one supply cadence from recent sell-through and matched
        # demand. No arbitrary per-SKU unit cap; holdings and capital bound it.
        target=max(1,math.ceil(sold_recent/3+evidence['compatible_sales']/18))
        qty=min(max(0,target-have[sku]),int(budget//worst))
        if qty>0:
            buys.append({'sku':sku,'qty':qty});budget-=qty*worst
            selected.append(dict(evidence,qty=qty,own_sales_proven=proven,sales_last_hour=sold_recent,target_units=target))
    return {'buy':buys,'reason':'grade_condition_matched_trial' if buys else 'no_profitable_evidence',
            'candidates':len(options),'cash_reserve':reserve,'selected':selected,
            'ranked_candidates':[x[3] for x in options[:8]]}


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--state',default='.market');args=ap.parse_args()
    state=Path(args.state);intel=Intel(state)
    try:
        plan=restock_plan(read_json(state/'inventory.json',{}),read_json(state/'catalog.json',{}),intel)
        intel.decision('restock',plan)
        print(json.dumps(plan))
    finally:intel.close()

if __name__=='__main__':main()
