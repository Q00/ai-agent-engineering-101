"""Exercise the deployed/local A2A contract and verify signatures; no market orders."""
import argparse, base64, json, uuid
from pathlib import Path
import httpx
from cryptography.hazmat.primitives.serialization import load_pem_private_key
from cryptography.exceptions import InvalidSignature
p=argparse.ArgumentParser(); p.add_argument('url'); p.add_argument('--production',action='store_true'); a=p.parse_args()
root=Path(__file__).resolve().parent
pem=(root.parent/'shop_key.pem').read_bytes() if a.production else json.loads((root/'.dev.vars').read_text().split('=',1)[1]).encode()
pub=load_pem_private_key(pem,None).public_key()
c=httpx.Client(base_url=a.url.rstrip('/'),timeout=30,headers={'A2A-Version':'1.0'})
card=c.get('/.well-known/agent-card.json').raise_for_status().json()
assert card['supportedInterfaces'][0]['url']==a.url.rstrip('/')+'/'
assert card['supportedInterfaces'][0]['protocolVersion']=='1.0'
print('PASS public A2A card')
def rpc(method,params):
    r=c.post('/',json={'jsonrpc':'2.0','id':str(uuid.uuid4()),'method':method,'params':params}).raise_for_status().json()
    return r

def send(text,tid=None):
    m={'role':'ROLE_USER','messageId':str(uuid.uuid4()),'parts':[{'text':text}]}
    if tid:m['taskId']=tid
    r=rpc('SendMessage',{'message':m});assert 'error' not in r,r
    return r['result']['task']
for item,floor in [('lamp',100),('chair',180),('bookshelf',260)]:
    t=send('I want to buy a '+item);tid=t['id']
    t=send(str(floor-1),tid);assert t['status']['state']=='TASK_STATE_INPUT_REQUIRED'
    t=send(str(floor),tid);assert t['status']['state']=='TASK_STATE_COMPLETED'
    r=t['artifacts'][0]['parts'][0]['data'];assert r['task']==tid and r['price']==floor and r['item']==item
    body={k:r[k] for k in ('shop','item','price','task')}
    canon=lambda x:json.dumps(x,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()
    pub.verify(base64.b64decode(r['sig_seller']),canon(body))
    try: pub.verify(base64.b64decode(r['sig_seller']),canon(dict(body,price=floor+1)))
    except InvalidSignature: pass
    else: raise AssertionError('modified receipt verified')
    assert 'error' in rpc('SendMessage',{'message':{'role':'ROLE_USER','messageId':str(uuid.uuid4()),'taskId':tid,'parts':[{'text':str(floor)}]}})
    assert rpc('GetTask',{'id':tid})['result']['task']['artifacts']==t['artifacts']
    print('PASS',item,'floor, signature, tamper rejection, terminal guard, persisted receipt')
t=send('I want to buy a bookshelf')
r=rpc('CancelTask',{'id':t['id']}); assert r['result']['task']['status']['state']=='TASK_STATE_CANCELED'
print('PASS cancellation; verification trades do not enter the market ledger')
