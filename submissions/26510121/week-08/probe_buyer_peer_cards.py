"""Read public cards of recently failed buyer routes; no negotiation or payment."""
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timezone
import json
import sqlite3
import time
import httpx
from r3.state import ROOT


def main():
    with sqlite3.connect((ROOT/'.market/r3.sqlite3').as_uri()+'?mode=ro',uri=True) as c:
        rows=[json.loads(d) for d, in c.execute('SELECT data FROM events WHERE kind=? AND ts>?',
                                               ('attempt',time.time()-7200))]
    urls=[u for u,n in Counter(r['shop'] for r in rows if r.get('shop') and
                                r.get('kind') in ['rfq_failed','ValueError','ReadTimeout']).most_common(4)]
    def get(url):
        try:
            r=httpx.get(url.rstrip('/')+'/.well-known/agent-card.json',timeout=8,trust_env=False)
            return url,{'status':r.status_code,'text':r.text}
        except Exception as exc:return url,{'error':type(exc).__name__}
    with ThreadPoolExecutor(max_workers=4) as pool:responses=dict(pool.map(get,urls))
    path=ROOT/'logs'/('buyer-peer-cards-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'.json')
    path.write_text(json.dumps(responses,ensure_ascii=False,indent=2),encoding='utf-8')
    print('SOURCE',path)
    for url,row in responses.items():
        d=json.loads(row['text']) if row.get('status')==200 else {}
        print(url,row.get('status',row.get('error')),json.dumps({k:d.get(k) for k in
            ['name','protocolVersion','supportedInterfaces','url','skills']},ensure_ascii=True)[:2800])


if __name__=='__main__':main()
