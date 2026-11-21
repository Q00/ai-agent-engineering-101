"""Trace observed low-star reviews to public settled orders and result events."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timezone
import json
import re
import httpx
from r3.state import ROOT,read_json


def main():
    initial=read_json(ROOT/'logs/review-detail-source-20261106T051229261673Z.json')
    shops=json.loads(initial['sources']['/api/shops']['text'])['shops']
    own=next(s for s in shops if s['name']=='seochanit')
    low=[r for r in own['reviews'] if r['stars']<=2]
    refs=[]
    for r in low:
        m=re.search(r'ledger\s*#(\d+)',r['review'],re.I)
        if m:refs.append(int(m[1]))
    def get(seq):
        r=httpx.get('https://market.ourolabs.ai/api/ledger',params={'after':seq-1},timeout=20,trust_env=False)
        return str(seq),{'status':r.status_code,'text':r.text}
    with ThreadPoolExecutor(max_workers=3) as pool:responses=dict(pool.map(get,refs))
    matches=[]
    for seq,response in responses.items():
        if response['status']!=200:continue
        rows=json.loads(response['text'])
        settled=next((e for e in rows if e['seq']==int(seq)),{})
        order=settled.get('data',{}).get('order')
        results=[e for e in rows if e.get('kind')=='result' and e.get('data',{}).get('order')==order]
        matches.append({'review':next(r for r in low if seq in r['review']),
                        'settle':settled,'results':results})
    result={'at':datetime.now(timezone.utc).isoformat(),'review_source':'logs/review-detail-source-20261106T051229261673Z.json',
            'responses':responses,'matches':matches}
    p=ROOT/'logs'/('negative-review-trace-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'.json')
    p.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print('SOURCE',p);print(json.dumps(matches,ensure_ascii=True))


if __name__=='__main__':main()
