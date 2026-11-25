"""A bounded request-for-quote market inside high-value buyer decisions.

Collect at most two independent shop quotes concurrently. Compare actual terms
before committing; the caller still verifies the final signed checkout.
"""
import concurrent.futures
import time
import queue
import threading
import math
import os
_CLEANUP = queue.Queue(maxsize=8)
_CLEANUP_LOCK = threading.Lock()
_CLEANUP_STARTED = False
from .market_memory import buyer_assessment
from .protocol import quote, quote_terms


def request_text(candidate, order):
    text = f"I want to buy {candidate['sku']}. Please quote this exact SKU, certificate, grade and warranty hours."
    if order.get('kind') == 'food':
        required=math.ceil((order.get('use_within_hours',0)+candidate.get('checkout_buffer_seconds',0)/3600)*1000000)/1000000
        text += f" Food must last at least {required:.6f} hours after payment."
        text += ' State expires_at and the actual available unit. If none meets this shelf-life requirement, report unavailable.'
    else:
        text += ' Prefer a 6-hour warranty covering the full published break horizon; state the actual price and grade.'
        if candidate.get('execution_seconds'):
            text+=' Without declared grade my bid requires certified grade A; without declared warranty my bid requires at least 6 hours. This is a quote request, not acceptance.'
    if candidate.get('avoid_units'):
        text += ' Offer a different available unit, not these recently refused units: '+', '.join(candidate['avoid_units'][:4])+'.'
    if candidate.get('paid_task_refs'):
        text+=' My previously settled tasks at your shop: '+', '.join(candidate['paid_task_refs'][:3])+'. If you offer a repeat-customer discount, quote the actual discounted price.'
    return text


def clarify(peer,task,candidate,order,journal,config,deadline):
    if (not config.get('buyer_contract_routing_enabled') or order.get('kind')!='goods' or
            task.get('status',{}).get('state')!='TASK_STATE_INPUT_REQUIRED' or deadline-time.time()<6):return task
    try:
        terms=quote_terms(task);quote(task)
        if config.get('buyer_throughput_enabled') and not config.get('service_contract_v26_enabled'):return task
        missing=terms.get('grade') not in ('A','B','C') or terms.get('warranty_hours') is None
    except ValueError as exc:
        if str(exc)!='ambiguous_ask':return task
        missing=True
    if not missing:return task
    if config.get('service_contract_v26_enabled'):
        if candidate.get('_v26_terms_attempted'):return task
        assessment=buyer_assessment(dict(candidate['product'],sku=candidate['sku']),order,terms if 'terms' in locals() else {},journal,config,shop=candidate['shop'])
        from .market_memory import buyer_price_actions
        current=assessment['saved_state']
        ask_value=quote(task) if 'terms' in locals() and 'ask' in terms else candidate.get('reference_price',0)
        actions=buyer_price_actions(ask_value,assessment['cap'],candidate['product'],ask_value,
            config.get('buyer_counter_ratio',.92),0,config.get('buyer_negotiation_turns',3),
            failure_stress=assessment['failure'],covered_failure=assessment['covered_failure'],saved_state=current,
            target_saved=assessment.get('bid_targets'),path_model=assessment.get('negotiation_path_model'))
        worthwhile=('grade' in assessment.get('conditional_terms',[]) or current['metric']!='saved' or
            any(a.get('expected_delta',{}).get('saved',0)>=0 and a.get('expected_delta',{}).get('deals',0)>0 for a in actions))
        if not worthwhile:return task
        candidate['_v26_terms_attempted']=True
    text=(f"Please clarify ONE current offer for {candidate['sku']} on this task: ask, sku, unit, grade "
          "(A/B/C), warranty_hours. Use a structured data part if possible. I require the market certificate "
          "at checkout; without a declared grade my offer will be conditional on certified grade A, "
          "and without stated coverage I will require a minimum 6-hour warranty. This message is not acceptance.")
    if config.get('service_contract_v26_enabled'):
        text+=' Quote the actual priced warranty; say warranty_hours=0 if none. Do not assume free coverage.'
        started=time.monotonic();original=task
        if hasattr(peer,'deadline'):peer.deadline=min(deadline-2,time.time()+config.get('buyer_terms_clarify_seconds',2))
        try:
            reply=peer.send(text,task['id'])
            if reply.get('id')!=task.get('id'):raise ValueError('conflicting_offer_terms')
        except Exception as exc:
            journal.add('buyer_terms_confirmation',{'order':order['order'],'shop':candidate['shop'],'sku':candidate['sku'],
                'task':task['id'],'status':'unconfirmed','error_type':type(exc).__name__,'seconds':time.monotonic()-started},role='buyer')
            return original
        finally:
            if hasattr(peer,'deadline'):peer.deadline=deadline
        journal.add('buyer_terms_confirmation',{'order':order['order'],'shop':candidate['shop'],'sku':candidate['sku'],
            'task':task['id'],'status':'response_received','seconds':time.monotonic()-started},role='buyer')
    else:reply=peer.send(text,task['id'])
    from .protocol import observe_quote
    observe_quote(journal,reply,candidate['shop'],order,'clarification')
    journal.add('buyer_terms_clarified',{'order':order['order'],'shop':candidate['shop'],'sku':candidate['sku']},role='buyer')
    return reply


def compare_quotes(order, ranked, registry, journal, config, peer_factory, deadline):
    if config.get('buyer_throughput_enabled'):
        return race_quotes(order,ranked,registry,journal,config,peer_factory,deadline)
    enabled=config.get('buyer_patch34_enabled')
    if ((not enabled and (order.get('kind') != 'goods' or order['budget'] < config.get('quote_compare_min_budget', 100000)))
            or deadline - time.time() < (10 if enabled else 45)):
        return {}, 0
    selected, seen = [], set()
    for c in ranked:
        if c['shop'] not in seen and registry.get(c['shop'], {}).get('pubkey'):
            selected.append(c)
            seen.add(c['shop'])
        if len(selected) >= min(2, config['max_shop_candidates']):
            break
    if len(selected) < 2:
        return {}, 0

    def fetch(c):
        peer = peer_factory()
        if enabled and hasattr(peer,'deadline'):
            reserve=min(4,max(1,(deadline-time.time())/3)) if config.get('buyer_championship_enabled') else 0
            peer.deadline=deadline-reserve
        try:
            peer.discover(c['shop'])
            task=peer.send(request_text(c,order))
            return peer,clarify(peer,task,c,order,journal,config,deadline)
        except Exception:
            peer.close()
            raise

    prepared = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        futures = [(c, pool.submit(fetch, c)) for c in selected]
        for c, future in futures:
            try:
                peer, task = future.result()
                from .protocol import observe_quote
                observe_quote(journal,task,c['shop'],order,'rfq')
                prepared[c['id']] = (peer, task)
                if task.get('status', {}).get('state') != 'TASK_STATE_INPUT_REQUIRED':
                    continue  # Final artifacts are verified in the normal receipt path.
                terms, ask = quote_terms(task), quote(task)
                if terms.get('sku') not in (None, c['sku']):
                    raise ValueError('quote_sku_mismatch')
                if config.get('buyer_championship_enabled'):
                    from .buyer_scoring import offer_ineligible
                    reason=offer_ineligible(terms,order,config)
                    if reason:
                        c['quote_failed']=True
                        journal.add('buyer_declined_terms',{'order':order['order'],'shop':c['shop'],
                                    'sku':c['sku'],'reason':reason,'stage':'rfq'},role='buyer')
                        continue
                assessment = buyer_assessment(dict(c['product'], sku=c['sku']), order, terms, journal, config,shop=c['shop'])
                q, qc, cap = assessment['failure'], assessment['covered_failure'], assessment['cap']
                if cap<1:
                    c['quote_failed']=True
                    journal.add('buyer_declined_terms',{'order':order['order'],'sku':c['sku'],
                                                       'reason':'no_safe_saved_price'},role='buyer')
                    continue
                price_feasible=ask<=cap
                conditional=bool(assessment.get('conditional_terms'))
                feasible = price_feasible and not conditional
                # A quote above our cap is only a low-confidence negotiation
                # option; do not pretend the capped price is actually offered.
                price = ask if price_feasible else min(cap, int(ask * .92))
                completion = 1 if feasible else .35 if price_feasible else .25
                from .buyer_scoring import contribution,terms_delta,score_inputs
                current=assessment['saved_state']
                surplus = max(0, (1-q)*c['product']['list_price'] - (1-qc)*price)
                c['score'] = ((contribution(price,c['product']['list_price'],q,qc,**score_inputs(current)) if current['metric']=='saved'
                               else surplus)*completion)
                c['expected_delta'] = terms_delta(current,price,c['product']['list_price'],q,qc,completion)
                c['evidence']['live_quote'] = dict(assessment, ask=ask, terms=terms,
                    feasible_at_ask=feasible, above_cap_completion_is_scenario=not feasible)
                journal.add('quote_comparison', {'order': order['order'], 'shop': c['shop'], 'sku': c['sku'],
                    'ask': ask, 'assessment': assessment, 'expected_surplus_at_ask':
                    (1-q)*c['product']['list_price']-(1-qc)*ask, 'feasible_at_ask': feasible}, role='buyer')
            except Exception as exc:
                # No response bodies: peer-controlled text is not operational evidence.
                journal.add('attempt', {'order': order['order'], 'shop': c['shop'], 'sku': c['sku'],
                            'success': False, 'kind': 'rfq_failed', 'error_type': type(exc).__name__,
                            'reason':str(exc) if isinstance(exc,ValueError) and str(exc) in
                                {'ambiguous_ask','conflicting_ask','unsupported_a2a','conflicting_offer_terms',
                                 'quote_sku_mismatch','invalid_money','task_required','peer_rpc_error'} else None}, role='buyer')
                c['quote_failed'] = True
    ranked[:] = [c for c in ranked if not c.get('quote_failed')]
    from .reversal import route_key
    ranked.sort(key=lambda c:route_key(c,journal,config) if config.get('reversal_v28_enabled') else (c['score'],),reverse=True)
    return prepared, len(selected)


def _close_quote(peer,task):
    try:
        if hasattr(peer,'cancel') and task.get('status',{}).get('state')=='TASK_STATE_INPUT_REQUIRED':
            peer.cancel(task['id'])
    except Exception:pass
    finally:peer.close()


def defer_close(peer, task):
    """Bound cleanup work in the persistent worker; never queue payments."""
    global _CLEANUP_STARTED
    with _CLEANUP_LOCK:
        if not _CLEANUP_STARTED:
            def run():
                while True:
                    item = _CLEANUP.get()
                    try:
                        _close_quote(*item)
                    except Exception:
                        pass
                    finally:
                        _CLEANUP.task_done()
            for _ in range(2):
                threading.Thread(target=run, name='quote-cleanup', daemon=True).start()
            _CLEANUP_STARTED = True
    try:
        _CLEANUP.put_nowait((peer, task))
        return True
    except queue.Full:
        # Bounded fallback preserves cancellation rather than leaking unlimited peers.
        _close_quote(peer, task)
        return False


def close_quotes(prepared,background=False):
    for peer,task in prepared.values():
        if background:
            if os.environ.get('R3_PERSISTENT_BUYER') == '1':
                defer_close(peer, task)
            else:
                threading.Thread(target=_close_quote, args=(peer, task), daemon=True).start()
        else:_close_quote(peer,task)


def race_quotes(order,ranked,registry,journal,config,peer_factory,deadline):
    """Only quote RPCs run concurrently. Payment stays in the strict single path."""
    selected=[];seen=set()
    for c in ranked:
        if c['shop'] in seen or not registry.get(c['shop'],{}).get('pubkey'):continue
        selected.append(c);seen.add(c['shop'])
        if len(selected)>=min(2,config['max_shop_candidates']):break
    if len(selected)<2 or deadline-time.time()<4:return {},0
    timeout=config.get('buyer_rfq_goods_seconds',5) if order.get('kind')=='goods' else config.get('buyer_rfq_food_seconds',3)
    messages=queue.Queue();lock=threading.Lock();stopped=threading.Event()
    def fetch(c):
        peer=peer_factory();task={};started=time.monotonic()
        try:
            if hasattr(peer,'deadline'):peer.deadline=min(deadline-1,time.time()+timeout)
            peer.discover(c['shop']);task=peer.send(request_text(c,order))
            if not config.get('service_contract_v26_enabled'):
                task=clarify(peer,task,c,order,journal,config,min(deadline,time.time()+timeout))
            seconds=time.monotonic()-started
            journal.add('buyer_quote_latency',{'order':order['order'],'shop':c['shop'],'sku':c['sku'],'seconds':seconds},role='buyer')
            from .protocol import observe_quote
            observe_quote(journal,task,c['shop'],order,'race')
            with lock:
                discard=stopped.is_set()
                if not discard:messages.put((c,peer,task,seconds,None))
            if discard:_close_quote(peer,task)
        except Exception as exc:
            peer.close()
            with lock:
                if not stopped.is_set():messages.put((c,None,{},time.monotonic()-started,exc))
    for c in selected:threading.Thread(target=fetch,args=(c,),daemon=True).start()
    prepared={};received=0;until=min(deadline-1,time.time()+timeout+1);strong=False
    try:
        while received<len(selected):
            left=until-time.time()
            if left<=0:break
            try:c,peer,task,seconds,error=messages.get(timeout=left)
            except queue.Empty:break
            received+=1
            if error is not None:
                c['quote_failed']=True
                journal.add('attempt',{'order':order['order'],'shop':c['shop'],'sku':c['sku'],
                    'success':False,'kind':'rfq_failed','error_type':type(error).__name__,
                    'reason':str(error) if isinstance(error,ValueError) and str(error) in
                    ['ambiguous_ask','conflicting_ask','conflicting_offer_terms','quote_sku_mismatch'] else None},role='buyer')
                continue
            prepared[c['id']]=(peer,task)
            if task.get('status',{}).get('state')!='TASK_STATE_INPUT_REQUIRED':continue
            try:
                terms=quote_terms(task);ask=quote(task)
                if terms.get('sku') not in (None,c['sku']):raise ValueError('quote_sku_mismatch')
                from .buyer_scoring import offer_ineligible,contribution,terms_delta,score_inputs
                reason=offer_ineligible(terms,order,config)
                if reason:
                    c['quote_failed']=True
                    journal.add('buyer_declined_terms',{'order':order['order'],'shop':c['shop'],'sku':c['sku'],'reason':reason,'stage':'rfq'},role='buyer')
                    continue
                a=buyer_assessment(dict(c['product'],sku=c['sku']),order,terms,journal,config,shop=c['shop'])
                if a['cap']<1:
                    c['quote_failed']=True;continue
                feasible=ask<=a['cap'] and not a['conditional_terms']
                price=min(ask,a['cap']);completion=1 if feasible else .35 if ask<=a['cap'] else .25
                if config.get('system_framework_enabled'):
                    from .contracts import observe,probability
                    observe(journal,order,c,terms,a,ask)
                    if not feasible:completion=probability(journal,order,c,a,ask,completion)
                c['score']=completion*contribution(price,c['product']['list_price'],a['failure'],a['covered_failure'],**score_inputs(a['saved_state']))
                c['expected_delta']=terms_delta(a['saved_state'],price,c['product']['list_price'],a['failure'],a['covered_failure'],completion)
                c['execution_seconds']=max(1,seconds+2)
                c['evidence']['live_quote']=dict(a,ask=ask,terms=terms,feasible_at_ask=feasible)
                journal.add('quote_comparison',{'order':order['order'],'shop':c['shop'],'sku':c['sku'],
                    'ask':ask,'assessment':a,'feasible_at_ask':feasible,'seconds':seconds},role='buyer')
                from .buyer_execution import strong_offer
                if strong_offer(c,a,ask,config):
                    c['fast_ready']=True
                    strong=True;until=min(until,time.time()+config.get('buyer_compare_grace_seconds',.35))
            except ValueError as exc:
                c['quote_failed']=True
                journal.add('attempt',{'order':order['order'],'shop':c['shop'],'sku':c['sku'],
                    'kind':'rfq_failed','success':False,'reason':str(exc) if str(exc) in
                    ['ambiguous_ask','conflicting_ask','conflicting_offer_terms','quote_sku_mismatch'] else None},role='buyer')
    finally:
        with lock:
            stopped.set();discard=[]
            while not messages.empty():discard.append(messages.get_nowait())
        for _,peer,task,_,_ in discard:
            if peer:threading.Thread(target=_close_quote,args=(peer,task),daemon=True).start()
    pending={c['id'] for c in selected if c['id'] not in prepared}
    ranked[:]=[c for c in ranked if not c.get('quote_failed') and c['id'] not in pending]
    from .reversal import route_key
    ranked.sort(key=lambda c:route_key(c,journal,config),reverse=True)
    journal.add('buyer_quote_race',{'order':order['order'],'contacted':len(selected),'received':received,
        'strong_offer':strong,'pending_discarded':len(pending)},role='buyer')
    return prepared,len(selected)
