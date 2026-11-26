"""Read-only task continuity around a healthy seller generation reload."""
from datetime import datetime,timezone
import json
from pathlib import Path
import subprocess
import sys

HERE=Path(__file__).resolve().parent
SUB=HERE.parents[1]
REPO=SUB.parents[2]
sys.path.insert(0,str(SUB))
from protocol import A2AClient
from tracing import Trace

PUBLIC='https://wisconsin-dylan-nhs-myspace.trycloudflare.com'
trace=Trace('cutover-continuity',HERE/'continuity-traces')
client=A2AClient(PUBLIC,trace,timeout=45)
try:
    quoted=client.send('I want to buy a lamp')
    assert quoted['status']['state']=='TASK_STATE_INPUT_REQUIRED'
    assert quoted['status']['message']['metadata']['ask']==100
    task_id=quoted['id']
    quote_id=quoted['status']['message']['metadata']['quoteId']
    (HERE/'quote-before-cutover.json').write_text(json.dumps(quoted,ensure_ascii=False,indent=2))
    deployment=subprocess.run([str(REPO/'.venv/bin/python'),str(HERE/'deploy-market.py')],
        cwd=REPO,check=False,capture_output=True,text=True)
    (HERE/'deployment-console.log').write_text(deployment.stdout+deployment.stderr)
    deployment.check_returncode()
    current=client.get_task(task_id,timeout=2)
    assert current['id']==task_id and current['status']['state']=='TASK_STATE_INPUT_REQUIRED'
    assert current['status']['message']['metadata']['quoteId']==quote_id
    assert current['status']['message']['metadata']['ask']==100
    canceled=client.rpc('CancelTask',{'id':task_id})
    assert canceled['id']==task_id and canceled['status']['state']=='TASK_STATE_CANCELED'
    result={'passed':True,'public_url':PUBLIC,'original_task_id':task_id,'original_quote_id':quote_id,
            'quote_and_task_preserved':True,'read_only_recovery_supported':True,
            'canceled_on_original_task':True,'purchase_created':False,
            'verified_at':datetime.now(timezone.utc).isoformat()}
    (HERE/'cutover-verification.json').write_text(json.dumps(result,indent=2))
    print(json.dumps(result,indent=2))
finally:
    client.close()
