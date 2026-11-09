"""Wait for the next Buyer child BEFORE RPC/payment, then stop existing runtime."""
from datetime import datetime,timezone
import subprocess
import sys
import time
import uuid
from r3.state import ROOT,Journal,read_json,atomic_json

if __name__=='__main__':
    j=Journal();token=uuid.uuid4().hex;started=time.time()
    before=read_json(j.md/'runtime.json',{})
    j.put('buyer_maintenance',{'token':token,'until':started+120})
    record={'at':started,'runtime_before':before,'token':token,'drained':False}
    try:
        while time.time()<started+85:
            ready=j.get('buyer_maintenance_ready',{})
            if ready.get('token')==token:
                record.update(drained=True,ready=ready)
                print('Buyer is waiting before any negotiation or payment; stopping runtime.',flush=True)
                result=subprocess.run([sys.executable,'prepare_autonomous_restart.py'],cwd=ROOT)
                record['stop_exit_code']=result.returncode
                if result.returncode:raise RuntimeError('Existing runtime did not release its lease')
                break
            time.sleep(.5)
        else:raise RuntimeError('No new Buyer child reached the safe maintenance point; runtime left running')
    finally:
        j.put('buyer_maintenance',{})
        record['finished_at']=time.time()
        path=ROOT/'logs'/('buyer-v12-drain-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'.json')
        atomic_json(path,record);print('SOURCE',path,flush=True)
