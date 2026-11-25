"""Counterfactual runtime effects under one fixed, observed inventory snapshot.

These are execution constraints/branch or prompt-guidance changes, not forecasts
of purchases, profits or causal Book growth. Cash reserves remain mandatory.
"""
from capital_policy import WINDOW_MAX_UNITS
from seller_meta_policy import DEFAULT,BOUNDS,FOCUS,validate

VERSION='meta-effects-v2'


def metrics(data,config):
    risk=data['risk'];state=data['inventory_state'];operating=risk['operating_policy']
    stock=risk['stock_acquisition_cost'];balance=risk['cash_balance']
    exposure_limit=int(max(0,balance+stock)*config['inventory_capital_share'])
    target=min(exposure_limit,int(max(0,balance+stock)*config['target_capital_share']))
    available=(max(0,min(balance-risk['cash_reserve'],exposure_limit-stock-risk['pending_cost_ceiling']))
               if all(k in risk for k in ('cash_reserve','pending_cost_ceiling')) else risk['available_to_invest'])
    budget=min(config['window_budget'],available)
    room=(WINDOW_MAX_UNITS if state.get('max_units') is None else
          max(0,state['max_units']-state['stock_units']-state['pending_units']))
    buying=operating['allow_replenishment'] and room>0 and budget>0
    exploration_budget=min(budget,max(0,config['rolling_exploration_budget']-state.get('rolling_exploration_reserved',0)))
    new=buying and operating['allow_new_skus'] and exploration_budget>0
    coverage=state['stocked_variety']<state['minimum_variety']
    allocation=stock<target
    probability=(1. if coverage or allocation else config['epsilon']) if new else 0.
    return {'effective_window_budget':budget,'unit_room':room,'target_stock_cost':target,
            'inventory_cost_limit':exposure_limit,'available_to_invest':available,
            'effective_exploration_budget':exploration_budget,
            'allocation_recovery':allocation,'coverage_recovery':coverage,
            'effective_exploration_probability':probability,
            'effective_focus':config['focus'] if operating['phase']=='growth' else 'replenishment',
            'transaction_model_reachable':buying,'new_sku_path_reachable':new}


def evaluate(data,before,after):
    validate(before);validate(after)
    a,b=metrics(data,before),metrics(data,after)
    delta=b['effective_window_budget']-a['effective_window_budget']
    probability=round(b['effective_exploration_probability']-a['effective_exploration_probability'],6)
    target_guidance=(a['target_stock_cost']!=b['target_stock_cost']
                     and (a['transaction_model_reachable'] or b['transaction_model_reachable']))
    focus_guidance=(a['effective_focus']!=b['effective_focus']
                    and (a['transaction_model_reachable'] or b['transaction_model_reachable']))
    changes=[]
    # An unavailable unit slot/ended round cannot be unlocked by a budget edit.
    if delta and (a['transaction_model_reachable'] or b['transaction_model_reachable']):changes.append('effective_budget')
    exploration_delta=b['effective_exploration_budget']-a['effective_exploration_budget']
    if exploration_delta and (a['new_sku_path_reachable'] or b['new_sku_path_reachable']):changes.append('effective_exploration_budget')
    if probability:changes.append('exploration_branch_probability')
    if target_guidance:changes.append('target_guidance_only')
    if focus_guidance:changes.append('focus_guidance_only')
    reasons=[]
    if before['window_budget']!=after['window_budget'] and not delta:
        reasons.append('cash_or_exposure_binds_before_window_budget')
    if before['target_capital_share']!=after['target_capital_share'] and a['target_stock_cost']==b['target_stock_cost']:
        reasons.append('target_clipped_by_inventory_exposure_share')
    if before['inventory_capital_share']!=after['inventory_capital_share'] and not delta and not target_guidance:
        reasons.append('cash_or_window_budget_binds_before_exposure_share')
    if before['rolling_exploration_budget']!=after['rolling_exploration_budget'] and not exploration_delta:
        reasons.append('cash_exposure_or_window_budget_binds_before_rolling_exploration')
    if before['epsilon']!=after['epsilon'] and not probability:
        reasons.append('exploration_forced_by_recovery' if a['new_sku_path_reachable'] and
                       (a['coverage_recovery'] or a['allocation_recovery']) else 'exploration_path_unavailable')
    if not a['transaction_model_reachable'] and not b['transaction_model_reachable']:
        reasons.append('no_investable_budget_or_unit_capacity_or_round_ended')
    if before['focus']!=after['focus'] and not focus_guidance:reasons.append('focus_path_unchanged_or_unavailable')
    if not changes and not reasons:reasons.append('no_execution_or_guidance_change')
    candidates=data.get('market_opportunities',[])
    def affordable(row,budget):
        non_budget=[reason for reason in row.get('blocked_by',[]) if reason not in
                    ('live_cash_or_total_exposure','current_window_budget')]
        return not non_budget and row['cost_ceiling_with_max_surge']<=budget
    unlocked=[r['sku'] for r in candidates if affordable(r,b['effective_window_budget'])
              and not affordable(r,a['effective_window_budget'])] if b['transaction_model_reachable'] else []
    return {'version':VERSION,'has_effect':bool(changes),'effect_types':changes,'no_effect_reasons':reasons,
            'before':a,'after':b,'effective_budget_delta':delta,
            'effective_exploration_budget_delta':exploration_delta,
            'exploration_probability_delta':probability,
            'displayed_candidates_unlocked':unlocked,
            'scope':'Fixed observed snapshot. Exposure share changes actual risk capacity; target share is prompt guidance. '
                    'Guidance-only changes do not increase spend capacity or guarantee different choices. '
                    'Candidate unlocks cover displayed ranked candidates, not total market demand.'}


def options(data):
    config={k:data['active_policy'][k] for k in DEFAULT};eligible=[];excluded=[]
    values={'window_budget':{int(config['window_budget']*factor) for factor in (.5,.75,1.25,2)},
            'rolling_exploration_budget':{int(config['rolling_exploration_budget']*factor) for factor in (.5,.75,1.25,2)},
            'inventory_capital_share':{round(config['inventory_capital_share']+step,6) for step in (-.10,-.05,.05,.10)},
            'target_capital_share':{round(config['target_capital_share']+step,6) for step in (-.10,-.05,.05,.10)},
            'epsilon':{round(config['epsilon']+step,6) for step in (-.05,.05)},'focus':set(FOCUS)}
    for key,choices in values.items():
        for value in sorted(choices):
            if value==config[key] or key in BOUNDS and not BOUNDS[key][0]<=value<=BOUNDS[key][1]:continue
            after=validate({**config,key:value});effect=evaluate(data,config,after)
            row={'parameter':key,'numeric_value':value if key!='focus' else None,
                 'focus_value':value if key=='focus' else None}
            if effect['has_effect']:eligible.append({**row,'effect':effect})
            else:excluded.append({**row,'no_effect_reasons':effect['no_effect_reasons']})
    return eligible,excluded
