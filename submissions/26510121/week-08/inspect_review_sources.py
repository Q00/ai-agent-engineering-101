"""Capture public forum/ledger/board surfaces for review attribution. GET only."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timezone
import json
import re
import httpx
from r3.state import ROOT

BASE='https://market.ourolabs.ai'


def main():
    def get(path):
        try:
            r=httpx.get(BASE+path,timeout=20,trust_env=False)
            return path,{'status':r.status_code,'text':r.text}
        except Exception as exc:return path,{'error':type(exc).__name__}
    paths=['/forum','/api/forum?limit=100','/api/ledger?after=-500','/api/board']
    with ThreadPoolExecutor(max_workers=4) as pool:responses=dict(pool.map(get,paths))
    page=responses['/forum'].get('text','')
    scripts=re.findall(r'<script[^>]+src=[\"\']([^\"\']+)',page)
    result={'at':datetime.now(timezone.utc).isoformat(),'responses':responses,'script_links':scripts}
    p=ROOT/'logs'/('review-public-source-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'.json')
    p.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print('SOURCE',p)
    print('SCRIPTS',json.dumps(scripts))
    print('PAGE_API_SNIPPETS','\n'.join(s for s in page.splitlines() if any(w in s for w in
        ['/api/','vote','dislike','stance','review','fetch(']))[:18000])
    for path in paths[1:]:
        response=responses[path]
        if response.get('status')!=200:print(path,response.get('status',response.get('error')));continue
        data=json.loads(response['text'])
        if path.startswith('/api/ledger'):
            from collections import Counter
            print('LEDGER_KINDS',dict(Counter(e['kind'] for e in data)))
            print('VOTE_SAMPLES',json.dumps([e for e in data if 'vote' in e['kind'] or 'forum' in e['kind']][:8],ensure_ascii=True))
        elif path.startswith('/api/forum'):
            print('FORUM_ROWS',len(data),'FIELDS',list(data[0]) if data else [])
            print('OWN_POSTS',json.dumps([e for e in data if e.get('owner')=='seochanit' or e.get('author')=='seochanit'],ensure_ascii=True)[:10000])
        else:
            rows=data.get('reviews',[])
            print('BOARD_REVIEW_TYPE',type(rows).__name__,'COUNT',len(rows))
            print('REVIEW_SAMPLE',json.dumps(rows[:1] if isinstance(rows,list) else list(rows.items())[:1],ensure_ascii=True)[:2200])


if __name__=='__main__':main()
