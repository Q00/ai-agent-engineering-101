"""Persistent per-shop opening bids, with no model or private budget logging."""
import math
import time


def opening_ratio(audit):
    policy=audit.get('negotiation',{})
    return min(.95,max(.05,policy.get('paid_opening_ratio',.35)))


def offer_for(audit,ask,ceiling,turn,last):
    target=min(ceiling,audit.get('margin_target',ceiling*.85))
    learned=audit.get('negotiation',{}).get('paid_opening_ratio')
    opening=min(target,ask*learned) if learned else min(target*.85,ask*.35)
    ladder=[opening,target*.95,target,target+(ceiling-target)*.35,
            target+(ceiling-target)*.7,ceiling]
    offer=min(math.floor(ceiling),max(last or 0,math.floor(ladder[min(max(0,turn),len(ladder)-1)])))
    if last is not None and offer<=last<math.floor(ceiling):offer=min(math.floor(ceiling),last+1)
    return offer


def record(audit,outcome,initial_ask,last_offer,detail=''):
    policy=audit.setdefault('negotiation',{'opening_ratio':.05,'low_offer_rejections':0,'agreements':0})
    if outcome=='low_offer_rejected':
        policy['low_offer_rejections']=policy.get('low_offer_rejections',0)+1
        policy['firm_opening_ratio']=.05
    elif outcome=='receipt_validated':
        policy['agreements']=policy.get('agreements',0)+1
        # A receipt is an agreement, not proof of payment. Keep the learned
        # opening ratio rather than resetting to zero after a successful trade.
    history=policy.setdefault('history',[])
    history.append({'at':time.time(),'outcome':outcome,'initial_ask':initial_ask,
                    'last_offer':last_offer,'next_opening_ratio':opening_ratio(audit),'detail':detail})
    del history[:-20]
    policy['opening_ratio']=opening_ratio(audit)
    history[-1]['next_opening_ratio']=policy['opening_ratio']


def low_offer_rejection(state,text,initial_ask,last):
    if state!='TASK_STATE_REJECTED' or initial_ask is None or last is None or last>=initial_ask:
        return False
    # Do not attribute stock/expiry/terms failures to bargaining aggressiveness.
    return not any(word in text.lower() for word in ('out of stock','not in stock','unavailable','expired','certificate','재고','품절','만료','인증'))
