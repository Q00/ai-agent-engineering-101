"""Restore a fresh local SDK task snapshot during a controlled seller update."""
import asyncio
import time
import json
from pathlib import Path
from a2a.types import Task
from a2a.server.context import ServerCallContext
from google.protobuf.json_format import ParseDict
from optimizer import read


def snapshot_header(path):
    """Read metadata without allocating the entire historical task archive."""
    import re
    with Path(path).open(encoding='utf-8-sig') as f:head=f.read(4096)
    match=re.search(r'"at"\s*:\s*([\d.eE+-]+)',head)
    return {'at':float(match[1]) if match else 0}


def snapshot_tasks(path):
    """Stream standard JSON task arrays, including tasks larger than a chunk."""
    decoder=json.JSONDecoder()
    with Path(path).open(encoding='utf-8-sig') as f:
        buffer=''
        while '"tasks"' not in buffer:
            chunk=f.read(65536)
            if not chunk:return
            buffer+=chunk
        buffer=buffer[buffer.index('[',buffer.index('"tasks"'))+1:]
        while True:
            buffer=buffer.lstrip(' \r\n\t,')
            if buffer.startswith(']'):return
            try:raw,end=decoder.raw_decode(buffer)
            except ValueError:
                chunk=f.read(65536)
                if not chunk:raise ValueError('Incomplete handoff task archive')
                buffer+=chunk;continue
            yield raw
            buffer=buffer[end:]


def capture_streaming(md,output,base_url='http://127.0.0.1:1100'):
    """Disk-backed incremental handoff: preserve every task without a RAM spike."""
    import sqlite3,httpx,datetime
    md=Path(md);output=Path(output);header=snapshot_header(output) if output.exists() else {'at':0}
    with sqlite3.connect(str(output)+'.sqlite3') as c:
        c.execute('create table if not exists tasks(id text primary key,payload text)')
        if not c.execute('select 1 from tasks limit 1').fetchone() and output.exists():
            for raw in snapshot_tasks(output):c.execute('insert or replace into tasks values(?,?)',(raw['id'],json.dumps(raw,ensure_ascii=False)))
            c.commit()
        token=None;page=0;seen=set();recover=[]
        with httpx.Client(timeout=120,headers={'A2A-Version':'1.0'}) as client:
            while True:
                params={'pageSize':100,'includeArtifacts':True}
                if header['at']:params['statusTimestampAfter']=datetime.datetime.fromtimestamp(max(0,header['at']-120),datetime.timezone.utc).isoformat()
                if token:params['pageToken']=token
                response=client.post(base_url+'/',json={'jsonrpc':'2.0','id':page,'method':'ListTasks','params':params});response.raise_for_status();body=response.json()
                if body.get('error'):raise RuntimeError(str(body['error']))
                for raw in body['result'].get('tasks',[]):
                    if raw.get('status',{}).get('state') in ('TASK_STATE_INPUT_REQUIRED','TASK_STATE_WORKING','TASK_STATE_SUBMITTED'):
                        response=client.post(base_url+'/',json={'jsonrpc':'2.0','id':raw['id'],'method':'GetTask','params':{'id':raw['id']}});response.raise_for_status();full=response.json()
                        if full.get('error'):raise RuntimeError(str(full['error']))
                        raw=full['result']
                    if raw.get('status',{}).get('state')=='TASK_STATE_COMPLETED' and not raw.get('artifacts'):recover.append(raw)
                    c.execute('insert or replace into tasks values(?,?)',(raw['id'],json.dumps(raw,ensure_ascii=False)))
                token=body['result'].get('nextPageToken')
                if not token:break
                if token in seen:raise RuntimeError('Repeated handoff pagination token')
                seen.add(token);page+=1
            c.commit()
        recovered=recover_receipts(recover,md)
        for raw in recover:c.execute('insert or replace into tasks values(?,?)',(raw['id'],json.dumps(raw,ensure_ascii=False)))
        c.commit();temporary=Path(str(output)+'.tmp');at=time.time();count=0
        with temporary.open('w',encoding='utf-8') as f:
            f.write('{"at":'+str(at)+',"include_artifacts":true,"tasks":[')
            for row in c.execute('select payload from tasks'):
                if count:f.write(',')
                f.write(row[0]);count+=1
            f.write(']}')
        temporary.replace(output)
    return dict(at=at,tasks=count,recovered_original_receipts=recovered,snapshot=str(output),streaming=True)

def transcript(raw):
    messages=list(raw.get('history',[]))+[(raw.get('status',{}).get('message') or {})]
    seen=set();result=[]
    for message in messages:
        identity=message.get('messageId')
        if identity and identity in seen:continue
        if identity:seen.add(identity)
        role=message.get('role')
        if role not in ('ROLE_USER','ROLE_AGENT'):continue
        text=' '.join(p.get('text','') for p in message.get('parts',[]) if p.get('text'))
        if text:result.append(('buyer: ' if role=='ROLE_USER' else 'you: ')+text)
    return result or ['buyer: restored negotiation']

def recover_receipts(tasks,md):
    """Recover missing artifacts only from the market's verified original receipt."""
    from shop_profit import ownership_tags
    targets={t['id']:t for t in tasks if t.get('status',{}).get('state')=='TASK_STATE_COMPLETED'
             and not any(p.get('data',{}).get('sig_seller') for a in t.get('artifacts',[]) for p in a.get('parts',[]) if isinstance(p.get('data'),dict))}
    recovered=0;path=Path(md)/'ledger.jsonl';tags=ownership_tags()
    if not targets or not path.exists():return 0
    with path.open(encoding='utf-8') as source:
        for line in source:
            if '"result"' not in line or '"receipt"' not in line:continue
            try:event=json.loads(line)
            except ValueError:continue
            d=event.get('data') or {};rec=d.get('receipt') or {};identity=rec.get('task')
            if event.get('kind')!='result' or event.get('round',3)!=3 or identity not in targets:continue
            if d.get('shop') not in tags or not d.get('verified') or (d.get('checks') or {}).get('sig_seller_ok') is not True:continue
            targets[identity].setdefault('artifacts',[]).append({'artifactId':'restored-receipt-'+identity,'name':'receipt','parts':[{'data':rec}]})
            del targets[identity];recovered+=1
    return recovered

def capture(md,output=None,base_url='http://127.0.0.1:1100',incremental=False):
    import httpx
    md=Path(md);tasks={};token=None
    output=Path(output) if output else md/'seller-handoff-snapshot.json'
    saved=read(output,{})
    cached={t['id']:t for t in saved.get('tasks',[])}
    if incremental and cached:tasks.update(cached)
    with httpx.Client(timeout=120,headers={'A2A-Version':'1.0'}) as client:
        page=0;seen_tokens=set()
        while True:
            params={'pageSize':100,'includeArtifacts':True}
            if incremental and cached:
                import datetime
                params['statusTimestampAfter']=datetime.datetime.fromtimestamp(max(0,saved['at']-120),datetime.timezone.utc).isoformat()
            if token:params['pageToken']=token
            response=client.post(base_url+'/',json={'jsonrpc':'2.0','id':page,'method':'ListTasks','params':params})
            response.raise_for_status();body=response.json()
            if body.get('error'):raise RuntimeError(str(body['error']))
            result=body['result']
            for task in result.get('tasks',[]):tasks[task['id']]=task
            token=result.get('nextPageToken')
            if not token:break
            if token in seen_tokens:raise RuntimeError('Repeated seller page token; do not restart')
            seen_tokens.add(token);page+=1
        for identity,task in list(tasks.items()):
            if task.get('status',{}).get('state') not in ('TASK_STATE_INPUT_REQUIRED','TASK_STATE_WORKING','TASK_STATE_SUBMITTED'):continue
            previous=cached.get(identity)
            if previous and previous.get('history') and previous.get('status')==task.get('status'):
                tasks[identity]=previous
                continue  # Preserve the original complete transcript unchanged.
            response=client.post(base_url+'/',json={'jsonrpc':'2.0','id':identity,'method':'GetTask','params':{'id':identity}})
            response.raise_for_status();body=response.json()
            if body.get('error'):raise RuntimeError(str(body['error']))
            tasks[identity]=body['result']
    rows=list(tasks.values());recovered=recover_receipts(rows,md)
    snapshot={'at':time.time(),'tasks':rows,'recovered_original_receipts':recovered,'include_artifacts':True}
    output.write_text(json.dumps(snapshot,ensure_ascii=False),encoding='utf-8')
    return {'tasks':len(rows),'recovered_original_receipts':recovered,'snapshot':str(output)}

def restore(agent,store,md):
    path=md/'seller-handoff.json'
    if not path.exists():return 0
    snapshot=snapshot_header(path)
    if not 0<=time.time()-snapshot.get('at',0)<300:return 0
    inventory=read(md/'inventory.json',{})
    units={u['unit']:u for u in inventory.get('units',[])}
    async def load():
        import re
        from optimizer import db
        count=0
        for raw in snapshot_tasks(path):
            task=ParseDict(raw,Task())
            await store.save(task,ServerCallContext())
            status=raw.get('status',{});state=status.get('state','')
            if state in ('TASK_STATE_INPUT_REQUIRED','TASK_STATE_WORKING','TASK_STATE_SUBMITTED'):
                with db(md) as c:
                    row=c.execute('select * from trials where task=?',(task.id,)).fetchone()
                if row and row['unit'] in units:
                    history=transcript(raw)
                    text=' '.join(p.get('text','') for p in (status.get('message') or {}).get('parts',[]))
                    matches=list(re.finditer(r'(?:asking|Counteroffer:)\s*(\d+)',text,re.I))
                    if not matches:
                        text=next((line for line in reversed(history) if line.startswith('you: ') and re.search(r'(?:asking|Counteroffer:)\s*(\d+)',line,re.I)),text)
                        matches=list(re.finditer(r'(?:asking|Counteroffer:)\s*(\d+)',text,re.I))
                    agent.open[task.id]={'unit':units[row['unit']],'ask':int(matches[0][1]) if matches else row['ask'],
                        'offer':row['last_offer'],'previous_offer':None,
                        'lines':history,'seen_at':time.time(),
                        'warranty_hours':float(re.search(r'Includes a ([\d.]+)-hour',text)[1]) if re.search(r'Includes a ([\d.]+)-hour',text) else 0}
            # Signed receipt reservations survive until inventory confirms sale.
            for artifact in raw.get('artifacts',[]):
                for part in artifact.get('parts',[]):
                    unit=(part.get('data') or {}).get('unit')
                    if unit and unit in units:
                        agent.promised.add(unit);agent.promised_at[unit]=time.time()
            count+=1
        return count
    count=asyncio.run(load())
    (md/'seller-handoff.json').replace(md/'seller-handoff-restored.json')
    return count

if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('--capture',action='store_true',required=True)
    parser.add_argument('--state',type=Path,default=Path('.market'));parser.add_argument('--output',type=Path)
    parser.add_argument('--incremental',action='store_true')
    args=parser.parse_args();print(json.dumps(capture(args.state,args.output,incremental=args.incremental)))
