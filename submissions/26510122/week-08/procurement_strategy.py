"""Feasible comparison baskets and an auditable model allocation review.

Examples are alternatives, never automatic purchase instructions. All targets
are existing tool options; accounting and fresh execution remain downstream.
"""
from collections import Counter


def comparison_baskets(winners,rows,*,anchor,minimum,minimum_categories,slots,units,budget,novel_budget,stocks,categories,pending):
    ordered=sorted(rows,key=lambda r:(r['sku']!=anchor,r['cost_ceiling'],r['sku']))
    witness=[];seen=set()
    for row in ordered:
        if len(witness)>=minimum and len(seen)>=minimum_categories:break
        if witness and row['category'] in seen and len(seen)<minimum_categories:continue
        if categories.get(row['category'],0)+sum(r['category']==row['category'] for r in witness)>=24:continue
        witness.append(row);seen.add(row['category'])
    if len(witness)<minimum or len(seen)<minimum_categories or sum(r['cost_ceiling'] for r in witness)>min(budget,novel_budget):return []
    ranked=sorted(winners,key=lambda r:(r['second_trial'], not r.get('midprice_priority'),
                                      -r.get('evidence', {}).get('midprice_contribution_12h', 0) if r.get('midprice_priority') else 0,
                                      -r['forecast']['sales_rate_per_hour'],r['sku']))
    results=[];fingerprints=set()
    for name,new_count,fraction,goods_h,food_h in (
        ('broad',8,.65,0.,0.),('balanced',4,.35,2.,1.),('depth',2,.15,6.,2.)):
        new=list(witness);ceiling=min(budget,novel_budget,max(sum(r['cost_ceiling'] for r in witness),int(budget*fraction)))
        for row in rows:
            if len(new)>=min(new_count,slots,8,units):break
            if row['sku'] in {r['sku'] for r in new}:continue
            if categories.get(row['category'],0)+sum(r['category']==row['category'] for r in new)>=24:continue
            if sum(r['cost_ceiling'] for r in new)+row['cost_ceiling']<=ceiling:new.append(row)
        if len(new)>slots or len(new)>units:continue
        spend=sum(r['cost_ceiling'] for r in new);remaining=budget-spend;free=units-len(new)
        cats=Counter(categories)
        for row in new:cats[row['category']]+=1
        picks=[];lines=[]
        for winner in ranked:
            if len(picks)>=min(8,slots-len(new)):break
            horizon=goods_h if winner['kind']=='goods' else food_h
            options=[o for o in winner['target_options'] if not horizon and o['scenario'] in ('low','base','high','second_trial','expiry_match')
                     or horizon and (o.get('coverage_hours',0)<=horizon)]
            options=sorted(options,key=lambda o:o['target_stock'],reverse=True)
            option=next((o for o in options if o['qty']<=free and o['cost_ceiling']<=remaining
                         and cats[winner['category']]+o['qty']<=24),None)
            if option is None:continue
            picks.append({'sku':winner['sku'],'target_stock':option['target_stock']})
            lines.append({'sku':winner['sku'],'qty':option['qty'],'kind':'replenishment',
                'target_stock':option['target_stock'],'coverage_hours':option.get('coverage_hours'),
                'cost_ceiling':winner['cost_ceiling_per_unit'],'second_trial':winner['second_trial']})
            remaining-=option['cost_ceiling'];free-=option['qty'];cats[winner['category']]+=option['qty']
        lines += [{'sku':r['sku'],'qty':1,'kind':'exploration','cost_ceiling':r['cost_ceiling']} for r in new]
        fingerprint=tuple(sorted((r['sku'],r['qty']) for r in lines))
        if not lines or fingerprint in fingerprints:continue
        fingerprints.add(fingerprint)
        additions=Counter({r['sku']:r['qty'] for r in lines});before=after=demand=0.
        for row in winners:
            if row['second_trial']:continue
            mean=row['forecast']['sales_rate_per_hour']*2;projected=stocks.get(row['sku'],0)+pending.get(row['sku'],0)
            demand+=mean;before+=min(projected,mean);after+=min(projected+additions[row['sku']],mean)
        results.append({'id':name,'skus':[r['sku'] for r in new],'replenishment':picks,'lines':lines,
            'cost_ceiling':budget-remaining,'unallocated_budget':remaining,'units':sum(r['qty'] for r in lines),
            'supply_lines':len(lines),'novel_lines':len(new),'replenishment_lines':len(picks),
            'forecast_units_2h':round(demand,3),'approx_additional_units_covered_2h':round(after-before,3),
            'approx_coverage_2h':round(after/demand,4) if demand else None,
            'caution':'Mean-demand coverage is an uncalibrated comparison over the SAME evidenced sellers and two-hour horizon. '
                      'It excludes novel demand, price/margin uncertainty, spoilage, holding and refunds; it is not a profit score.'})
    return results


def review_schema(alternatives,winners):
    return {'type':'object','additionalProperties':False,'properties':{
        'compared_ids':{'type':'array','items':{'type':'string','enum':[a['id'] for a in alternatives] or ['none']},'maxItems':3},
        'chosen_approach':{'type':'string','enum':[a['id'] for a in alternatives]+['custom','hold']},
        'tradeoff':{'type':'string'},
        'exceptions':{'type':'array','maxItems':12,'items':{'type':'object','additionalProperties':False,
            'properties':{'sku':{'type':'string','enum':[w['sku'] for w in winners] or ['none']},'reason':{'type':'string'}},
            'required':['sku','reason']}}},'required':['compared_ids','chosen_approach','tradeoff','exceptions']}


def required_exceptions(winners,picks):
    selected={r['sku']:r for r in picks};required=[]
    for row in winners:
        if row['second_trial']:continue
        choice=selected.get(row['sku']);largest=max(o['target_stock'] for o in row['target_options'])
        if choice is None or choice['target_stock']<largest:required.append(row['sku'])
    return required


def validate_review(review,alternatives,winners,skus,picks):
    if not isinstance(review,dict):raise ValueError('allocation_review_required: compare the feasible baskets before deciding')
    ids=[a['id'] for a in alternatives];compared=review.get('compared_ids')
    if (not isinstance(compared,list) or len(set(compared))<min(2,len(ids)) or any(i not in ids for i in compared)):
        raise ValueError('allocation_review_required: compare at least two distinct feasible basket IDs')
    if not isinstance(review.get('tradeoff'),str) or len(review['tradeoff'].strip())<8:
        raise ValueError('allocation_review_required: explain the spend/coverage/discovery tradeoff')
    approach=review.get('chosen_approach')
    if approach not in ids+['custom']:raise ValueError('allocation_review: invalid buy approach')
    if approach in ids:
        example=next(a for a in alternatives if a['id']==approach)
        if set(example['skus'])!=set(skus) or sorted((r['sku'],r['target_stock']) for r in example['replenishment'])!=sorted((r['sku'],r['target_stock']) for r in picks):
            raise ValueError('allocation_review: basket differs from the named example; use custom for a modified basket')
    exceptions=review.get('exceptions')
    if not isinstance(exceptions,list):raise ValueError('allocation_review: exceptions must be a list')
    reasons={r.get('sku'):r.get('reason') for r in exceptions if isinstance(r,dict)}
    missing=[sku for sku in required_exceptions(winners,picks) if not isinstance(reasons.get(sku),str) or len(reasons[sku].strip())<8]
    if missing:raise ValueError('allocation_review: give a concrete risk/opportunity cost for lower depth or omission of '+','.join(missing))
    return {'status':'reviewed','compared_ids':compared,'chosen_approach':approach,
            'tradeoff':review['tradeoff'],'exceptions':exceptions}
