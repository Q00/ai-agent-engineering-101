"""Bounded inventory-age pricing and cross-SKU suggestions; no model calls."""
import math
from statistics import median


def age_terms(unit,now,expected_hours=1):
    age=max(0,(now-unit.get('supplied_at',now))/3600)
    threshold=max(2,min(4,expected_hours*2))
    steps=min(5,max(0,int(age/threshold)))
    if unit.get('kind')=='food' and unit.get('expires_at'):
        remaining=max(0,(unit['expires_at']-now)/3600)
        if remaining<max(6,expected_hours*2):steps=max(steps,3)
    return dict(age_hours=age,steps=steps,opening_factor=1-.03*steps,
                minimum_margin=max(.06,.12-.02*steps),market_floor=max(.82,.90-.02*steps))


def expected_turnover(connection,sku):
    rows=connection.execute('select t.settled_at-u.supplied from trials t join units u on t.unit=u.unit where t.sku=? and t.inquiry=0 and t.settled_at>u.supplied order by t.settled_at desc limit 20',(sku,)).fetchall()
    return max(.5,min(6,median(r[0]/3600 for r in rows))) if len(rows)>=3 else 1


def promotion_score(unit,ask,demand,now):
    age=max(0,(now-unit.get('supplied_at',now))/3600)
    probability=min(1,max(.2,demand/10))
    return max(0,ask-unit['cost'])*probability*(1+min(2,age/4))


def alternative(unit,inventory,promised,now):
    category=unit.get('category')
    if not category:return None
    choices=[u for u in inventory.get('units',[]) if u.get('sku')!=unit['sku'] and u.get('category')==category
             and u.get('unit') not in promised and u.get('grade')!='expired'
             and u.get('list_price',math.inf)<unit.get('list_price',0)
             and (u.get('expires_at') is None or u['expires_at']-now>=21600)]
    return min(choices,key=lambda u:u['list_price']) if choices else None
