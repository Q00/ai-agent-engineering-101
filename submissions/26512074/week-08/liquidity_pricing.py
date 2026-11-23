"""Age-aware, cost-positive discounts; terms bind once per new negotiation."""
import math
from pathlib import Path
from optimizer import db,read

def apply(md,task,unit,inventory,plan,hours,initial,now):
    md=Path(md);policy=read(md/'liquidity-policy.json',{})
    if not policy.get('enabled') or unit.get('kind')!='goods':return plan
    from extreme_quotes import planned_arm
    if planned_arm(md,task)!='baseline':return plan
    with db(md) as c:
        c.execute('''create table if not exists liquidity_terms(
          task text primary key,at real,ask integer,floor integer,concession real,
          age_hours real,target_list_ratio real,warranty_hours real)''')
        old=c.execute('select * from liquidity_terms where task=?',(task,)).fetchone()
        if old:
            c.execute("update trials set strategy='liquidity-v1' where task=?",(task,))
            return dict(plan,ask=old['ask'],floor=old['floor'],liquidity=dict(old))
        if not initial:return plan
        age=max(0,(now-unit.get('supplied_at',now))/3600)
        history=c.execute('''select count(*) n,sum(settled_at is not null) wins from trials
            where sku=? and inquiry=0 and at between ? and ?''',(unit['sku'],now-21600,now-300)).fetchone()
        slow=history['n']>=12 and (history['wins'] or 0)/history['n']<.15
        high=unit.get('list_price',0)>=200000
        if not (age>=6 or high and (age>=2 or slow)):return plan
        if age>=12:ratio,margin,concession=.65,.02,.50
        elif age>=6:ratio,margin,concession=.70,.03,.45
        else:ratio,margin,concession=.75,.04,.40
        modern=read(md/'operating-policy.json',{}).get('expected_profit_pricing',False)
        storage=unit['cost']*inventory.get('storage_per_day',.01)*(1/24 if modern else age/24)
        floor=math.ceil(unit['cost']*(1+margin)+storage)
        if hours:
            # Retain the existing funded warranty risk floor. Fee/menu and
            # issued warranties are not altered by a stock-turnover discount.
            floor=max(floor,plan['floor'])
        target=unit['list_price']*ratio
        if plan.get('market_median') and not modern:target=min(target,plan['market_median']*.98)
        ask=max(floor,min(int(plan['ask']),math.ceil(target)))
        c.execute('insert into liquidity_terms values(?,?,?,?,?,?,?,?)',
                  (task,now,ask,floor,concession,age,ratio,hours))
        row=dict(c.execute('select * from liquidity_terms where task=?',(task,)).fetchone())
        c.execute("update trials set strategy='liquidity-v1' where task=?",(task,))
    return dict(plan,ask=ask,floor=floor,liquidity=row)
