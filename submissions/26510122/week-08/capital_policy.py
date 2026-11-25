"""Working-capital policy for Book-based, percentile-ranked Round 3 scoring."""
from datetime import datetime
from seller_meta_policy import active
from market_scoring import SELLER_WEIGHTS,BOOK_CONTRACT,VERSION

ROUND_END = datetime.fromisoformat('2026-11-17T23:59:00+09:00').timestamp()
MIN_CASH = 600_000
# Invalid persisted state must fail closed even after capital grows. This is
# an error reservation sentinel, never a ceiling on normal inventory exposure.
INVALID_STATE_EXPOSURE = 10**15
MAX_UNIT_COST = INVALID_STATE_EXPOSURE
WINDOW_MAX_UNITS = 10*20  # official kit's 10 lines, market's 20 units per call
GROWTH_WINDOW_BUDGET = 24_000_000
GROWTH_EXPLORATION_BUDGET = 32_000_000
RANKING = SELLER_WEIGHTS


def operating_policy(now):
    hours = max(0.,(ROUND_END-now)/3600)
    phase = 'ended' if hours<=0 else 'closing' if hours<=2 else 'final_day' if hours<=24 else 'harvest' if hours<=72 else 'growth'
    share,target,window,rolling,new = ((.80,.65,GROWTH_WINDOW_BUDGET,GROWTH_EXPLORATION_BUDGET,True)
                                     if hours>0 else (0.,0.,0,0,False))
    meta=active(now)
    if hours>0:
        share=meta['inventory_capital_share'];target=min(share,meta['target_capital_share'])
        window=meta['window_budget'];rolling=meta['rolling_exploration_budget']
    return {'exploration_epsilon':meta['epsilon'] if hours>0 else .30,
            'allocation_focus':meta['focus'] if phase=='growth' else 'replenishment',
            'meta_version':meta['meta_version'],'meta_status':meta['meta_status'],
            'phase':phase,'hours_to_end':round(hours,4),'ends_at':ROUND_END,
            'inventory_capital_share':share,'target_capital_share':target,
            'window_budget':window,'rolling_exploration_budget':rolling,
            'total_unit_limit':None,'window_max_units':WINDOW_MAX_UNITS,
            'quantity_policy':'No global stock-count ceiling. Per-SKU demand targets, pending stock, category limits, cash and exposure govern quantity.',
            'allow_new_skus':new,'allow_replenishment':hours>0,
            'score_weights':RANKING,'score_unit':'each component is a market percentile, not a raw weighted sum',
            'score_version':VERSION,'book_definition':BOOK_CONTRACT,
            'terminal_policy':'Retained stock counts at acquisition cost; compare Book after spoilage, holding and refunds. No forced cash liquidation.'}


def target_inventory_cost(inventory, now):
    stock = sum(max(0,int(u.get('cost') or 0)) for u in inventory.get('units',[])
                if u.get('grade') in ('A','B','C') and (u.get('expires_at') is None or u['expires_at']>now))
    equity_at_cost = max(0,int(inventory.get('balance') or 0))+stock
    return int(equity_at_cost*operating_policy(now)['target_capital_share'])
