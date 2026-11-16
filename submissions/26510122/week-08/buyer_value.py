"""Conditional price/cover stress bounds; never a private cost or failure forecast."""
import math
import re
from copy import deepcopy
from market_scoring import saved_at_price
from buyer_breakage import contribution_debit

VERSION='buyer-value-v1'
TARGET_FLOOR=.80
SCORE_CUSHION=.05
COVER_HOURS=6


def covered_evidence(evidence,hours):
    result=deepcopy(evidence or {})
    if type(hours) not in (int,float) or not math.isfinite(hours) or hours<COVER_HOURS:return result
    for key in ('same_shop_sku','same_product'):
        row=result.get(key) or {}
        # Credit cover only for verified historical failures observed within6h.
        # Unknown/later failures remain unprotected; refunds still lose the saving.
        eligible=min(max(0,row.get('broken',0)-row.get('refunded',0)),max(0,row.get('unrefunded_within_6h',0)))
        if row:row['refunded']=row.get('refunded',0)+eligible
    return result


def adjusted_saved(list_price,price,cost,evidence,hours=0):
    saving=saved_at_price(list_price,price,cost)
    if saving is None:return None
    return saving-contribution_debit(covered_evidence(evidence,hours),saving)


def terms(kind,list_price,cap,cost_reference,evidence,baseline=None):
    if kind!='goods' or not evidence:return {}
    local=evidence.get('same_shop_sku') or {};product=evidence.get('same_product') or {}
    if local.get('broken',0)<1 and product.get('broken',0)<2:return {}
    cost=(cost_reference or {}).get('unit_cost')
    if type(cost) not in (int,float) or not math.isfinite(cost) or not 0<=cost<list_price:return {}
    reference=(baseline or {}).get('saved')
    reference=reference if type(reference) in (int,float) and math.isfinite(reference) else None
    target=min(.95,max(TARGET_FLOOR,(reference+SCORE_CUSHION) if reference is not None else TARGET_FLOOR))
    def ceiling(hours):
        low,high,best=1,min(cap,list_price),None
        while low<=high:
            middle=(low+high)//2
            if adjusted_saved(list_price,middle,cost,evidence,hours)>=target:
                best=middle;low=middle+1
            else:high=middle-1
        return best
    plain,covered=ceiling(0),ceiling(COVER_HOURS)
    return {'version':VERSION,'target_saved':round(target,6),'max_price_without_warranty':plain,
            'max_price_with_6h_warranty':covered,'required_warranty_hours':COVER_HOURS if plain is None else 0,
            'reference_saved':reference,'operator_cushion':SCORE_CUSHION,
            'note':'Hard commercial stress bounds for this candidate, conditional on estimated private cost and censored own failure evidence. Target floor/cushion are operator choices, not official rules or guarantees. Six-hour cover changes only verified within6h unrefunded failures to refund outcomes; refunds still contribute zero.'}


def summary(value):
    return {k:value[k] for k in ('target_saved','max_price_without_warranty','max_price_with_6h_warranty') if k in value}


def quoted_cover(message):
    if re.search(r'\bno warranty\b',message,re.I):return 0
    rows=re.findall(r'\bwarranty\s*[:=]?\s*(\d+(?:\.\d+)?)\s*h|(?<![\d.])(\d+(?:\.\d+)?)\s*h(?:ours?)?\s+(?:market\s+)?warranty',message,re.I)
    values={float(a or b) for a,b in rows}
    return next(iter(values)) if len(values)==1 else None


def quote_cap(value,message,required=0):
    if not value:return None
    cover=quoted_cover(message)
    if required and cover is not None and cover<required:return 0
    protected=(cover is not None and cover>=COVER_HOURS) or required>=COVER_HOURS
    return value.get('max_price_with_6h_warranty' if protected else 'max_price_without_warranty') or 0


def receipt_terms(value,receipt,order):
    result=dict(order)
    if not value or not receipt:return result,None
    price=receipt.get('price');cover=receipt.get('warranty_hours',0)
    valid_cover=type(cover) in (int,float) and math.isfinite(cover) and cover>=COVER_HOURS
    limit=value.get('max_price_with_6h_warranty' if valid_cover else 'max_price_without_warranty')
    if type(price) is not int or type(limit) is not int or not 0<price<=limit:
        return result,'receipt violates risk-adjusted price/warranty terms'
    plain=value.get('max_price_without_warranty')
    if plain is None or price>plain:
        result['required_warranty_hours']=max(result.get('required_warranty_hours',0),COVER_HOURS)
    return result,None
