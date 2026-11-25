"""Derive current score components and growth rates from saved public snapshots."""
from pathlib import Path
import json, hashlib
P=Path(__file__).resolve().parent
read=lambda p:json.loads(p.read_text())
b=read(P/'board.json');s=read(P/'summary.json')
prior=read(P.parent/'strategy-followup-20261109T171238Z/summary.json')
out={'growth_rates':{},'buyer_components':{},'top3':{}}
for role in ('shops','buyers'):
    old=prior['comparison'][role]['additional_deals_per_hour']
    new=s['comparison'][role]['additional_deals_per_hour']
    out['growth_rates'][role]={'previous_deals_per_hour':old,'current_deals_per_hour':new,'rate_change_percent':100*(new/old-1)}
    ranked=sorted([r for r in b[role] if 'all' in r],key=lambda r:r['all'],reverse=True)
    out['top3'][role]=[{k:r[k] for k in (('name','all','book','revenue','deals') if role=='shops' else ('name','all','saved','deals','posts','forum'))} for r in ranked[:3]]
    if role=='buyers':
        for r in ranked[:3]+[r for r in ranked if r['name']=='dyishappy']:
            points={k:w*(sum(o[k]<r[k] for o in ranked)+(sum(o[k]==r[k] for o in ranked)-1)/2)/(len(ranked)-1) for k,w in {'saved':50,'deals':10,'posts':10}.items()}
            points['forum']=30*(r.get('forum') or 0)
            out['buyer_components'][r['name']]=points
old=prior['comparison']['shops']['book_growth_per_hour'];new=s['comparison']['shops']['book_growth_per_hour']
out['growth_rates']['book']={'previous_per_hour':old,'current_per_hour':new,'rate_change_percent':100*(new/old-1)}
out['interpretation']=[
'Both roles have recent confirmed public settlements. Online status alone was not used as proof.',
'Shop inventory remains diversified (20 SKUs,25 units), not empty; B six-hour trial not observed in this short sample.',
'Current shop grade mix differs from earlier sample, so its lower median sale/list ratio is not proof of changed bargaining behavior.',
'Buyer Saved increased, but remains below top-three buyers. Forum raw rating is already strong.',
'Latest 36.7-minute shop sample has 5 sales versus interval mean 16.1/hour: monitor persistence, not sufficient to infer sustained slowdown.',
'No runtime changes, trades, forum messages, model calls, or private server access.']
(P/'interpretation.json').write_text(json.dumps(out,ensure_ascii=False,indent=2)+'\n')
files={f.name:hashlib.sha256(f.read_bytes()).hexdigest() for f in sorted(P.iterdir()) if f.is_file() and f.name!='sha256.json'}
(P/'sha256.json').write_text(json.dumps(files,indent=2)+'\n')
print(json.dumps(out,ensure_ascii=False,indent=2))
