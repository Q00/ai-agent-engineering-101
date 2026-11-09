"""A bounded request-for-quote market inside high-value buyer decisions.

Collect at most two independent shop quotes concurrently. Compare actual terms
before committing; the caller still verifies the final signed checkout.
"""
import concurrent.futures
import time
from .market_memory import buyer_assessment
from .protocol import quote, quote_terms


def request_text(candidate, order):
    text = f"I want to buy {candidate['sku']}. Please quote this exact SKU, certificate, grade and warranty hours."
    if order.get('kind') == 'food':
        text += f" Food must last at least {order.get('use_within_hours', 0)} hours after payment."
        text += ' State expires_at and the actual available unit. If none meets this shelf-life requirement, report unavailable.'
    else:
        text += ' Prefer a 6-hour warranty covering the full published break horizon; state the actual price and grade.'
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
        missing=terms.get('grade') not in ('A','B','C') or terms.get('warranty_hours') is None
    except ValueError as exc:
        if str(exc)!='ambiguous_ask':return task
        missing=True
    if not missing:return task
    text=(f"Please clarify ONE current offer for {candidate['sku']} on this task: ask, sku, unit, grade "
          "(A/B/C), warranty_hours. Use a structured data part if possible. I require the market certificate "
          "at checkout; without a declared grade my offer will be conditional on certified grade A, "
          "and without stated coverage I will require a minimum 6-hour warranty. This message is not acceptance.")
    reply=peer.send(text,task['id'])
    from .protocol import observe_quote
    observe_quote(journal,reply,candidate['shop'],order,'clarification')
    journal.add('buyer_terms_clarified',{'order':order['order'],'shop':candidate['shop'],'sku':candidate['sku']},role='buyer')
    return reply


def compare_quotes(order, ranked, registry, journal, config, peer_factory, deadline):
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
                from .buyer_scoring import contribution,terms_delta
                current=assessment['saved_state']
                surplus = max(0, (1-q)*c['product']['list_price'] - (1-qc)*price)
                c['score'] = ((contribution(price,c['product']['list_price'],q,qc) if current['metric']=='saved'
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
    ranked.sort(key=lambda c: c['score'], reverse=True)
    return prepared, len(selected)


def close_quotes(prepared):
    for peer, task in prepared.values():
        try:
            if hasattr(peer, 'cancel') and task.get('status', {}).get('state') == 'TASK_STATE_INPUT_REQUIRED':
                peer.cancel(task['id'])
        except Exception:
            pass
        finally:
            peer.close()
