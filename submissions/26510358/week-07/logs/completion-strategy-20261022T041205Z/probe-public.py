"""Deployment smoke test: current quotes and same-task cancellation, no purchase."""
import json
from pathlib import Path
import sys
import time
HERE=Path(__file__).resolve().parent
SUB=HERE.parents[1]
sys.path.insert(0,str(SUB))
from protocol import A2AClient
from tracing import Trace

PUBLIC='https://wisconsin-dylan-nhs-myspace.trycloudflare.com'
results=[]
for item,price in [('lamp',100),('chair',180),('bookshelf',260)]:
    client=A2AClient(PUBLIC,Trace('deployment-quote',HERE/'public-quotes'),timeout=45)
    try:
        start=time.monotonic()
        task=client.send('I want to buy a '+item)
        elapsed=round(time.monotonic()-start,3)
        assert task['status']['state']=='TASK_STATE_INPUT_REQUIRED',task
        metadata=task['status']['message']['metadata']
        assert metadata['ask']==price and metadata['itemId']==item and metadata['quantity']==1,metadata
        canceled=client.rpc('CancelTask',{'id':task['id']})
        assert canceled['id']==task['id'] and canceled['status']['state']=='TASK_STATE_CANCELED',canceled
        results.append({'item':item,'price':price,'task':task['id'],'quote':metadata['quoteId'],
                        'quote_seconds':elapsed,'same_task_canceled':True,'purchase_created':False})
    finally:
        client.close()
(HERE/'public-quote-verification.json').write_text(json.dumps({'passed':True,'quotes':results},indent=2))
print(json.dumps(results,indent=2))
