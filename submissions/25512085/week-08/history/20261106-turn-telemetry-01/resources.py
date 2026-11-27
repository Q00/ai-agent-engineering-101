"""Read-only, narrowly scoped VM resource inspection. No operational log reads."""
import base64
from pathlib import Path
import shutil
import subprocess
import zlib

OUT = Path(__file__).resolve().parent
CODE = r'''
from datetime import datetime, timezone
from pathlib import Path
import subprocess
print('UTC='+datetime.now(timezone.utc).isoformat(), flush=True)
base=Path('/home/comalswjd/ai-agent-engineering-101')
week=base/'submissions/25512085/week-08'
commands=[['free','-m'],['uptime'],['df','-h',str(week)],['df','-i',str(week)],
          *[['du','-sh',str(p)] for p in (base,week,week/'logs',week/'.market',week/'history',base/'.git')]]
for command in commands:
    print('COMMAND='+ ' '.join(command), flush=True)
    subprocess.run(command, check=False)
for line in Path('/proc/meminfo').read_text().splitlines():
    if line.split(':')[0] in ('MemTotal','MemAvailable','SwapTotal','SwapFree'):
        print(line)
print('TOP_RSS_PID_COMM_RSS_KIB_ELAPSED', flush=True)
rows=subprocess.check_output(['ps','-eo','pid,comm,rss,etime','--sort=-rss'],text=True).splitlines()
print('\n'.join(rows[:9]))
print('LOG_FILE_STAT_ONLY', flush=True)
for p in sorted((week/'logs').glob('market-kit.log*')):
    if p.is_file():
        s=p.stat(); print(p.name, 'bytes='+str(s.st_size), 'mtime_utc='+datetime.fromtimestamp(s.st_mtime,timezone.utc).isoformat())
print('ROTATION_SOURCE_LINES_ONLY', flush=True)
for p in (base/'scripts/market_kit.py',):
    for i,line in enumerate(p.read_text().splitlines(),1):
        if any(x in line for x in ('LOG_MAX','os.replace(LOG_FILE','with open(LOG_FILE','f.write(f"{stamp}')):
            print(p.name+':'+str(i)+':'+line.strip())
'''
if __name__ == '__main__':
    encoded=base64.b64encode(zlib.compress(CODE.encode())).decode()
    command='/home/comalswjd/venv/bin/python -B -c "import base64,zlib;exec(zlib.decompress(base64.b64decode(\''+encoded+'\')))"'
    with (OUT/'resources-v3-stderr.log').open('xb') as err, (OUT/'resources-v3-stdout.log').open('xb') as out:
        p=subprocess.run([shutil.which('gcloud.cmd'),'compute','ssh','comalswjd@market-agent',
            '--project=studious-plate-458511-a1','--zone=us-central1-a','--quiet','--command='+command],
            stdout=out,stderr=err,timeout=180)
    print('resource inspection exit='+str(p.returncode))
    raise SystemExit(p.returncode)
