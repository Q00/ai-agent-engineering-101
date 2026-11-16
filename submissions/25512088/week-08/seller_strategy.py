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
LOSS_LEADER_RATE=.04
CLASS_NEAR_COST_RATE=.20
LOSS_LEADER_BUDGET=100_000
LOSS_LEADER_TRIALS_PER_SKU=5
BOOK_CUSHION_TRIAL_SHARE=.90


def class_book_cushion(intel, own_name='25512088'):
    """Return fresh Class Book headroom above the next lower shop."""
    if intel is None:return 0
    board=intel.source('board',None,max_age=300)
    if not isinstance(board,dict):return 0
    players=board.get('players')
    if not isinstance(players,list):return 0
    names={str(row.get('shop',{}).get('name')) for row in players
           if isinstance(row,dict) and row.get('student') is True
           and isinstance(row.get('shop'),dict)
           and row.get('shop',{}).get('name') is not None}
    rows=[row for row in board.get('shops',[]) if str(row.get('name')) in names]
    own=next((row for row in rows if str(row.get('name'))==str(own_name)),None)
    value=own.get('book') if isinstance(own,dict) else None
    if not isinstance(value,(int,float)) or isinstance(value,bool):return 0
    lower=[row.get('book') for row in rows if isinstance(row.get('book'),(int,float))
           and not isinstance(row.get('book'),bool) and row.get('book')<value]
    return max(0,value-max(lower)) if lower else 0


def active_book_cushion_principal(program, now=None):
    """Principal in still-breakable, near-cost public trial sales."""
    now=time.time() if now is None else now
    try:
        return program.db.execute('''
            SELECT COALESCE(SUM(s.cost),0)
            FROM sales s JOIN growth_events g ON g.task=s.task
            WHERE g.event='book_cushion_trial' AND s.refunded=0
              AND (s.settled IS NULL OR s.settled>?)''',
            (now-6*3600,)).fetchone()[0]
    except Exception:
        return 0


def score_safe_loss_budget(intel, already_used, own_name='25512088'):
    """Add loss trials only while Revenue is the cheaper class-score step.

    ``already_used`` is historical and is already reflected in the current
    official Book value.  When the official student cohort proves that the
    next weighted Book step is cheaper than the next weighted Revenue step,
    preserve Book by returning the amount already spent: no new principal
    loss can pass the caller's budget check.  This lets a bounded loss trial
    help cross a nearby Revenue boundary, then switches it off after that
    boundary is crossed.  Missing/stale class evidence keeps the original
    fixed-cap behaviour.
    """
    if not intel:return LOSS_LEADER_BUDGET
    board=intel.source('board',None,max_age=300)
    if not isinstance(board,dict):return LOSS_LEADER_BUDGET
    players=board.get('players')
    if isinstance(players,list):
        student_shops={str(row.get('shop',{}).get('name')) for row in players
                       if isinstance(row,dict) and row.get('student') is True
                       and isinstance(row.get('shop'),dict)
                       and row.get('shop',{}).get('name') is not None}
        if str(own_name) in student_shops:
            class_rows=[row for row in board.get('shops',[])
                        if isinstance(row,dict) and str(row.get('name')) in student_shops]
            own=next((row for row in class_rows
                      if str(row.get('name'))==str(own_name)),None)
            def next_higher_gap(metric):
                value=own.get(metric) if isinstance(own,dict) else None
                if not isinstance(value,(int,float)) or isinstance(value,bool):return None
                higher=[row.get(metric) for row in class_rows
                        if isinstance(row.get(metric),(int,float))
                        and not isinstance(row.get(metric),bool) and row.get(metric)>value]
                return min(higher)-value if higher else None
            def next_lower_gap(metric):
                value=own.get(metric) if isinstance(own,dict) else None
                if not isinstance(value,(int,float)) or isinstance(value,bool):return None
                lower=[row.get(metric) for row in class_rows
                       if isinstance(row.get(metric),(int,float))
                       and not isinstance(row.get(metric),bool) and row.get(metric)<value]
                return value-max(lower) if lower else None
            book_cushion=next_lower_gap('book')
            revenue_gap=next_higher_gap('revenue')
            # The current class-trial cohort needs at most this much Book to
            # generate the Revenue required for the next Class percentile.
            # Open only a small additional budget when that worst-case loss
            # stays inside 75% of the fresh lower-Book boundary. This compares
            # the actual direction of both movements: Revenue rises while Book
            # can fall. The old comparison used the gap to the *higher* Book
            # shop and disabled safe near-cost turnover despite 3M+ of lower
            # boundary headroom.
            max_loss_to_revenue=(
                math.ceil(revenue_gap*CLASS_NEAR_COST_RATE/(1-CLASS_NEAR_COST_RATE))
                if isinstance(revenue_gap,(int,float)) else None)
            if (book_cushion is None or revenue_gap is None or
                    max_loss_to_revenue>book_cushion*.75):
                return max(0,int(already_used or 0))
            extra=min(LOSS_LEADER_BUDGET,max(0,int(book_cushion//4)))
            return max(0,int(already_used or 0))+extra
    rows=sorted((r for r in board.get('shops',[]) if not r.get('house') and
                 isinstance(r.get('book'),int)),key=lambda r:r['book'])
    index=next((n for n,row in enumerate(rows) if str(row.get('name'))==str(own_name)),None)
    if index is None or index==0:return LOSS_LEADER_BUDGET
    headroom=max(0,rows[index]['book']-rows[index-1]['book'])
    extra=min(LOSS_LEADER_BUDGET,headroom//4)
    return max(LOSS_LEADER_BUDGET,already_used+extra)


def buyer_preference_target(preferences,sku,now=None):
    """Return an equal-buyer lower-quartile price from the live estimate."""
    now=time.time() if now is None else now
    if not isinstance(preferences,dict) or preferences.get('version')!=3:
        return None
    generated=preferences.get('generated_at')
    if not isinstance(generated,(int,float)) or now-generated>180:
        return None
    prices=[];samples=0
    for buyer in (preferences.get('buyers') or {}).values():
        if not buyer.get('active'):continue
        item=next((x for x in buyer.get('skus',[]) if x.get('sku')==sku),None)
        if not item or not isinstance(item.get('median_paid'),(int,float)) or item['median_paid']<=0:
            continue
        prices.append(item['median_paid']);samples+=item.get('price_samples',0)
    if len(prices)<3:return None
    prices.sort();target=prices[int(.25*(len(prices)-1))]
    return {'price':math.ceil(target),'buyers':len(prices),'samples':samples,
            'basis':'active_buyer_item_lower_quartile'}


def top_seller_grade_target(intel,catalog,grade,kind,now=None):
    """Return the live top-shop median paid/list ratio for this condition."""
    if intel is None or grade not in ('A','B','C') or kind not in ('goods','food'):
        return None
    now=time.time() if now is None else now
    board=intel.source('board',None,max_age=300)
    shops=intel.source('shops',None,max_age=300)
    if not isinstance(board,dict) or not shops:return None
    registry=shops.get('shops',[]) if isinstance(shops,dict) else shops
    urls={str(row.get('name')):row.get('url') for row in registry if row.get('name') and row.get('url')}
    leaders=[row for row in sorted(board.get('shops',[]),key=lambda row:row.get('all',0),reverse=True)
             if str(row.get('name'))!='25512088'][:10]
    leader_urls={urls.get(str(row.get('name'))) for row in leaders}- {None}
    if not leader_urls:return None
    ratios=[];seen=set()
    for row in intel.db.execute('SELECT shop,sku,price FROM trades WHERE grade=? AND ts>?',(grade,now-3600)):
        if row['shop'] not in leader_urls:continue
        product=catalog.get(row['sku'],{});listed=product.get('list_price')
        if product.get('kind')!=kind or not isinstance(listed,(int,float)) or listed<=0:continue
        ratio=row['price']/listed
        if 0<ratio<=1.5:ratios.append(ratio);seen.add(row['shop'])
    if len(ratios)<50 or len(seen)<3:return None
    return {'ratio':median(ratios),'samples':len(ratios),'shops':len(seen),
            'basis':'top10_verified_settlement_grade_median_1h'}


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
    """Use the market-compatible six-hour warranty for every good.

    Historical code expanded to 24/48/72 hours when the local sale row showed
    no refund, before official broken events were reconciled into local sale
    evidence. Those historical sparse cohorts did not justify longer
    liabilities. Six hours satisfies the common buyer requirement without
    inventing safety evidence that the official ledger does not provide.
    """
    if unit.get('kind')!='goods':return 0
    return 6


def market_warranty_hours(unit, intel=None, required=0):
    """Choose only between the two conditions this seller can fund safely.

    An explicit buyer requirement remains authoritative.  When no warranty is
    requested, a mature exact-SKU/grade public cohort may select the zero-hour
    lane.  Zero-hour sales cannot create a later refund liability, so their
    lower Book-preserving floor can release costly inventory without spending
    the active Class-Book warranty cushion.  Sparse or unavailable evidence
    keeps the established six-hour default.
    """
    if unit.get('kind')!='goods':return 0
    try:required=float(required or 0)
    except (TypeError,ValueError):required=0
    if required>6:return None
    if required>0:return 6
    if intel is None:return 6
    no_warranty=intel.comparable(unit['sku'],unit.get('grade'),None,0)
    if no_warranty.get('count',0)>=5 and no_warranty.get('buyers',0)>=3:
        return 0
    return 6


def protected_floor(unit, policy, hazard=.15, warranty=None):
    # Cost + storage + profit / (1-breakage), using a matured public cohort
    # when available and the conservative 15% prior otherwise.
    floor=cost_floor(unit['cost'],policy)
    warranty=warranty_hours(unit) if warranty is None else warranty
    if not warranty:return floor
    cost=unit['cost'];reserve=math.ceil(cost*.09)
    acquisition=policy.get('acquisition_margin_percent',25)/100
    founder=policy.get('founder_percent',5)/100
    survival=max(.5,1-max(0,min(.49,hazard)))
    return max(floor,math.ceil((cost+reserve)/(survival*(1-founder))),
               math.ceil((cost*(1-acquisition)+reserve)/(survival-acquisition)))


def score_floor(unit, hazard=.15, warranty=None):
    """Preserve expected Book for an ordinary public sale.

    New referral enrollment is closed. A buyer without a legacy credential
    therefore covers unit principal, one day of storage, and measured refund
    risk. Legacy members still use ``protected_floor`` so old promises remain
    fully funded.
    """
    principal=unit['cost']+math.ceil(unit['cost']*.01)
    warranty=warranty_hours(unit) if warranty is None else warranty
    if not warranty:return principal
    survival=max(.5,1-max(0,min(.49,hazard)))
    return math.ceil(principal/survival)


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


def negotiate(unit, current, offered, catalog, program, token='', ref='', intel=None):
    hazard=current.get('hazard_probability',.15)
    legacy=bool(token or ref)
    warranty=current.get('warranty_hours',warranty_hours(unit,program))
    floor=(protected_floor(unit,program.p,hazard,warranty) if legacy else
           score_floor(unit,hazard,warranty))
    # Once verified market evidence has already opened a bounded public
    # loss-leader cohort, do not reject a buyer merely because its offer is a
    # few won below that cohort's initial evidence price.  An offer at or above
    # raw unit cost improves Book immediately; the six-hour warranty keeps the
    # measured downside short-lived.  Legacy benefits never use this path.
    public_cost_recovery=bool(not legacy and current.get('loss_leader'))
    if public_cost_recovery:
        floor=min(floor,unit['cost'])
    unwarranted_cost_recovery=bool(
        not legacy and not warranty and offered>=unit['cost'])
    if unwarranted_cost_recovery:
        # With no warranty there is no later refund event.  A sale at raw cost
        # therefore preserves Book immediately and does not consume the Class
        # Book cushion reserved for still-breakable warranty trials.
        floor=min(floor,unit['cost'])
    cushion=class_book_cushion(intel,program.p.get('shop_name','25512088'))
    active_cushion=active_book_cushion_principal(program)
    cushion_cap=math.floor(cushion*BOOK_CUSHION_TRIAL_SHARE)
    cushion_recovery=bool(
        not legacy and bool(warranty) and unit.get('kind')=='goods' and
        str(current.get('hazard_basis','')).startswith('public-matured') and
        offered>=unit['cost'] and active_cushion+unit['cost']<=cushion_cap)
    if cushion_recovery:
        # The sale itself cannot lower Book.  Even if this single live trial
        # later breaks, the reserved half-cushion keeps the current Class Book
        # boundary intact.  The six-hour query above prevents overlapping
        # near-cost trials from spending the same headroom twice.
        floor=min(floor,unit['cost'])
    # Recent live offers showed a separate, deterministic opportunity: public
    # buyers offered just below raw cost on inventory with no later refund
    # liability. The 95% cohort
    # raised the official Shop score without a refund. Once the live Revenue
    # gap fell far enough, the live aggregate exposed distinct buyer demand
    # down to the 80% cost band. The configured floor is only a maximum
    # allowance: fresh Class boundaries must still prove that reaching the next
    # Revenue percentile consumes no more than 75% of the lower Book cushion.
    # Those offers
    # cannot later create a refund liability. Permit only that narrow cohort
    # while fresh Class boundaries prove that the worst-case configured loss
    # can reach the next Revenue percentile without crossing the next lower
    # Book percentile. Historical losses are already reflected in Book; the
    # budget function grants at most 100k of additional measured principal.
    loss_used=program.db.execute(
        'SELECT COALESCE(SUM(CASE WHEN cost>amount THEN cost-amount ELSE 0 END),0) '
        'FROM sales WHERE refunded=0').fetchone()[0]
    loss_budget=score_safe_loss_budget(intel,loss_used,program.p.get('shop_name','25512088'))
    raw_loss_floor=math.ceil(unit['cost']*(1-CLASS_NEAR_COST_RATE))
    projected_raw_loss=max(0,unit['cost']-offered)
    class_near_cost_trial=bool(
        not legacy and not warranty and unit.get('kind') in ('goods','food') and
        raw_loss_floor<=offered<unit['cost'] and class_book_cushion(intel)>0 and
        loss_used+projected_raw_loss<=loss_budget)
    if class_near_cost_trial:
        floor=min(floor,raw_loss_floor)
    minimum=program.quote(floor,unit['cost'],token=token,ref=ref)
    # A numeric buyer offer just below the ordinary 1% expected-Book target is
    # better than leaving expensive principal idle when it still preserves
    # principal after measured breakage risk. Keep this exception extremely
    # narrow (within 0.5% of the normal floor), public-only, and reward-free.
    # Legacy promises and genuinely loss-making offers never use it.
    survival=max(.5,1-max(0,min(.49,hazard))) if current.get('warranty_hours') else 1
    principal_floor=math.ceil(unit['cost']/survival)
    near_book_recovery=bool(
        not legacy and not public_cost_recovery and
        offered>=principal_floor and offered>=math.ceil(minimum['price']*.995))
    if near_book_recovery:
        floor=principal_floor
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
    q['warranty_hours']=warranty
    required=unit['cost']+math.ceil(unit['cost']*(.09 if legacy else .01))
    score_safe=q['price']*((1-hazard) if q['warranty_hours'] else 1)-q['reward'] >= required
    bounded_cost_recovery=(public_cost_recovery and q['reward']==0 and
                           q['price']>=unit['cost'])
    bounded_book_recovery=(near_book_recovery and q['reward']==0 and
                           q['price']*survival>=unit['cost'])
    bounded_cushion_recovery=(cushion_recovery and q['reward']==0 and
                              q['price']>=unit['cost'])
    bounded_unwarranted_recovery=(unwarranted_cost_recovery and q['reward']==0 and
                                  q['price']>=unit['cost'])
    bounded_class_near_cost=(class_near_cost_trial and q['reward']==0 and
                             not q.get('warranty_hours') and
                             q['price']>=raw_loss_floor)
    if not (score_safe or bounded_cost_recovery or bounded_book_recovery or
            bounded_cushion_recovery or bounded_unwarranted_recovery or
            bounded_class_near_cost):
        return current
    q['hazard_probability']=hazard;q['hazard_basis']=current.get('hazard_basis','conservative-unmeasured')
    q['near_book_recovery']=bounded_book_recovery
    q['book_cushion_recovery']=bounded_cushion_recovery
    q['unwarranted_cost_recovery']=bounded_unwarranted_recovery
    q['class_near_cost_trial']=bounded_class_near_cost
    q['class_near_cost_projected_loss']=(projected_raw_loss
                                         if bounded_class_near_cost else 0)
    q['class_near_cost_loss_budget_remaining']=max(
        0,loss_budget-loss_used-projected_raw_loss)
    q['book_cushion']=cushion
    q['book_cushion_active_principal']=active_cushion
    return q if q['price']<current['price'] else current


def sale_quote(unit, catalog, program, intel=None, token='', ref='', *, record=True, holdings=1,
               buyer_preferences=None, warranty_override=None):
    now=time.time()
    base=public_price(unit,catalog)  # Also validates expiry and grade.
    hours=(unit['expires_at']-now)/3600 if unit.get('expires_at') else None
    warranty=(warranty_hours(unit,program) if warranty_override is None else
              float(warranty_override))
    if warranty not in (0,6):
        raise ValueError('Unsupported warranty duration')
    warranty=int(warranty)
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
    preference=buyer_preference_target(buyer_preferences,unit['sku'])
    preference_applied=bool(preference and preference['price']<base)
    if preference_applied:
        # Aim at a price already accepted by roughly three quarters of the
        # active-buyer cohort. The protected floor below remains authoritative.
        base=min(base,preference['price'])
    grade_target=top_seller_grade_target(intel,catalog,unit.get('grade'),unit.get('kind'))
    grade_target_price=math.ceil(listed*grade_target['ratio']) if listed and grade_target else None
    grade_target_applied=bool(grade_target_price and grade_target_price<base)
    if grade_target_applied:
        # Never open above the condition-matched top-shop median when the
        # verified market already demonstrates a lower acceptance point. The
        # cost/refund floor below remains authoritative.
        base=grade_target_price
    evidence_since=now-21600
    supplied_at=unit.get('supplied_at')
    if isinstance(supplied_at,(int,float)) and not isinstance(supplied_at,bool) and supplied_at<=now:
        evidence_since=max(evidence_since,supplied_at)
    adjustment=1.0;competitors=0;substitutes=0;sold=0;quotes=0;own_offers=[]
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
        # Our settled sales and numeric offers are local authoritative evidence
        # and do not depend on a fresh competitor registry. Keeping them under
        # ``if shops`` disabled adaptive pricing three minutes after each
        # scheduled snapshot even while current-cohort evidence kept arriving.
        sold=program.db.execute(
            'SELECT COUNT(*) FROM sales WHERE sku=? AND settled>? AND refunded=0',
            (unit['sku'],evidence_since)).fetchone()[0]
        for row in intel.db.execute(
                "SELECT role,body FROM decisions "
                "WHERE role IN ('seller_quote','seller_offer_received') AND ts>?",
                (evidence_since,)):
            signal=json.loads(row[1])
            if signal.get('sku')!=unit['sku']:continue
            if row[0]=='seller_quote':quotes+=1
            elif isinstance(signal.get('offered'),int) and signal['offered']>0:
                own_offers.append(signal['offered'])
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
        (unit['sku'],evidence_since)).fetchone()[0]
    markdown=liquidation_discount(unit,holdings,recent_sales,failed_quotes_6h=quotes)
    hazard,hazard_basis=observed_hazard(intel,program.p.get('shop_name','25512088'))
    legacy=bool(token or ref)
    protected=(protected_floor(unit,program.p,hazard,warranty) if legacy else
               score_floor(unit,hazard,warranty))
    offer_target=None;offer_target_applied=False
    poor_local_conversion=bool(not legacy and quotes>=10 and len(own_offers)>=10 and sold/quotes<.15)
    if poor_local_conversion:
        # A buyer's numeric offer is stronger local conversion evidence than a
        # market-wide settlement from another shop.  When this SKU repeatedly
        # receives offers but converts poorly, use the upper quartile of those
        # offers as the opening demand target.  The floor and bounded-loss gate
        # below still decide whether that target is safe to expose.
        own_offers.sort()
        offer_target=own_offers[math.ceil(.75*len(own_offers))-1]
        if offer_target<base:
            base=offer_target;offer_target_applied=True
    floor=protected;loss_leader=False
    market_evidence=[price for price in (comp.get('p10'),grade_target_price)
                     if isinstance(price,(int,float)) and price>0]
    loss_used=program.db.execute(
        'SELECT COALESCE(SUM(CASE WHEN cost>amount THEN cost-amount ELSE 0 END),0) FROM sales WHERE refunded=0').fetchone()[0]
    loss_budget=score_safe_loss_budget(intel,loss_used,program.p.get('shop_name','25512088'))
    loss_trials=program.db.execute(
        'SELECT COUNT(*) FROM sales WHERE sku=? AND amount<cost',(unit['sku'],)).fetchone()[0]
    cohort_floor=math.ceil(unit['cost']*(1-LOSS_LEADER_RATE))
    near_floor_demand=bool(poor_local_conversion and offer_target is not None and
                           offer_target<cohort_floor and
                           offer_target>=math.ceil(cohort_floor*.97))
    local_evidence=(offer_target if poor_local_conversion and offer_target is not None and
                    offer_target>=cohort_floor else (cohort_floor if near_floor_demand else None))
    score_evidence_floor=local_evidence if local_evidence is not None else (
        max(market_evidence) if market_evidence else None)
    projected_loss=max(0,unit['cost']-(score_evidence_floor or unit['cost']))
    if (score_evidence_floor is not None and score_evidence_floor<protected
            and not token and not ref and score_evidence_floor>=cohort_floor
            and loss_used+projected_loss<=loss_budget
            and (not projected_loss or loss_trials<LOSS_LEADER_TRIALS_PER_SKU)):
        # A bounded public cohort may trade up to 4% raw principal for direct
        # conversion evidence. Legacy benefits never stack with this experiment.
        floor=score_evidence_floor;loss_leader=floor<protected
    if markdown:
        base=max(floor,math.ceil(base*(1-markdown)))
    q=program.quote(max(base,floor),unit['cost'],token=token,ref=ref)
    q['warranty_hours']=warranty;q['hazard_probability']=hazard;q['hazard_basis']=hazard_basis
    q['liquidation']=bool(markdown);q['liquidation_discount']=markdown
    q['liquidation_basis']='excess_stock_no_own_sale_6h' if markdown else None
    q['loss_leader']=loss_leader;q['loss_budget_remaining']=max(0,loss_budget-loss_used-projected_loss)
    required=unit['cost']+math.ceil(unit['cost']*(.09 if legacy else .01))
    safe_margin=q['price']*((1-hazard) if q['warranty_hours'] else 1)-q['reward'] >= required
    bounded_trial=(loss_leader and q['reward']==0 and q['price']>=cohort_floor)
    if not (safe_margin or bounded_trial):
        raise ValueError('Net margin guard rejected quote')
    if intel and record:
        intel.decision('seller_quote',{'sku':unit['sku'],'cost':unit['cost'],'price':q['price'],
                       'reward':q['reward'],'floor':floor,'protected_floor':protected,
                       'floor_policy':('legacy_benefit_protected' if legacy else 'public_book_preserving'),
                       'comparable':comp,'market_adjustment':adjustment,
                       'buyer_preference_target':preference,'buyer_preference_applied':preference_applied,
                       'top_seller_grade_target':grade_target,
                       'top_seller_grade_target_applied':grade_target_applied,
                       'pricing_basis':('buyer_preference_lower_quartile' if preference_applied else
                           ('top10_grade_median' if grade_target_applied else
                            ('exact_condition_p10' if acceptance_first else
                             ('settlement_median' if comp['median'] else 'grade_formula')))),
                       'same_competitor_units':competitors,'substitute_competitor_units':substitutes,
                       'quotes_6h':quotes,'sales_6h':sold,
                       'local_evidence_since':evidence_since,
                       'local_evidence_window':'current_supply_cohort_or_6h',
                       'warranty_hours':warranty,
                       'hazard_probability':hazard,'hazard_basis':hazard_basis,
                       'holdings':holdings,'recent_sales_6h':recent_sales,
                       'own_offer_target':offer_target,
                       'own_offer_samples':len(own_offers),
                       'own_offer_target_applied':offer_target_applied,
                       'poor_local_conversion':poor_local_conversion,
                       'liquidation':bool(markdown),'liquidation_discount':markdown,
                       'loss_leader':loss_leader,'projected_raw_loss':projected_loss,
                       'loss_budget':loss_budget,
                       'loss_leader_evidence':(('own_offer_near_raw_loss_floor' if near_floor_demand else
                                                'own_offer_upper_quartile') if local_evidence is not None else
                           ('top10_grade_or_exact_condition' if score_evidence_floor else None)),
                       'loss_budget_remaining':q['loss_budget_remaining'],
                       'source':('buyer_preference_lower_quartile' if preference_applied else
                           ('top10_grade_median' if grade_target_applied else
                            ('exact_condition_p10' if acceptance_first else
                             ('settlement_median' if comp['median'] else 'grade_formula_cost_protected'))))})
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
                amounts=[row[0] for row in conn.execute(
                    'SELECT w.amount FROM warranties w JOIN sales s ON s.task=w.task '
                    'WHERE s.refunded=0 AND (s.settled IS NULL OR s.settled>?)',
                    (now-21600,))]
                from seller_procurement import warranty_risk_reserve
                exposure=warranty_risk_reserve(amounts)
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
