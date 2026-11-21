"""Capture public market rules and linked patch notes, preserving dated diffs."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import difflib
from html.parser import HTMLParser
import json
import re
from urllib.parse import urljoin, urlsplit
import httpx
from r3.state import ROOT, read_json

BASE='https://market.ourolabs.ai'


class Page(HTMLParser):
    def __init__(self):
        super().__init__();self.text=[];self.links=[];self.active=None;self.skip=0
    def handle_starttag(self,tag,attrs):
        attrs=dict(attrs)
        if tag in {'script','style'}:self.skip+=1
        if tag=='a':self.active=[attrs.get('href',''),'']
    def handle_endtag(self,tag):
        if tag in {'script','style'}:self.skip=max(0,self.skip-1)
        if tag=='a' and self.active:self.links.append(self.active);self.active=None
    def handle_data(self,data):
        if not self.skip:
            if data.strip():self.text.append(data.strip())
            if self.active:self.active[1]+=data


def main():
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    def get(path):
        url=urljoin(BASE+'/',path)
        try:
            r=httpx.get(url,timeout=20,trust_env=False,follow_redirects=True)
            return url,{'status':r.status_code,'url':str(r.url),'text':r.text}
        except Exception as exc:return url,{'error':type(exc).__name__}
    with ThreadPoolExecutor(max_workers=3) as pool:
        sources=dict(pool.map(get,['/','/agent.md','/api/board']))
    home=Page();home.feed(sources[BASE+'/'].get('text',''))
    selected=[]
    for href,label in home.links:
        url=urljoin(BASE+'/',href)
        if (urlsplit(url).netloc==urlsplit(BASE).netloc and
            re.search(r'patch|changelog|rules|notice|update|패치|규칙|3\.4',href+' '+label,re.I)
            and url not in sources and url not in selected):selected.append(url)
    with ThreadPoolExecutor(max_workers=3) as pool:sources.update(dict(pool.map(get,selected[:6])))
    current=sources[BASE+'/agent.md'].get('text','')
    old=read_json(ROOT/'logs/peer-market-source-20261105T094827924168Z.json')['responses']['/agent.md']['data']
    diff=''.join(difflib.unified_diff(old.splitlines(True),current.splitlines(True),
                                   fromfile='agent.md captured 2026-11-05',tofile='agent.md current '+stamp))
    result={'at':datetime.now(timezone.utc).isoformat(),'sources':sources,'home_links':home.links,
            'home_text':'\n'.join(home.text),'rule_diff_from_nov5':diff}
    path=ROOT/'logs'/('patch-rules-source-'+stamp+'.json')
    path.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print('SOURCE',path)
    print('LINKS',json.dumps(home.links,ensure_ascii=True))
    print('HOME_TEXT',result['home_text'][:20000])
    print('RULE_DIFF',diff)
    for url in selected[:6]:
        p=Page();p.feed(sources[url].get('text',''));print('LINKED_PAGE',url,'\n','\n'.join(p.text)[:24000])


if __name__=='__main__':main()
