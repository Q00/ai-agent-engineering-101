"""One time-aware order state; one authorized receipt; no nested recovery loops."""
import asyncio
import math
import sqlite3
import time
from dataclasses import dataclass,field
from pathlib import Path
from optimizer import append,read
from patch36 import outcome,outcome_components,saved_objective,target_price,comparison_price,document


@dataclass
class OrderState:
    order_id:str
    started_at:float
    expires:float
    kind:str
    acceptable:list
    budget:float
    candidates:dict=field(default_factory=dict)
    frontier:list=field(default_factory=list)
    visited:set=field(default_factory=set)
    stages:dict=field(default_factory=dict)
    decisions:list=field(default_factory=list)
    reason:str='started'
    def elapsed(self):return max(0,time.time()-self.started_at)
    def remaining(self):return self.expires-time.time()
    def summary(self):
        return dict(order_id=self.order_id,started_at=self.started_at,finished_at=time.time(),elapsed_seconds=self.elapsed(),kind=self.kind,
                    reason=self.reason,visited=len(self.visited),valid_frontier=len(self.frontier),stages=self.stages,decisions=self.decisions[-20:],
                    version=getattr(self,'version','rank-velocity-v8'))  # Budget deliberately stays only in memory.
    def update(self,key,quote,now,ttl):
        self.candidates[key]=quote
        self.refresh(now,ttl)
    def refresh(self,now,ttl):
        self.frontier=[(k,q) for k,q in self.candidates.items() if q.get('stock',True)
                       and 0<=now-q.get('at',0)<ttl and q.get('valid',True)]


def opportunity(md):
    r=document(Path(md)/'buyer-opportunity.json')
    if r and 'next_wait_by_outcome' not in r:
        try:
            from round3_metrics import cached_waits
            r=dict(r,next_wait_by_outcome=cached_waits(md,time.time()))
        except (OSError,ValueError,sqlite3.Error):pass
    return r if 0<=time.time()-r.get('at',0)<900 else {}


def action_values(value_now,value_better,probability,response_seconds,next_rate,settled_wait=None,unsettled_wait=None,checkout_probability=1.):
    """Common ALL units; skip's continuation is the measured next-order rate."""
    seconds=max(.05,response_seconds)
    settle=value_now-next_rate*seconds
    improvement=max(0,probability*(value_better-value_now))
    negotiate=value_now+improvement-next_rate*(2*seconds)
    # Skipping pays the *unsettled* cooldown; settling pays its own wait.
    # Missing observations remain unknown, preserving the v8 decision.
    wait_difference=checkout_probability*(settled_wait-unsettled_wait) if settled_wait is not None and unsettled_wait is not None else 0.
    settle-=next_rate*wait_difference
    negotiate-=next_rate*wait_difference
    return dict(settle=settle,negotiate=negotiate,skip=0.,extra_gain=improvement,
                extra_cost=next_rate*seconds,continue_bargaining=improvement>next_rate*seconds)


def reliability(shop,payments,stats):
    local=stats.get('checkout_by_shop',{}).get(shop)
    if local:return min(1,max(0,local['probability']))
    own=[p for p in payments if p.get('shop')==shop][-100:]
    return (1+sum(p.get('paid') is True for p in own))/(2+len(own))


def candidate_priority(value,response,settled_wait):
    # Two response legs plus the common, observed post-settlement wait.
    # Dividing by quote ping alone excessively rewards a fast lower score.
    return value/(2*max(.05,response)+max(.05,settled_wait or 0))


def learned_opening(stats,url,sku,grade,hours,cheap,ask):
    profiles=stats.get('opening_profiles',{})
    p=profiles.get(f'{url}|{sku}|{grade}|{hours:g}') or profiles.get(url,{})
    if p.get('settled_n',0)<3 or p.get('explicit_refusal_rate',0)<.5:return cheap,None
    ratio=p.get('floor_ratio_median');seconds=p.get('avoidable_response_seconds')
    if ratio is None or seconds is None or seconds<=0:return cheap,None
    price=max(cheap,min(math.floor(ask),math.ceil(ask*ratio)))
    return price,p


def search_ready(row,quote,order,catalog,payments,stats,elapsed,remaining):
    _,shop,sku,_=row;p=catalog[sku];prob=reliability(shop['url'],payments,stats)
    current=outcome(p['list_price'],quote['ask'],sku,quote.get('grade'),p.get('kind','goods'),quote.get('warranty_hours',0),shop['url'])
    attainable=outcome(p['list_price'],0,sku,quote.get('grade'),p.get('kind','goods'),quote.get('warranty_hours',0),shop['url'])
    gain=saved_objective(current,prob);upper=saved_objective(attainable,prob)
    response=max(.1,quote.get('response_seconds',1.))
    cost=stats.get('all_per_second',0)*min(max(.1,remaining),response)
    # Once search reached its 3s target, compare the remaining quality room in
    # attainable ALL units, never a structurally impossible fixed .95 score.
    return gain>0 and elapsed>=min(3,max(0,remaining)) and (upper-gain<=cost or current>=attainable-1e-6)


async def execute(api,shops,order,catalog,payments,memory,policy,md):
    from protocol import A2AClient,receipt,task_state,task_text
    from policy import read_price
    from buyer_compare import food_min_hours
    from rating_policy import adjusted_stars
    from round3_metrics import record_prediction
    from round3_metrics import order_key
    now=time.time();state=OrderState(str(order_key(order) or 'unknown'),now,
        order.get('expires',math.inf),order.get('kind','goods'),order.get('acceptable',[]),order['budget'])
    from capital_policy import ROUND_END
    state.expires=min(state.expires,ROUND_END)
    state.version=policy.get('version','rank-velocity-v8')
    result=dict(shop=None,receipt=None,stars=adjusted_stars(3),review='No positive time-adjusted authorized deal.',
                strategy_revision=state.version,accept={'cert':True})
    stats=opportunity(md);rate=max(0,stats.get('all_per_second',0));ttl=policy.get('buyer_quote_ttl_seconds',6)
    wait_groups=stats.get('next_wait_by_outcome',{})
    settled_wait=wait_groups.get('settled',{}).get('mean')
    unsettled_wait=wait_groups.get('unsettled',{}).get('mean')
    soft=policy.get('buyer_soft_seconds',25);first_soft=policy.get('buyer_first_candidate_seconds',12)
    try:
        api.checkout_feedback(list(payments),memory,now)
        start=time.perf_counter();rows=api.candidates(shops,order,catalog,payments,memory)
        state.stages['candidate_generation_seconds']=time.perf_counter()-start
        rows=[r for r in rows if memory.get(r[1]['url'],{}).get('avoid_until',0)<=time.time()
              and memory.get(r[1]['url'],{}).get('stock_cooldowns',{}).get(r[2],{}).get('until',0)<=time.time()]
        rows=await api.refresh_stock(rows);state.stages['candidate_seconds']=time.perf_counter()-start
        start=time.perf_counter();search_at=time.time();rows=await api.compare(rows,order,catalog,memory,None,payments=payments)
        state.stages['search_seconds']=time.perf_counter()-start
        valid_at=[r[1]['_comparison_quote']['at'] for r in rows if r[1].get('_comparison_quote',{}).get('at')]
        if valid_at:state.stages['first_valid_quote_seconds']=max(0,min(valid_at)-search_at)
        # Candidate rank uses expected contribution per response, not success
        # percentage alone. Cached quotes are merely estimates until refreshed.
        def priority(row):
            score,shop,sku,_=row;q=shop.get('_comparison_quote',{})
            if policy.get('buyer_time_rank_enabled'):
                return candidate_priority(score,q.get('response_seconds',memory.get(shop['url'],{}).get('response_seconds',1.)),settled_wait if settled_wait is not None else stats.get('next_wait_mean'))
            return score/max(.1,q.get('response_seconds',memory.get(shop['url'],{}).get('response_seconds',1.)))
        selection_at=time.perf_counter();rows.sort(key=priority,reverse=True);state.stages['candidate_selection_seconds']=time.perf_counter()-selection_at
        for score,shop,sku,_ in rows:
            state.refresh(time.time(),ttl)
            key=(shop['url'],sku)
            if key in state.visited:continue
            if state.remaining()<=3:state.reason='official_deadline';break
            audit=memory.setdefault(shop['url'],{});response=max(.1,audit.get('response_seconds',1.))
            if score<=rate*response and rate>0:
                state.decisions.append(dict(action='skip_candidate',estimated_value=score,next_rate=rate,response_seconds=response))
                state.reason='next_order_has_higher_expected_rate';continue
            if state.elapsed()>soft and score<=rate*(response+stats.get('next_wait_mean',1)):
                state.reason='soft_limit_lower_value_than_next_order';break
            start=time.perf_counter()
            if not await api.refresh_stock([(score,shop,sku,order['budget'])]):
                state.update(key,dict(stock=False,at=time.time()),time.time(),ttl);continue
            state.stages['stock_seconds']=state.stages.get('stock_seconds',0)+time.perf_counter()-start
            stock_checked_at=time.time()
            state.visited.add(key);candidate_started=time.time();task=None;last=None;opening_done=False;previous_ask=None
            selected=shop.get('_comparison_quote',{});grade=selected.get('grade');warranty=selected.get('warranty_hours',0) or 0
            minimum=min(6,warranty) if warranty>=3 and catalog[sku].get('kind')=='goods' else 0
            accept={'cert':True};required=food_min_hours(order) if state.kind=='food' else 0
            if required:accept['min_hours_left']=required
            if grade in ('A','B','C'):accept['grades']=[grade]
            async with A2AClient(shop['url'],timeout=min(policy.get('buyer_request_timeout',8),max(1,state.remaining()-1))) as client:
                try:
                    tick=time.perf_counter();await client.discover()
                    terms=' Require a market certificate. Confirm available stock, true grade, total price and warranty.'
                    if grade:terms+=f' Only grade {grade}, as previously quoted.'
                    if required:terms+=f' At least {required:g} hours of shelf life left.'
                    if minimum:terms+=f' At least {minimum:g}-hour warranty, as quoted.'
                    task=await client.send(f'I want to buy {catalog[sku]["name"]} ({sku}). Quote the total price.'+terms)
                    response=max(.05,time.perf_counter()-tick);audit['response_seconds']=response
                    while True:
                        status=task_state(task)
                        if status=='TASK_STATE_COMPLETED':
                            if state.remaining()<=0:state.reason='official_or_round_deadline';break
                            # Never stop a receipt/checkouts path for a soft budget.
                            validation_started=time.perf_counter();rec=receipt(task,sku,order['budget'],last)
                            why=api.terms_failure(rec,order,minimum)
                            if why:raise ValueError(why)
                            if grade and rec.get('claim') not in (None,grade):raise ValueError('receipt grade changed from agreed terms')
                            # The selected SKU was checked just before the offer.
                            # Reuse that bounded observation; the Kit still checks
                            # the exact certified unit during its single checkout.
                            if time.time()-stock_checked_at>=ttl and state.remaining()>4:
                                api._stock_cache.clear()
                                if not await api.refresh_stock([(score,shop,sku,order['budget'])]):raise ValueError('stock disappeared before checkout')
                            if grade:accept['grades']=[grade]
                            if minimum:accept['warranty_hours']=minimum
                            state.stages['validation_seconds']=time.perf_counter()-validation_started
                            state.stages['final_candidate_selected_seconds']=time.time()-state.started_at
                            state.reason='authorized_receipt_ready'
                            p=catalog[sku];prediction=outcome_components(p['list_price'],rec['price'],sku,grade,p.get('kind','goods'),warranty,shop['url'])
                            prediction['expected_score']=outcome(p['list_price'],rec['price'],sku,grade,p.get('kind','goods'),warranty,shop['url'])
                            record_prediction(md,order,rec,prediction,state.summary())
                            result.update(shop=shop['url'],receipt=rec,accept=accept,review='Authorized time-aware purchase; kit verifies signature and completes one checkout.')
                            return result
                        if status!='TASK_STATE_INPUT_REQUIRED':break
                        if state.remaining()<=3:state.reason='official_deadline';break
                        text=task_text(task);life=api.stated_lifetime(text)
                        if required and life is not None and life<required:raise ValueError('insufficient food lifetime')
                        ask=await read_price(text,role='ask',metadata=task.get('status',{}).get('message',{}).get('metadata',{}),allow_model=False)
                        if not isinstance(ask,(int,float)) or isinstance(ask,bool) or not math.isfinite(ask) or ask<=0:break
                        new_grade,new_warranty=api.continued_quote_terms(text,grade,warranty)
                        if grade and new_grade!=grade:raise ValueError('required grade changed')
                        if catalog[sku].get('kind')!='goods':new_warranty=0
                        if minimum and new_warranty<minimum:raise ValueError('required warranty withdrawn')
                        grade,warranty=new_grade,new_warranty;minimum=max(minimum,min(6,warranty) if warranty>=3 else 0)
                        probability=reliability(shop['url'],payments,stats);p=catalog[sku]
                        if time.time()-stock_checked_at>ttl:
                            if not await api.refresh_stock([(score,shop,sku,order['budget'])]):
                                state.update(key,dict(at=time.time(),stock=False),time.time(),ttl);state.reason='stock_no_longer_valid';break
                            stock_checked_at=time.time()
                        actual=outcome(p['list_price'],ask,sku,grade,p.get('kind','goods'),warranty,shop['url'])
                        # outcome already includes full use/break/refund risk.
                        # No official rule scales a paid deal's score linearly
                        # by the fraction of six hours left in this round.
                        value=saved_objective(actual,probability)
                        quote=dict(at=time.time(),sku=sku,ask=ask,grade=grade,warranty_hours=warranty,response_seconds=response,
                                   expected_score=actual,success_probability=probability,next_response_seconds=response,stock=True,stock_checked_at=stock_checked_at)
                        if life is not None:quote['hours_left']=life
                        state.update(key,quote,time.time(),ttl)
                        audit.setdefault('quotes',[]).append(quote);audit['quotes']=audit['quotes'][-30:]
                        cheap=min(ask,target_price(p['list_price'],sku,grade,share=policy.get('buyer_target_share',.95)))
                        benchmark=comparison_price(shop['url'],sku,grade,ask,warranty)
                        cheap=min(cheap,benchmark*.98)
                        cheap=max(1,min(order['budget'],math.floor(cheap)))
                        if not opening_done and last is None and policy.get('buyer_opening_learning_enabled'):
                            adaptive,profile=learned_opening(stats,shop['url'],sku,grade,warranty,cheap,ask)
                            if profile and adaptive>cheap:
                                foregone=max(0,saved_objective(outcome(p['list_price'],cheap,sku,grade,p.get('kind','goods'),warranty,shop['url']),probability)-saved_objective(outcome(p['list_price'],adaptive,sku,grade,p.get('kind','goods'),warranty,shop['url']),probability))
                                saved_time=profile['avoidable_response_seconds']*profile['explicit_refusal_rate']
                                if foregone<rate*saved_time:
                                    state.decisions.append(dict(action='learned_opening',sku=sku,shop=shop['url'],samples=profile['n'],settled_samples=profile['settled_n'],foregone_all=foregone,time_value=rate*saved_time))
                                    cheap=min(order['budget'],adaptive)
                        if last is not None:cheap=min(math.floor(ask),max(last+1,math.floor(last+.7*(ask-last))))
                        upside=saved_objective(outcome(p['list_price'],cheap,sku,grade,p.get('kind','goods'),warranty,shop['url']),probability)
                        # Response probability shrinks with repeated stalled quotes.
                        improve_probability=.5 if previous_ask is None else .75 if ask<previous_ask else .2
                        actions=action_values(value,upside,improve_probability,response,rate,settled_wait,unsettled_wait,probability)
                        # A missing empirical rate bootstraps from an actually
                        # available quote and measured next-order delay, not 0.
                        if rate<=0 and value>0:
                            rate=value/max(1,15+stats.get('next_wait_mean',1));actions=action_values(value,upside,improve_probability,response,rate,settled_wait,unsettled_wait,probability)
                        state.decisions.append(dict(action='compare',sku=sku,expected_score=actual,value=value,next_rate=rate,
                                                    response_seconds=response,values=actions))
                        acceptable=ask<=order['budget'] and actions['settle']>0
                        if acceptable and not actions['continue_bargaining']:offer=math.floor(ask)
                        elif not opening_done and upside>rate*response:offer=cheap;opening_done=True
                        elif actions['continue_bargaining'] and upside>rate*response and cheap!=last:offer=cheap
                        elif acceptable:offer=math.floor(ask)
                        else:
                            state.reason='negotiate_or_wait_loses_to_next_order';break
                        # Soft limits only reject a lower-value extension. They
                        # do not cancel an already completed/validating receipt.
                        candidate_time=time.time()-candidate_started
                        if (state.elapsed()>soft or candidate_time>first_soft) and max(value,improve_probability*upside)<=rate*response:
                            state.reason='soft_time_budget_opportunity_cost';break
                        if previous_ask==ask and opening_done and offer<ask and acceptable:offer=math.floor(ask)
                        if last==offer or (previous_ask==ask and opening_done and offer<ask):
                            state.reason='stalled_quote_no_value_in_repeat';break
                        state.decisions.append(dict(action='offer',sku=sku,shop=shop['url'],ask=ask,offer=offer,
                            grade=grade,warranty_hours=warranty,
                            quote_expected_score=actual,offer_expected_score=outcome(p['list_price'],offer,sku,grade,p.get('kind','goods'),warranty,shop['url']),
                            first_offer=last is None,accepting_quote=offer>=ask,opening_exploration=opening_done,
                            settled_wait_seconds=settled_wait,unsettled_wait_seconds=unsettled_wait))
                        first_bid=last is None
                        if first_bid:state.stages.setdefault('first_offer_seconds',time.time()-state.started_at)
                        last=offer;previous_ask=ask;tick=time.perf_counter()
                        task=await client.send(str(offer),task['id'])
                        duration=time.perf_counter()-tick
                        stage='first_offer_request_seconds' if first_bid else 'additional_negotiation_seconds'
                        state.stages[stage]=state.stages.get(stage,0)+duration
                        if task_state(task)=='TASK_STATE_COMPLETED':state.stages['receipt_issue_request_seconds']=duration
                        response=max(.05,time.perf_counter()-tick);audit['response_seconds']=.7*audit.get('response_seconds',response)+.3*response
                        state.stages['negotiation_seconds']=state.stages.get('negotiation_seconds',0)+response
                except Exception as exc:
                    audit['last_time_policy_failure']=dict(at=time.time(),reason=type(exc).__name__+': '+str(exc)[:120])
                    if 'receipt' in str(exc).lower() or 'warranty' in str(exc).lower() or 'grade changed' in str(exc).lower():audit.update(avoid_until=time.time()+600)
                    state.update(key,dict(stock=False,valid=False,at=time.time()),time.time(),ttl)
                finally:
                    if task and task_state(task)=='TASK_STATE_INPUT_REQUIRED':
                        try:await asyncio.wait_for(client.cancel(task['id']),timeout=min(1,max(.1,state.remaining())))
                        except Exception:pass
            state.update(key,dict(at=time.time(),stock=False,valid=False),time.time(),ttl)
            if state.elapsed()>soft:
                alternatives=[r[0]/max(.1,memory.get(r[1]['url'],{}).get('response_seconds',1.)) for r in rows if (r[1]['url'],r[2]) not in state.visited]
                if not alternatives or max(alternatives)<=rate:
                    state.reason='next_order_preferred_after_attempt';break
        if state.reason=='started':state.reason='no_positive_time_adjusted_candidate'
        result['review']=state.reason
        return result
    finally:
        state.stages['result_ready_seconds']=state.elapsed()
        append(Path(md)/'buyer-timing.jsonl',state.summary())
