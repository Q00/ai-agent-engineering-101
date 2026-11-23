"""Read only explicit buyer conditions. Unknown prose stays with the negotiating model."""
import json
import math
import re

def parse_requirements(text):
    result={}
    # Recognize the kit buyer's labelled JSON acceptance policy, not arbitrary JSON in product names.
    m=re.search(r'Required checkout policy\s*:\s*(\{[^{}]*\})',text,re.I)
    if m:
        try:
            data=json.loads(m.group(1))
            if isinstance(data,dict):
                grades=data.get('grades')
                if isinstance(grades,list) and grades and all(g in ('A','B','C') for g in grades):result['grades']=grades
                for key in ('min_hours_left','warranty_hours'):
                    v=data.get(key)
                    if type(v) in (int,float) and math.isfinite(v) and v>=0:result[key]=v
        except ValueError:pass
    m=re.search(r'\bneed\s*:\s*shelf life\s+(\d+(?:\.\d+)?)\s*h\s+or more',text,re.I)
    explicit_shelf_life = re.finditer(
        r'\b(?:need|require|must have)\s+at least\s+(\d+(?:\.\d+)?)\s+hours?\s+of\s+(?:remaining\s+)?shelf life\b',
        text, re.I)
    explicit_lasting = re.finditer(
        r'\b(?:need|require)\s+(?:it|the (?:item|product|unit))\s+to\s+last\s+'
        r'(?:at least\s+)?(\d+(?:\.\d+)?)\s*hours?\b', text, re.I)
    for match in ([m] if m else []) + list(explicit_shelf_life) + list(explicit_lasting):
        hours = float(match.group(1))
        if math.isfinite(hours):
            result['min_hours_left'] = max(result.get('min_hours_left', 0), hours)
    return result

def condition_problem(unit,requirements):
    if not unit:return 'Stock is unavailable'
    if 'grades' in requirements and unit.get('grade') not in requirements['grades']:
        return 'Actual grade does not meet buyer policy'
    hours=requirements.get('min_hours_left',0)
    if hours and (unit.get('hours_left') is None or unit['hours_left']<hours):
        return 'Remaining shelf life does not meet buyer policy'
    if requirements.get('warranty_hours',0)>6:
        return 'Requested warranty exceeds shop maximum of 6 hours'
    if unit.get('kind')=='food' and requirements.get('warranty_hours',0)>0:
        return 'This shop does not offer a breakage warranty for food'
    return None

def merge_requirements(old,new):
    result=dict(old)
    if 'grades' in new:
        result['grades']=[g for g in new['grades'] if g in old.get('grades',['A','B','C'])]
    for key in ('min_hours_left','warranty_hours'):
        if key in new:result[key]=max(old.get(key,0),new[key])
    return result
