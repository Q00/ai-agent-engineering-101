"""Shadow-only warm-SKU reentry, preserving the existing refund stress.

No runtime imports this research module. Its output is not a trading instruction.
"""
import math


def eligible(row,now):
    numeric=('mature_margin','forecast_book_without_sku_gate','forecast_revenue_without_sku_gate',
        'capital','candidate_qty','positive_marginal_qty','external_samples','latest_external_observation')
    if any(type(row.get(k)) not in (int,float) or not math.isfinite(row[k]) for k in numeric):return False
    return (row.get('kind')=='goods' and type(row.get('mature_sales')) is int and 1<=row['mature_sales']<3
        and row['mature_margin']>0 and row.get('low_reviews',0)==0
        and row['external_samples']>=6
        and len({buyer for buyer,count in row.get('external_buyers',[]) if buyer and count>0})>=2
        and 0<=now-row['latest_external_observation']<=1800
        and type(row['positive_marginal_qty']) is int and 1<=row['positive_marginal_qty']<=20
        and row['candidate_qty']>=row['positive_marginal_qty']
        and row['forecast_book_without_sku_gate']>0 and row['forecast_revenue_without_sku_gate']>0
        and row['capital']>0)


def compare(audit):
    now=audit['at'];lookup={r['sku']:r for r in audit['candidates']}
    baseline=[{'sku':r['sku'],'qty':r['qty'],'book':r['book'],
        'capital':lookup[r['sku']]['capital']*r['qty']/lookup[r['sku']]['candidate_qty'],
        'source':'existing_v33'} for r in audit['baseline_buy']]
    used={r['sku'] for r in baseline}
    extra=[{'sku':r['sku'],'qty':r['positive_marginal_qty'],
        'book':r['forecast_book_without_sku_gate'],
        'revenue':r['forecast_revenue_without_sku_gate'],
        'capital':r['capital']*r['positive_marginal_qty']/r['candidate_qty'],
        'source':'mature_profitable_warm_reentry'} for r in audit['candidates']
        if r['sku'] not in used and eligible(r,now)]
    selected=[];spent=0
    for row in sorted(baseline+extra,key=lambda r:r['book'],reverse=True):
        if len(selected)>=10:break
        if spent+row['capital']>audit['financial_capacity']['spendable']:continue
        selected.append(row);spent+=row['capital']
    if sum(r['book'] for r in selected)<sum(r['book'] for r in baseline):
        selected=baseline;spent=sum(r['capital'] for r in baseline)
    return {'at':now,'execution_allowed':False,'baseline':baseline,'eligible_warm':extra,'proposed':selected,
        'baseline_book':sum(r['book'] for r in baseline),'proposed_book':sum(r['book'] for r in selected),
        'proposed_capital':spent,
        'limits':['Forecast comparison on a frozen snapshot, not observed profit or ALL improvement.',
            'Per-SKU maturity moves from three to at least one profitable mature sale only for the qualified warm cohort.',
            'Upper refund stress, published supply capital bound, cash reserve and legal line/quantity limits are retained.',
            'No admission for an entirely unsold SKU or known loss/low-review cohort.']}
