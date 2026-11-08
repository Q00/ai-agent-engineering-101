"""Read-only official input monitor. Forum text is never executed or forwarded."""
from __future__ import annotations

import argparse
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import time

import httpx

from adaptive_control import Control
from rank_controller import RankController


LIMIT = 2 * 1024 * 1024
SOURCES = {
    'rules': ('/rules', 600, 'text'),
    'agent': ('/agent.md', 600, 'text'),
    'board': ('/api/board', 90, 'json'),
    'shops': ('/api/shops', 90, 'json'),
}


class _Text(HTMLParser):
    """Turn official HTML into text; the result is never executed or prompted raw."""
    def __init__(self):
        super().__init__();self.parts=[]
    def handle_data(self,data):
        if data.strip():self.parts.append(data.strip())


def official_facts(name, raw):
    """Extract an allowlisted, bounded rule summary without forwarding page prose."""
    digest=hashlib.sha256(raw).hexdigest()
    if name!='rules':return {'sha256':digest,'bytes':len(raw)}
    parser=_Text();parser.feed(raw.decode('utf-8','replace'))
    text=' '.join(parser.parts);facts={'sha256':digest,'bytes':len(raw)}
    latest=re.search(r'Patch\s+([0-9]+(?:\.[0-9]+)?)\s+([A-Z][a-z]{2}\s+\d{1,2},\s+\d{4})',text)
    round_match=re.search(r'now playing Round\s+(\d+)',text,re.I)
    if latest:facts['latest_patch']={'version':latest.group(1),'announced':latest.group(2)}
    if round_match:facts['round']=int(round_match.group(1))
    formulas={}
    for label in ('Shop ALL','Buyer ALL'):
        match=re.search(re.escape(label)+r'\s*=\s*([^\.]{1,240})\.',text,re.I)
        if match:
            # Only metric names, numbers, spaces and arithmetic punctuation survive.
            value=re.sub(r'[^A-Za-z0-9 .+*%_-]','',match.group(1)).strip()
            formulas[label.lower().replace(' ','_')]=value[:240]
    if formulas:facts['score_formulas']=formulas
    facts['retroactive_round3']='Applies to all of Round 3' in text
    return facts


class Monitor:
    def __init__(self,state='.market',market='https://market.ourolabs.ai',client=None):
        self.state=Path(state);self.market=market.rstrip('/');self.control=Control(self.state)
        self.client=client or httpx.Client(timeout=httpx.Timeout(15,connect=5),follow_redirects=False)
        self.owns_client=client is None

    @staticmethod
    def _bounded(response):
        raw=response.content
        if len(raw)>LIMIT:raise ValueError('response_too_large')
        return raw

    def fetch(self,name):
        path,max_age,kind=SOURCES[name]
        response=self.client.get(self.market+path,headers={'Accept':'application/json,text/plain'})
        response.raise_for_status();raw=self._bounded(response)
        if kind=='json':
            body=response.json()
            if not isinstance(body,(dict,list)):raise ValueError('schema')
            if name=='board' and (not isinstance(body,dict) or 'round' not in body):raise ValueError('board_schema')
        else:
            if not raw.strip():raise ValueError('empty')
            body=official_facts(name,raw)
        previous=self.control.db.execute('SELECT content_hash FROM input_snapshots WHERE source=?',(name,)).fetchone()
        digest=self.control.save_input(name,body,schema_ok=True)
        rank_result=None
        if name=='board':
            rank_result=RankController(self.state,self.control).observe(body,board_hash=digest)
        changed=bool(previous and previous[0]!=digest)
        if changed:
            self.control.event('monitor','official_input_changed',{
                'source':name,'old_hash':previous[0],'new_hash':digest,'action':'invalidate_related_experiments'})
        result={'source':name,'hash':digest,'changed':changed,'max_age':max_age}
        if rank_result is not None:result['rank']=rank_result
        return result

    def poll(self):
        results=[]
        for name in SOURCES:
            try:results.append(self.fetch(name))
            except Exception as exc:
                self.control.event('monitor','official_input_error',{
                    'source':name,'error':type(exc).__name__,'raw_body_stored':False})
                results.append({'source':name,'error':type(exc).__name__})
        status={name:self.control.input_fresh(name,max_age) for name,(_,max_age,_) in SOURCES.items()}
        self.control.event('monitor','freshness_snapshot',status)
        return {'results':results,'fresh':status,'mutations_allowed':all(status.values())}

    def close(self):
        if self.owns_client:self.client.close()
        self.control.close()


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--state',default='.market');ap.add_argument('--market',default='https://market.ourolabs.ai')
    ap.add_argument('--once',action='store_true');args=ap.parse_args()
    monitor=Monitor(args.state,args.market)
    try:
        while True:
            print(json.dumps(monitor.poll(),ensure_ascii=False),flush=True)
            if args.once:return
            time.sleep(30)
    finally:monitor.close()


if __name__=='__main__':main()
