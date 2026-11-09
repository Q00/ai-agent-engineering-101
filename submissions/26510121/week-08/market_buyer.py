"""R3 buyer: choose useful surplus; verify exact SKU, receipt, and acceptance policy."""
import argparse
import json
import os
import time
from r3.state import Journal, read_json
from r3.market_memory import buyer_price_actions, buyer_assessment
from r3.economy import buyer_candidates
from r3.model import Chooser
from r3.protocol import Peer, quote, quote_terms, verify_receipt
from r3.quote_market import compare_quotes, close_quotes, request_text,clarify
from r3.adaptive import begin_buyer


def buy(order, shops, journal, chooser=None, peer_factory=Peer):
    cfg, strategy = begin_buyer(journal, order)
    journal.reconcile()
    journal.add('buyer_execution', {'order': order['order'], 'strategy_version': strategy['version']},
                eid='buyer-execution:' + order['order'], role='buyer')
    empty = {"shop": "", "receipt": None, "stars": 3, "review": "No settled purchase; no seller rating inferred."}
    if cfg.get('buyer_patch34_enabled') and journal.get('market_rules',{}).get('buyer_compatible') is False:
        journal.add('buyer_rules_blocked',{'order':order['order'],'reason':'unrecognized_buyer_rules'},role='buyer')
        return empty
    if time.time() + cfg["checkout_buffer_seconds"] >= order["expires"]:
        return empty
    catalog = journal.snapshot("catalog.json", {})
    registry = journal.snapshot("registry.json", {})
    ranked = buyer_candidates(order, shops, catalog, journal,cfg)
    blocked={(e['shop'],e['sku']) for e in journal.get('checkout_exclusions:'+order['order'],[])}
    ranked=[c for c in ranked if (c['shop'],c['sku']) not in blocked]
    if not ranked:
        from r3.forum import queue_wanted
        queue_wanted(journal, order)
    chooser = chooser or Chooser(journal, cfg)
    seconds=cfg.get('buyer_goods_time_seconds',90) if order.get('kind')=='goods' else cfg.get('buyer_food_time_seconds',18)
    session=journal.get('buyer_deadline:'+order['order'])
    if session is None:
        session=min(order['expires']-cfg['checkout_buffer_seconds'],time.time()+seconds*cfg.get('checkout_max_attempts',2))
        journal.put('buyer_deadline:'+order['order'],session)
    deadline = min(order["expires"] - cfg["checkout_buffer_seconds"], session, time.time()+seconds)
    if deadline<=time.time()+2:return empty
    prepared, contacted = compare_quotes(order, ranked, registry, journal, cfg, peer_factory, deadline)
    try:
        return execute(order, journal, chooser, peer_factory, cfg, strategy, registry, ranked,
                       deadline, prepared, contacted, empty)
    finally:
        close_quotes(prepared)


def execute(order, journal, chooser, peer_factory, cfg, strategy, registry, ranked,
            deadline, prepared, contacted, empty):
    for _ in range(cfg["max_shop_candidates"]):
        if not ranked or time.time() >= deadline:
            break
        shortlist=ranked[:8]
        if order.get('kind')=='food' and cfg.get('buyer_fast_food_enabled') and not cfg.get('buyer_patch34_enabled'):
            c=shortlist[0]
            journal.add('buyer_fast_choice',{'order':order['order'],'sku':c['sku'],'shop':c['shop'],
                                            'reason':'bounded low-value search; checkout remains authoritative'})
        else:
            c = chooser.choose("buyer", shortlist, {"order": order, "strategy": strategy,
                           'buyer_execution':True,'operation':'offer_selection',
                           'forum_evidence': 'unverified discovery hint; registry and checkout remain authoritative'}, deadline=deadline,
                               critical=ranked[0]["reference_price"] > .8 * order["budget"])
        ranked = [x for x in ranked if x['id'] != c['id']]
        cached = prepared.pop(c['id'], None)
        if cached is None and contacted >= cfg['max_shop_candidates']:
            # Spend remaining attempts on already collected quotes.
            ranked = [x for x in ranked if x['id'] in prepared]
            if not ranked:
                break
            c = ranked.pop(0)
            cached = prepared.pop(c['id'])
        peer = cached[0] if cached else peer_factory()
        if cfg.get('buyer_patch34_enabled') and hasattr(peer,'deadline'):peer.deadline=deadline
        if not cached:
            contacted += 1
        started = time.monotonic()
        task, task_complete = None, False
        try:
            pub = registry.get(c["shop"], {}).get("pubkey")
            if not pub:
                raise ValueError("registered_signing_key_unavailable")
            if cached:
                task = cached[1]
            else:
                peer.discover(c["shop"])
                task = peer.send(request_text(c, order))
                from r3.protocol import observe_quote
                observe_quote(journal,task,c['shop'],order,'direct')
                task=clarify(peer,task,c,order,journal,cfg,deadline)
            agreed = None
            previous_offer = None
            required_warranty = 0
            required_grade = None
            conditional_requirements=[]
            max_turns=cfg.get('buyer_negotiation_turns',cfg['max_negotiation_turns'])
            for turn in range(max_turns + 1):
                state = task["status"]["state"]
                if state == "TASK_STATE_COMPLETED":
                    receipts = [p["data"] for a in task.get("artifacts", []) for p in a.get("parts", [])
                                if isinstance(p.get("data"), dict) and "sig_seller" in p["data"]]
                    if len(receipts) != 1:
                        raise ValueError('receipt_integrity_failure')
                    # Some peers return a signed final offer immediately. It is
                    # still subject to our cap, signature and authoritative checkout.
                    if agreed is None:
                        assessment = buyer_assessment(dict(c['product'], sku=c['sku']), order, receipts[0], journal, cfg,shop=c['shop'])
                        cap, required_warranty = assessment['cap'], assessment['required_warranty']
                        required_grade = assessment.get('required_grade',receipts[0].get('grade',receipts[0].get('claim')))
                        conditional_requirements=assessment.get('conditional_terms',[])
                        price = receipts[0].get('price')
                        if type(price) not in (int, float) or price > cap:
                            raise ValueError('receipt_integrity_failure')
                        agreed = price
                    if cfg.get('buyer_contract_routing_enabled'):
                        rec_terms=receipts[0]
                        actual_grade=rec_terms.get('grade') or rec_terms.get('claim')
                        actual_warranty=rec_terms.get('warranty_hours')
                        if required_grade and actual_grade in ('A','B','C') and actual_grade!=required_grade and 'grade' not in conditional_requirements:
                            raise ValueError('quote_grade_changed')
                        if type(actual_warranty) in (int,float) and actual_warranty<required_warranty and 'warranty_hours' not in conditional_requirements:
                            raise ValueError('quote_warranty_changed')
                        final_terms=dict(rec_terms)
                        if actual_grade in ('A','B','C'):final_terms['grade']=actual_grade
                        final_terms.setdefault('grade',required_grade)
                        final_terms.setdefault('warranty_hours',required_warranty)
                        final=buyer_assessment(dict(c['product'],sku=c['sku']),order,final_terms,journal,cfg,shop=c['shop'])
                        price=rec_terms.get('price')
                        if type(price) not in (int,float) or price>agreed or price>final['cap']:
                            raise ValueError('receipt_integrity_failure')
                        # A signed lower price is a genuine seller concession.
                        agreed=price
                        required_grade=final['required_grade']
                        required_warranty=final['required_warranty']
                    if not verify_receipt(receipts[0], pub, task=task["id"],
                            sku=c["sku"], budget=order["budget"], agreed=agreed):
                        raise ValueError("receipt_integrity_failure")
                    # A2A protobuf numbers decode as whole-valued floats; the signature canonicalizes integer money.
                    rec = dict(receipts[0], price=int(receipts[0]["price"]))
                    if cfg.get('buyer_championship_enabled'):
                        from r3.buyer_scoring import offer_ineligible
                        if offer_ineligible(rec,order,cfg):
                            raise ValueError('known_unsuitable_shelf_life')
                    if cfg.get('buyer_patch34_enabled'):
                        from r3.buyer_scoring import blocked_units
                        if rec.get('unit') in blocked_units(journal,c['shop'],c['sku'],order):
                            raise ValueError('known_unavailable_unit')
                    if rec.get('cert') is False:
                        # The kit will reject this checkout. Try another shop while the order is still alive.
                        raise ValueError('certificate_unavailable')
                    accept = {"cert": True, "expected_sku": c["sku"]}
                    if order.get("kind") == "food":
                        accept["min_hours_left"] = order.get("use_within_hours", 0) + cfg["checkout_buffer_seconds"] / 3600
                    else:
                        accept["warranty_hours"] = required_warranty
                        if required_grade in ('A', 'B', 'C'):
                            accept['grades'] = [required_grade]
                    journal.add("agreement", {"order": order["order"], "sku": c["sku"], "shop": c["shop"],
                                              "task": task["id"], "price": rec["price"], "accept": accept,
                                              "strategy_version": strategy['version']},
                                eid="agreement:" + order["order"]+':'+task['id'], role="buyer")
                    task_complete = True
                    return {"shop": c["shop"], "receipt": rec, "stars": 3,
                            "review": "Pending checkout; settled quality will determine review.", "accept": accept}
                if state != "TASK_STATE_INPUT_REQUIRED" or time.time() >= deadline:
                    break
                ask = quote(task)
                product = c["product"]
                terms = quote_terms(task)
                if terms.get('sku') not in (None, c['sku']):
                    raise ValueError('quote_sku_mismatch')
                assessment = buyer_assessment(dict(product, sku=c['sku']), order, terms, journal, cfg,shop=c['shop'])
                cap, required_warranty, stress = assessment['cap'], assessment['required_warranty'], assessment['failure']
                required_grade = assessment.get('required_grade',terms.get('grade'))
                conditional_requirements=assessment.get('conditional_terms',[])
                if (product.get('kind') == 'food' and type(terms.get('expires_at')) in (int, float) and
                        terms['expires_at'] < time.time() + order.get('use_within_hours', 0) * 3600 +
                        cfg['checkout_buffer_seconds']):
                    break  # An already unsuitable offer cannot pass the final certificate check.
                actions = buyer_price_actions(ask, cap, product, c['reference_price'],
                                              cfg['buyer_counter_ratio'], turn,
                                              max_turns, previous_offer,
                                              stress, required_warranty >= 6, assessment['covered_failure'],
                                              assessment['saved_state'] if assessment['saved_state']['metric']=='saved' else None)
                if not actions:
                    break
                if order.get('kind')=='food' and cfg.get('buyer_fast_food_enabled') and not cfg.get('buyer_patch34_enabled'):
                    move=max(actions,key=lambda a:a['score'])
                else:
                    move = chooser.choose("buyer", actions, {"ask": ask, "order": order, "turn": turn,
                                           'buyer_execution':True,'operation':'price',
                                           "product": product, "strategy": strategy}, deadline=deadline)
                agreed = move["price"]
                previous_offer = agreed
                if move['id'] == 'counter':
                    journal.add('buyer_counter', {'order': order['order'], 'shop': c['shop'], 'ask': ask,
                                'price': agreed, 'strategy_version': strategy['version']}, role='buyer')
                task = peer.send(str(agreed), task["id"])
            journal.add("attempt", {"order": order['order'], "strategy_version": strategy['version'],
                                    "shop": c["shop"], "sku": c["sku"], "success": False,
                                    'use_within_hours':order.get('use_within_hours',0),
                                    "kind": "no_agreement", "seconds": time.monotonic() - started}, role="buyer")
        except Exception as exc:
            if not isinstance(exc, ValueError):
                ranked = [x for x in ranked if x['shop'] != c['shop']]
            reason = str(exc) if isinstance(exc, ValueError) and str(exc) in {
                'certificate_unavailable', 'receipt_integrity_failure', 'registered_signing_key_unavailable',
                'ambiguous_ask', 'conflicting_ask', 'conflicting_offer_terms', 'quote_sku_mismatch', 'invalid_money',
                'known_unavailable_unit','known_unsuitable_shelf_life','quote_grade_changed','quote_warranty_changed'} else None
            journal.add("attempt", {"order": order['order'], "strategy_version": strategy['version'],
                                    "shop": c["shop"], "sku": c["sku"], "success": False,
                                    'reason': reason,
                                    'use_within_hours':order.get('use_within_hours',0),
                                    "kind": type(exc).__name__, "seconds": time.monotonic() - started}, role="buyer")
        finally:
            try:
                if not task_complete and hasattr(peer, 'cancel') and task:
                    peer.cancel(task['id'])
            except Exception:
                pass
            peer.close()
    from r3.forum import queue_wanted
    queue_wanted(journal, order)
    return empty


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--shops", required=True)
    p.add_argument("--order", required=True)
    p.add_argument("--item")
    p.add_argument("--budget", type=int)
    args = p.parse_args()
    if os.environ.get("R3_STRICT_ADAPTER") != "1":
        raise SystemExit("Use run_market.py: the exact-SKU acceptance adapter is required.")
    order = read_json(args.order, {})
    if not order.get("acceptable"):
        print(json.dumps({"shop": "", "receipt": None, "stars": 3, "review": "R3 actor waits for SKU orders."}))
    else:
        print(json.dumps(buy(order, read_json(args.shops, []), Journal()), ensure_ascii=False))
