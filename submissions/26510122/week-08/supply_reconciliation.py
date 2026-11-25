"""Reconcile acquisition intents from physical-unit evidence, never from price guesses."""
from inventory_observation import timestamp
import math
from decision_audit import LINKS


def supply_window(intent):
    try:
        opens, closes = map(float, intent['window'].split(':'))
        return (opens, closes) if math.isfinite(opens) and math.isfinite(closes) and opens < closes else None
    except (KeyError, ValueError, TypeError, AttributeError):
        return None


def partial_from_closed_window(intent, now):
    """Keep the missing reservation, but allow an independent future refill."""
    window = supply_window(intent)
    return bool(pending_quantity(intent) and intent.get('observed_units')
                and window and now > window[1] + 30)


def pending_quantity(intent):
    if intent.get('status') != 'issued_unknown':
        return 0
    if intent.get('supply_resolution') in ('rejected', 'not_sent'):
        return 0
    return max(0, intent.get('qty', 1)-len(set(intent.get('observed_units', []))))


def reserved_quantity(intent):
    """A confirmed non-purchase consumes neither outstanding nor rolling spend."""
    return 0 if intent.get('supply_resolution') in ('rejected','not_sent') else intent.get('qty',1)


RECONCILIATION_EVENTS = frozenset({'procurement_supply_result', 'receipt_signed', 'settled'})


def resolve_responses(intents, history):
    changes=[]
    by_id={i.get('intent_id'):i for i in intents if i.get('intent_id')}
    grouped={}
    for e in history:
        if e.get('event')!='procurement_supply_result':continue
        grouped.setdefault(e.get('intent_id'),[]).append(e)
    for identity,rows in grouped.items():
        intent=by_id.get(identity)
        if not intent:continue
        matching=[e for e in rows if e.get('decision_id')==intent.get('decision_id')
                  and e.get('sku')==intent['sku'] and e.get('qty')==intent.get('qty',1)
                  and e.get('window')==intent.get('window')]
        if not matching:continue
        # An accepted or uncertain request always takes precedence over a release.
        accepted=intent.get('supply_resolution')=='accepted' or any(e.get('outcome')=='accepted' for e in matching)
        uncertain=intent.get('supply_resolution')=='unknown' or any(e.get('outcome')=='unknown' for e in matching)
        final=next((e for e in reversed(matching) if e.get('outcome') in ('rejected','not_sent')),None)
        resolution='accepted' if accepted else 'unknown' if uncertain else final.get('outcome') if final else None
        if resolution in ('rejected','not_sent') and intent.get('observed_units'):
            resolution='conflicting_evidence'
        if resolution and intent.get('supply_resolution')!=resolution:
            intent['supply_resolution']=resolution
            proof=next((e for e in reversed(matching) if e.get('outcome')==resolution),final or matching[-1])
            intent['resolution_evidence']={k:proof.get(k) for k in
                ('request_id','at','http_status','evidence_source','log_line','log_sha256')}
            changes.append({'intent_id':identity,'sku':intent['sku'],'resolution':resolution,
                'released_units':intent.get('qty',1) if resolution in ('rejected','not_sent') else 0,
                **{k:intent[k] for k in LINKS if intent.get(k) is not None}})
    return changes


def reconcile(intents, inventory, history, now):
    history=list(history)
    changes=resolve_responses(intents,history)
    evidence = []
    for unit in inventory.get('units', []):
        if unit.get('unit') and unit.get('sku'):
            evidence.append({'unit': unit['unit'], 'sku': unit['sku'], 'at': now,
                             'supplied_at':timestamp(unit.get('supplied_at')), 'source': 'inventory'})
    for event in history:
        at = timestamp(event.get('at'))
        kind = event.get('event')
        if (kind not in ('receipt_signed', 'settled') or at is None or at > now
                or not event.get('unit') or not event.get('sku')):
            continue
        if kind == 'settled' and event.get('ledger') is None:
            continue
        evidence.append({'unit': event['unit'], 'sku': event['sku'], 'at': at,
                         'supplied_at':timestamp(event.get('supplied_at')),
                         'source': kind, 'ledger': event.get('ledger')})
    # Evidence for an already identified unit belongs to that original intent.
    owners = {unit: i for i, intent in enumerate(intents) for unit in intent.get('observed_units', [])}
    by_sku={}
    for i,intent in enumerate(intents):by_sku.setdefault(intent['sku'],[]).append((i,intent))
    for proof in evidence:
        candidates = [i for i, intent in by_sku.get(proof['sku'],[])
                      if intent['at'] <= proof['at']
                      and proof['unit'] not in intent.get('prior_units', [])
                      and (pending_quantity(intent) or proof['unit'] in intent.get('observed_units', [])
                           or intent.get('supply_resolution') in ('rejected','not_sent'))]
        supplied = proof.get('supplied_at')
        if supplied is not None and supplied > now + 2:
            continue
        if supplied is not None:
            candidates = [i for i in candidates if intents[i]['at'] <= supplied + 2
                          and (supply_window(intents[i]) is None
                               or supplied <= supply_window(intents[i])[1])]
        if proof['unit'] in owners:
            candidates = [owners[proof['unit']]] if owners[proof['unit']] in candidates else []
        if len(candidates) != 1:
            continue  # ambiguous repeated same-SKU acquisitions need stronger evidence
        index = candidates[0]
        intent = intents[index]
        if intent.get('supply_resolution') in ('rejected','not_sent'):
            intent['supply_resolution']='conflicting_evidence'
        old = intent['status']
        previous_units=set(intent.get('observed_units', []))
        previous_settled=set(intent.get('settled_units', []))
        units = previous_units | {proof['unit']}
        intent['observed_units'] = sorted(units)
        owners[proof['unit']] = index
        if proof['source'] == 'settled':
            intent['settled_units'] = sorted(set(intent.get('settled_units', [])) | {proof['unit']})
            intent['settled_ledgers'] = sorted(set(intent.get('settled_ledgers', [])) | {proof['ledger']})
        if len(intent.get('settled_units', [])) >= intent.get('qty', 1):
            intent['status'] = 'settled'
        elif len(units) >= intent.get('qty', 1) and old != 'settled':
            intent['status'] = 'observed_inventory'
        if old != intent['status'] or units!=previous_units or set(intent.get('settled_units',[]))!=previous_settled:
            changes.append({'sku': intent['sku'], 'unit': proof['unit'], 'old_status': old,
                            'status': intent['status'], 'source': proof['source'], 'ledger': proof.get('ledger'),
                            'outcome_link_status':'exact_intent_unit' if intent.get('decision_id') else 'legacy_or_unmatched',
                            **{key:intent[key] for key in (*LINKS,'intent_id') if intent.get(key) is not None}})
    return changes
