"""The existing sequential supply guards, with a reason for every lost unit.

Emitting a buy request is not confirmation that inventory arrived. This pure
calculation neither reserves funds nor changes the approval/priority policy.
"""
from collections import Counter
from replenishment_plan import MAX_CATEGORY_STOCK,product_signature


def blocked(desired, reason):
    return [{'sku':r['sku'],'kind':r['kind'],'approved_qty':r['qty'],
             'emitted_qty':0,'cut_qty':r['qty'],'reasons':[reason],'checks':{}}
            for r in desired]


def select_lines(desired, *, stocks, categories, pending_stock, catalog,
                 unit_room, supply_lines, budget, allow_replenishment,
                 allow_new_skus, time_limited, exploration_budget=None, covered_expiry_trials=(),
                 unprofitable_upper_midprice=(), unprofitable_service_recovery=()):
    stocks=Counter(stocks);categories=Counter(categories)
    result=[];emitted=[]
    for index,row in enumerate(desired):
        sku=row['sku'];requested=row['qty'];category=row['category']
        category_room=max(0,MAX_CATEGORY_STOCK-categories[category])
        deficit=(row['target_stock']-stocks[sku]-pending_stock.get(sku,0)
                 if row['kind']=='replenishment' else 1)
        checks={'execution_index':index,'budget_before':budget,'unit_room_before':unit_room,
                'supply_lines_remaining':max(0,supply_lines-len(emitted)),
                'stock_before':stocks[sku],'pending_stock':pending_stock.get(sku,0),
                'category_room':category_room,'target_deficit':deficit,
                'cost_ceiling_per_unit':row['cost_ceiling']}
        reasons=[];qty=0
        if not allow_replenishment:reasons=['replenishment_disabled']
        elif (row['kind']=='exploration' or row.get('second_trial')) and not allow_new_skus:
            reasons=['new_trials_disabled']
        elif len(emitted)>=supply_lines:
            reasons=['supply_window_time' if time_limited else 'supply_line_limit']
        elif row.get('expiry_trial') and sku in covered_expiry_trials:reasons=['expiry_demand_already_covered']
        elif sku in unprofitable_upper_midprice:reasons=['upper_midprice_margin_no_longer_positive']
        elif sku in unprofitable_service_recovery:reasons=['service_recovery_economics_changed']
        elif row['kind']=='exploration' and stocks[sku]:reasons=['already_stocked']
        elif row['kind']=='exploration' and product_signature(catalog[sku])!=row['signature']:
            reasons=['catalog_signature_changed']
        elif not category_room:reasons=['category_capacity']
        else:
            qty=min(requested,unit_room,category_room,deficit)
            if qty<requested:
                if unit_room==qty:reasons.append('unit_capacity')
                if category_room==qty:reasons.append('category_capacity')
                if deficit==qty:reasons.append('target_already_satisfied')
            if qty<=0:qty=0
            elif row['cost_ceiling']*qty>budget:
                checks['quantity_caps_before_budget']=list(reasons)
                reasons=['shared_budget'];qty=0
            elif row['kind']=='exploration' and exploration_budget is not None and row['cost_ceiling']*qty>exploration_budget:
                reasons=['rolling_exploration_budget'];qty=0
            else:
                budget-=row['cost_ceiling']*qty;unit_room-=qty
                if row['kind']=='exploration' and exploration_budget is not None:exploration_budget-=row['cost_ceiling']*qty
                categories[category]+=qty;stocks[sku]+=qty
                emitted.append({'sku':sku,'qty':qty})
        result.append({'sku':sku,'kind':row['kind'],'approved_qty':requested,
                       'emitted_qty':qty,'cut_qty':requested-qty,
                       'reasons':reasons,'checks':checks})
    return emitted,result
