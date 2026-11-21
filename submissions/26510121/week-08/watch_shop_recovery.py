"""Bounded public verification: require three consecutive discovery/board passes."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timezone
import json,time,argparse
import httpx
from r3.state import ROOT,read_json


def main(url=None):
    url=(url or read_json(ROOT/'.market/shop-health.json')['url']).rstrip('/')
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    path=ROOT/'logs'/f'shop-recovery-watch-{stamp}.jsonl'
    def get(path):
        try:
            r=httpx.get(path,timeout=8,trust_env=False,headers={'Cache-Control':'no-cache'})
            return {'status':r.status_code,'data':r.json() if r.status_code==200 else {}}
        except Exception as exc:return {'error':type(exc).__name__,'data':{}}
    consecutive=0
    print('SOURCE',path,flush=True)
    with path.open('x',encoding='utf-8') as out:
        for i in range(12):
            with ThreadPoolExecutor(max_workers=4) as pool:
                rows=list(pool.map(get,[url+'/.well-known/agent-card.json',url+'/.well-known/acp.json',
                    'https://market.ourolabs.ai/api/shops','https://market.ourolabs.ai/api/board']))
            shops=rows[2]['data'].get('shops',[]);board=rows[3]['data']
            shop=next((r for r in board.get('shops',[]) if r.get('name')=='seochanit'),{})
            buyer=next((r for r in board.get('buyers',[]) if r.get('name')=='seochanit'),{})
            listed=any(r.get('name')=='seochanit' and r.get('url','').rstrip('/')==url for r in shops)
            board_url=(shop.get('card') or {}).get('url','').rstrip('/')
            good=all(r.get('status')==200 for r in rows) and listed and shop.get('alive') is True and board_url==url
            consecutive=consecutive+1 if good else 0
            row={'at':datetime.now(timezone.utc).isoformat(),'url':url,'card':rows[0].get('status'),
                 'acp':rows[1].get('status'),'listed':listed,'shop_alive':shop.get('alive'),
                 'buyer_alive':buyer.get('alive'),'board_url':board_url,'shop_deals':shop.get('deals'),'active_shops':len(shops),
                 'consecutive':consecutive,'control':read_json(ROOT/'.market/shop-health.json').get('status')}
            out.write(json.dumps(row)+'\n');out.flush();print(json.dumps(row),flush=True)
            if consecutive>=3:return True
            if i<11:time.sleep(15)
    return False


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--url')
    raise SystemExit(0 if main(parser.parse_args().url) else 1)
