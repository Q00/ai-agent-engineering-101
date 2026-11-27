import json
import os
import subprocess
import sys
from control import OUT, write

home=OUT/'evidence/local-home'; home.mkdir(exist_ok=False)
env={k:v for k,v in os.environ.items() if k in ('SYSTEMROOT','WINDIR','COMSPEC','PATH','PATHEXT')}
env.update(HOME=str(home),USERPROFILE=str(home),PYTHONDONTWRITEBYTECODE='1')
reports={}
for mode in ('focused','all'):
    with (OUT/('local-'+mode+'.log')).open('xb') as f:
        p=subprocess.run([sys.executable,'-B',str(OUT/'run_isolated.py'),mode,'sandbox'],env=env,stdout=f,stderr=subprocess.STDOUT,timeout=240)
    lines=(OUT/('local-'+mode+'.log')).read_text().splitlines()
    report=json.loads(next(x.split('=',1)[1] for x in lines if x.startswith('BACKPORT_TEST_RESULT=')))
    reports[mode]=report
    assert p.returncode==0 and report['success'], report
write('local-tests.json',json.dumps(reports,indent=2).encode())
print(json.dumps(reports,indent=2))
