"""Read-only operational verification; never creates a purchase or review."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
import time
import httpx

HERE=Path(__file__).resolve().parent
SUB=HERE.parents[1]
before=json.loads((HERE/'board-before.json').read_text())
cutover=json.loads((HERE/'cutover-complete.json').read_text())
url=cutover['public_url']
with httpx.Client(timeout=15,trust_env=False) as client:
    board=client.get('https://market.ourolabs.ai/api/board').json()
    shops=client.get('https://market.ourolabs.ai/api/shops').json()
    card_response=client.get(url+'/.well-known/agent-card.json');card_response.raise_for_status()
    health=client.get(url+'/healthz');health.raise_for_status()
(HERE/'board-verified.json').write_text(json.dumps(board,ensure_ascii=False,indent=2))
(HERE/'shops-verified.json').write_text(json.dumps(shops,ensure_ascii=False,indent=2))
(HERE/'card-verified.json').write_text(json.dumps(card_response.json(),ensure_ascii=False,indent=2))
profiles={}
for role in ('shops','buyers'):
    owned=lambda b:sorted((r['name'],r['joined']) for r in b[role] if (r.get('owner') or {}).get('login')=='SUNGMYEONGGI')
    assert owned(before)==owned(board),(role,owned(before),owned(board))
    row=next(r for r in board[role] if r['name']=='myeonggi')
    assert row['alive'] and row['owner']['login']=='SUNGMYEONGGI'
    profiles[role]={'unchanged_owned_profile_identities':owned(board),'active':row['alive'],'deals':row['deals']}
shop_before=next(r for r in json.loads((HERE/'shops-before.json').read_text())['shops'] if r['name']=='myeonggi')
shop_after=next(r for r in shops['shops'] if r['name']=='myeonggi')
assert shop_before['pubkey']==shop_after['pubkey']
assert shop_after['url']==url
assert card_response.json()['description']==json.loads((HERE/'card-before.json').read_text())['description']
for name,digest in cutover['identity_file_sha256'].items():
    assert hashlib.sha256((SUB/name).read_bytes()).hexdigest()==digest
with httpx.Client(transport=httpx.HTTPTransport(uds=str(SUB/'.market/shop-service/control.sock')),timeout=15) as client:
    service=client.get('http://localhost/').json()
assert service['public_url']==url
assert all(g['alive'] for g in service['generations'])
for pid in (cutover['new_kit_pid'],cutover['independent_tunnel_pid'],int((SUB/'.market/shop-service/pid').read_text())):
    assert Path(f'/proc/{pid}/cmdline').is_file()
assert not Path(f'/proc/{cutover["old_kit_pid"]}/cmdline').read_bytes() if Path(f'/proc/{cutover["old_kit_pid"]}/cmdline').exists() else True
console=(HERE/'market-final-console.log').read_text()
assert '[market] shop registered as myeonggi' in console
assert '[market] buyer registered as myeonggi' in console
verified=[line for line in console.splitlines() if '[market] ledger #' in line and 'verified True' in line]
report={'captured_at':datetime.now(timezone.utc).isoformat(),'passed':True,'public_url':url,
        'profiles':profiles,'no_new_profiles':True,'shop_public_key_unchanged':True,
        'private_key_files_unchanged':True,'description_unchanged':True,'service':service,
        'buyer_verified_market_results_since_restart':verified[-3:],
        'verification_created_purchases':False}
(HERE/'deployment-verified.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
print(json.dumps({k:report[k] for k in ('captured_at','passed','public_url','no_new_profiles','buyer_verified_market_results_since_restart')},ensure_ascii=False))
