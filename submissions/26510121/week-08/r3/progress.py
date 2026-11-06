"""Bounded official-board history for continuous leader-gap diagnosis."""


def observe(journal,board,inv,capacity,now):
    current={'at':now}
    for role,table,fields in [('shop','shops',['book','revenue','all','stock_cost','stock_units']),
                              ('buyer','buyers',['saved','forum','deals','posts','all'])]:
        rows=[r for r in board.get(table,[]) if type(r.get('all')) in (int,float)]
        own=next((r for r in rows if r.get('name')=='seochanit'),{})
        current[role]={'own':{k:own.get(k) for k in fields},
                       'peers':{r['name']:{k:r.get(k) for k in fields} for r in rows if r['name']!='seochanit'},
                       'rank':1+sum(r['all']>own.get('all',0) for r in rows),'population':len(rows)}
    history=[r for r in journal.get('championship_board_history',[]) if 0<=now-r['at']<=21600]
    baseline=next((r for r in history if now-r['at']>=3600),None)
    rates={}
    if baseline:
        hours=(now-baseline['at'])/3600
        for role,field in [('shop','book'),('buyer','saved')]:
            own=current[role]['own'].get(field);old=baseline[role]['own'].get(field)
            if type(own) not in (int,float) or type(old) not in (int,float):continue
            peers=[]
            for name,row in current[role]['peers'].items():
                prior=baseline[role]['peers'].get(name,{})
                if type(row.get(field)) in (int,float) and type(prior.get(field)) in (int,float):
                    peers.append({'name':name,'per_hour':(row[field]-prior[field])/hours})
            rates[role]={'metric':field,'hours':hours,'own_per_hour':(own-old)/hours,
                         'fastest_matched':sorted(peers,key=lambda r:r['per_hour'],reverse=True)[:3],
                         'note':'observational pace, not a forecast or causal estimate'}
    if not history or now-history[-1]['at']>=240:history.append(current)
    journal.put('championship_board_history',history[-90:])
    from .meta import rank_targets
    result={'at':now,'ranks':{role:{k:current[role][k] for k in ['rank','population']} for role in ['shop','buyer']},
            'next_thresholds':{role:rank_targets(board,role) for role in ['shop','buyer']},'pace':rates,
            'idle_spendable_fraction':capacity['spendable']/max(1,inv.get('balance',0)),
            'stock_cost':sum(u.get('cost',0) for u in inv.get('units',[])),
            'scope':'everyone; student ranks remain separate in official board'}
    journal.put('championship_progress',result)
    return result
