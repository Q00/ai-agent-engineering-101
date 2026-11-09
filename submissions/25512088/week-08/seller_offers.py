from seller_procurement import settings
"""Fresh, cost-protected product discovery."""
import json
import time
from collections import Counter
from seller_strategy import sale_quote
from market_intel import read_json


def offers(state, program, intel, ref='', limit=3):
    path=state/'inventory.json'
    if not path.exists() or time.time()-path.stat().st_mtime>90:return []
    inv=json.loads(path.read_text())
    if inv.get('round')!=3:return []
    cp=state/'catalog.json'
    catalog=json.loads(cp.read_text()) if cp.exists() else {}
    preferences=read_json(state/'buyer-preferences.json',{})
    reserved=program.reserved_units(); ranked=[]
    holdings=Counter(u['sku'] for u in inv.get('units',[]) if u['unit'] not in reserved)
    for u in inv.get('units',[]):
        if u['unit'] in reserved:continue
        # Stock previews are not buyer inquiries or conversion trials.
        try:q=sale_quote(u,catalog,program,intel,ref=ref,record=False,holdings=holdings[u['sku']],
                        buyer_preferences=preferences)
        except ValueError:continue
        hours=(u['expires_at']-time.time())/3600 if u.get('expires_at') else None
        count=intel.comparable(u['sku'],u['grade'],hours,q.get('warranty_hours',0))['count']
        ranked.append((count,u,q))
    ranked.sort(key=lambda x:x[0],reverse=True)
    result=[];seen=set()
    for count,u,q in ranked:
        if u['sku'] in seen:continue
        seen.add(u['sku'])
        result.append(dict(sku=u['sku'],name=u['name'],grade=u['grade'],price=q['price'],
            warranty_hours=q.get('warranty_hours',0),expires_at=u.get('expires_at'),
            message=f"I want to buy {u['sku']}",
            conditions='Requote before buying; verify certificate, grade, shelf life and warranty at checkout.'))
        if len(result)>=limit:break
    return result
