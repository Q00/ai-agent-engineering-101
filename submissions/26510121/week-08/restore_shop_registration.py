"""Re-register the existing shop key and public URL; no new keys or trades."""
from datetime import datetime,timezone
import json
import os
import sys
import httpx
from cryptography.hazmat.primitives import serialization
from r3.state import ROOT,read_json
from r3.adapter import load_kit


if __name__=='__main__':
    url=read_json(ROOT/'.market/shop-health.json',{}).get('url','').rstrip('/')
    key_path=ROOT/'shop_key.pem'
    if not url or not key_path.is_file():raise SystemExit('Existing shop identity or URL is missing; no new identity created.')
    card=httpx.get(url+'/.well-known/agent-card.json',timeout=8,trust_env=False)
    card.raise_for_status()
    if card.json().get('name')!='Seochanit Shop':raise SystemExit('Unexpected public agent card; registration stopped.')
    key=serialization.load_pem_private_key(key_path.read_bytes(),password=None)
    owner=os.environ.get('MARKET_OWNER','')
    if not owner:
        for line in (ROOT/'.market/owner.env').read_text(encoding='utf-8-sig').splitlines():
            name,sep,value=line.strip().partition('=')
            if sep and name=='MARKET_OWNER':owner=value.strip().strip('\"\'')
    if not owner:raise SystemExit('Existing owner credential unavailable; no new identity created.')
    kit=load_kit();market=kit.Market('https://market.ourolabs.ai',owner=owner)
    result=market.join('shop','seochanit',key,url,tries=2)
    inventory=market.call('GET','/api/inventory',role='shop')
    registry=httpx.get('https://market.ourolabs.ai/api/shops',timeout=10,trust_env=False)
    shops=registry.json().get('shops',[]) if registry.status_code==200 else []
    evidence={'checked_at':datetime.now(timezone.utc).isoformat(),'joined':True,
        'name':result.get('name'),'round':result.get('round'),'url':url,
        'inventory_status':inventory.status_code,'listed_shops':len(shops),
        'own_listed':any(s.get('name')=='seochanit' and s.get('url','').rstrip('/')==url for s in shops)}
    path=ROOT/'logs'/('shop-registration-restored-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'.json')
    path.write_text(json.dumps(evidence,ensure_ascii=False,indent=2),encoding='utf-8')
    print('SOURCE',path);print(json.dumps(evidence))
