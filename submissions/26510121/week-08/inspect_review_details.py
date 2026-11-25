"""Read public documented data and OpenAPI metadata; no votes or messages."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timezone
import json
import httpx
from r3.state import ROOT


def main():
    def get(path):
        try:
            r=httpx.get('https://market.ourolabs.ai'+path,timeout=20,trust_env=False)
            return path,{'status':r.status_code,'text':r.text}
        except Exception as exc:return path,{'error':type(exc).__name__}
    with ThreadPoolExecutor(max_workers=3) as pool:
        sources=dict(pool.map(get,['/openapi.json','/api/shops','/api/forum?limit=1000']))
    p=ROOT/'logs'/('review-detail-source-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'.json')
    p.write_text(json.dumps({'at':datetime.now(timezone.utc).isoformat(),'sources':sources},ensure_ascii=False,indent=2),encoding='utf-8')
    print('SOURCE',p)
    for path,response in sources.items():
        print('STATUS',path,response.get('status',response.get('error')))
        if response.get('status')!=200:continue
        try:data=json.loads(response['text'])
        except ValueError:continue
        if path=='/openapi.json':
            print('RELEVANT_API',json.dumps({k:v for k,v in data.get('paths',{}).items()
                if any(x in k for x in ['forum','review','vote','stance'])},ensure_ascii=True)[:18000])
        elif path=='/api/shops':
            own=[s for s in data.get('shops',[]) if s.get('name')=='seochanit']
            print('OWN_SHOP_REVIEWS',json.dumps([{k:s.get(k) for k in ['name','reviews','url']} for s in own],ensure_ascii=True)[:12000])
        else:
            from collections import Counter
            print('FORUM_COUNT',len(data));print('DISLIKE_COUNTS',dict(Counter(r['dislikes'] for r in data)))
            print('OWN_FORUM',json.dumps([{k:r.get(k) for k in ['id','ts','role','likes','dislikes','score','text']}
                                        for r in data if r.get('owner')=='seochanit'],ensure_ascii=True)[:16000])


if __name__=='__main__':main()
