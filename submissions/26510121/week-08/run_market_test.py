"""Detached official test with isolated memory and an unedited console transcript."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

HERE=Path(__file__).resolve().parent
REPO=HERE.parents[2]
AGENT=HERE.parent/'week-07'


def main():
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    folder=HERE/'logs'/('market-test-'+stamp)
    folder.mkdir()
    for relative in ['test-report.json','logs/market-test.log']:
        source=AGENT/relative
        if source.is_file():shutil.copy2(source,folder/('previous-'+source.name))
    env=dict(os.environ,PYTHONUTF8='1',MARKET_LIVE='0',MARKET_ROUND='0',PYTHONPATH=str(AGENT),
             MARKET_BUYER_MEMORY=str(AGENT/'.market'/('test-memory-'+stamp+'.jsonl')))
    if not env.get('OPENAI_API_KEY'):
        for line in (HERE.parent/'week-06'/'.env').read_text(encoding='utf-8-sig').splitlines():
            name,sep,value=line.partition('=')
            if sep and name.strip()=='OPENAI_API_KEY':env['OPENAI_API_KEY']=value.strip().strip('"\'')
    if not env.get('OPENAI_API_KEY'):raise RuntimeError('existing_authorized_credential_unavailable')
    with (folder/'console.log').open('x',encoding='utf-8') as log:
        process=subprocess.Popen([sys.executable,'-u',str(REPO/'scripts'/'market_test_week07.py'),str(AGENT)],
            cwd=REPO,env=env,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,encoding='utf-8',errors='replace')
        for line in process.stdout:
            line=line.replace(env['OPENAI_API_KEY'],'[REDACTED]')
            log.write(line);log.flush()
        code=process.wait()
        log.write('\nEXIT: '+str(code)+'\n')
    (folder/'result.json').write_text(json.dumps({'exit_code':code,'production_memory_disabled':True,
        'console':'console.log','finished_utc':datetime.now(timezone.utc).isoformat()},indent=2)+'\n',encoding='utf-8')
    print('Official market test exit:',code,flush=True)
    return code


if __name__=='__main__':raise SystemExit(main())
