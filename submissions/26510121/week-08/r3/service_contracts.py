"""Small explicit customer requirements; no inferred identity or executable prose."""
import math
import re
import time
from .state import identity


def parse(text, previous=None):
    old=previous or {}
    result={'minimum_hours':old.get('minimum_hours',0.),'grades':old.get('grades'),
        'warranty_hours':old.get('warranty_hours',0.),'explicit_use':old.get('explicit_use',False),
        'explicit_grade':old.get('explicit_grade',False),'explicit_warranty':old.get('explicit_warranty',False),
        'sku':old.get('sku'),'valid':old.get('valid',True),'reason':old.get('reason')}
    text=text[:2000]
    num=r'(-?\d+(?:\.\d+)?)'
    unit=r'(hours?|hrs?|h|days?|시간|일)\b'
    patterns=[r'(?:last\s+at\s+least|must\s+last\s+(?:for\s+)?|use\s+within|minimum\s+shelf\s+life(?:\s+after\s+checkout)?\s*:?)[ ]*'+num+r'\s*'+unit,
        r'(?:need|require)\s+(?:at\s+least\s+)?'+num+r'\s*'+unit+r'\s+(?:of\s+)?(?:shelf\s+life|remaining)',
        r'(?:유통기한|잔여\s*수명)\s*(?:최소|:|=)?\s*'+num+r'\s*(시간|일)']
    hours=[]
    for pattern in patterns:
        for value,u in re.findall(pattern,text,re.I):
            hours.append(float(value)*(24 if u.lower() in ('day','days','일') else 1))
    if hours:
        if any(h<0 for h in hours):result.update(valid=False,reason='negative_requirement')
        result['minimum_hours']=max(result['minimum_hours'],*hours);result['explicit_use']=True
    minimum=re.findall(r'\b(?:at least|minimum)\s+(?:certified\s+)?grade\s+([ABC])\b',text,re.I)
    exact=re.findall(r'\b(?:require|need)\s+(?:certified\s+)?grade\s+([ABC])\b|\bonly\s+(?:certified\s+)?(?:grade\s+)?([ABC])\b|\bgrade\s+([ABC])\s+(?:only|required)\b',text,re.I)
    restrictions=[set('ABC'[:'ABC'.index(g.upper())+1]) for g in minimum]
    if exact:restrictions.append({g.upper() for pair in exact for g in pair if g})
    if restrictions:
        allowed=set(result['grades'] or 'ABC')
        for group in restrictions:allowed&=group
        result['grades']=sorted(allowed);result['explicit_grade']=True
        if not allowed:result.update(valid=False,reason='conflicting_grade_requirements')
    warranty=re.findall(r'(?:minimum|at least)\s*'+num+r'\s*(?:hours?|hrs?|h)\s*(?:of\s*)?warranty|warranty\s*(?:of\s*)?(?:at least|minimum)\s*'+num,text,re.I)
    if warranty:
        result['warranty_hours']=max(result['warranty_hours'],*(float(v) for pair in warranty for v in pair if v))
        result['explicit_warranty']=True
    if not all(math.isfinite(result[k]) and result[k]>=0 for k in ('minimum_hours','warranty_hours')):
        result.update(valid=False,reason='nonfinite_requirement')
    if result['warranty_hours']>72:result.update(valid=False,reason='unsupported_warranty')
    tokens=re.findall(r'\bG[0-9a-f]{3,}\b',text,re.I)
    skus={s.casefold() for s in tokens}
    if not result['sku'] and len(skus)==1:result['sku']=tokens[0]
    if result['sku'] and skus and result['sku'].casefold() not in skus:
        result.update(valid=False,reason='different_sku_requires_new_task')
    return result


def eligible(unit,catalog,spec,now=None,buffer_seconds=30):
    from .economy import grade,remaining
    now=time.time() if now is None else now
    if not spec.get('valid',True) or remaining(unit,now)<=0:return False
    if spec.get('sku') and unit['sku'].casefold()!=spec['sku'].casefold():return False
    if spec.get('grades') is not None and grade(unit,catalog,now) not in spec['grades']:return False
    return unit.get('kind')!='food' or remaining(unit,now)>=spec['minimum_hours']+buffer_seconds/3600


def record(journal,task,spec,unit=None,now=None):
    from .economy import remaining,grade
    now=time.time() if now is None else now
    catalog=journal.snapshot('catalog.json',{})
    row={'task':task,'sku':(unit or {}).get('sku') or spec.get('sku'),
        'minimum_hours':spec['minimum_hours'],'grades':spec['grades'],'warranty_hours':spec['warranty_hours'],
        'explicit_use':spec['explicit_use'],'explicit_grade':spec['explicit_grade'],
        'explicit_warranty':spec['explicit_warranty'],'valid':spec['valid'],'reason':spec.get('reason'),
        'selected_unit':(unit or {}).get('unit'),'grade':grade(unit,catalog,now) if unit else None,
        'remaining_hours':remaining(unit,now) if unit and unit.get('kind')=='food' else None,
        'source':'explicit_customer_text_only','schema':1}
    journal.add('shop_service_need',row,eid='shop-need:'+identity({k:v for k,v in row.items() if k not in ('remaining_hours','grade')}),role='shop',ts=now)
    return row


def priority(unit,choices,catalog,spec,profiles,now=None):
    """Protect scarce declared service regions; unknown preferences retain quality-first."""
    from .economy import remaining,grade
    now=time.time() if now is None else now
    quality={'A':0,'B':1,'C':2}.get(grade(unit,catalog,now),3);regret=0.
    if spec.get('explicit_use'):
        for bucket in profiles:
            if bucket['minimum_hours']<=spec['minimum_hours']:continue
            future=dict(bucket,valid=True,sku=unit['sku'])
            if eligible(unit,catalog,future,now):
                count=sum(eligible(u,catalog,future,now) for u in choices)
                regret+=bucket['weight']/max(1,count)
    # Cross-grade conservation needs an explicit permitted grade set. With no
    # declared grade preference, do not infer that lower quality is acceptable.
    if spec.get('explicit_grade'):return (regret,quality,remaining(unit,now),unit.get('supplied_at',0))
    return (quality,regret,remaining(unit,now),unit.get('supplied_at',0))
