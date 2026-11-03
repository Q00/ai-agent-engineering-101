import json, os, re, sys, time
from datetime import datetime
base = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else None
print('B_LOGSIZE arg', repr(sys.argv[1]) if len(sys.argv) > 1 else None, '-> used', base)
now = time.time()
rows = []
for l in open('.r3/metrics.jsonl', encoding='utf-8', errors='replace'):
    if '"hook":"seller"' not in l:
        continue
    try:
        o = json.loads(l)
    except Exception:
        continue
    if isinstance(o, dict) and isinstance(o.get('ts'), (int, float)):
        rows.append(o)
def age(ds):
    t = [o['ts'] for o in rows if o.get('decision') in ds]
    return round(now - max(t), 1) if t else None
W = 900
n_open = sum(1 for o in rows if o.get('decision') == 'open' and now - o['ts'] <= W)
n_end = sum(1 for o in rows if o.get('decision') in ('accept', 'reject', 'cancel') and now - o['ts'] <= W)
h, m, s = map(int, datetime.now().strftime('%H:%M:%S').split(':')); nows = h * 3600 + m * 60 + s
kit = None
try:
    for l in open('logs/market-kit.log', encoding='utf-8', errors='replace'):
        mm = re.match(r'(\d\d):(\d\d):(\d\d) \[(acp|seller)\]', l)
        if mm:
            kit = (nows - (int(mm[1]) * 3600 + int(mm[2]) * 60 + int(mm[3]))) % 86400
except FileNotFoundError:
    kit = 'no kit log'
lsz = os.path.getsize('logs/market-kit.log') if os.path.exists('logs/market-kit.log') else None
rotated = base is not None and lsz is not None and lsz < base
a_open, a_acc = age({'open'}), age({'accept'})
a_any = round(now - rows[-1]['ts'], 1) if rows else None
print('now', datetime.now().isoformat(timespec='seconds'))
print('last seller open', a_open, 's ago; last accept', a_acc, 's ago; last seller row', a_any, 's ago')
print('last 15 min: open', n_open, '/ accept+reject+cancel', n_end)
print('last kit [acp]/[seller] line', kit, 's ago')
ok = (a_open is None or a_open > 330) and (a_acc is None or a_acc > 60) and (kit is None or (isinstance(kit, int) and kit > 60))
if rotated: print('kit log ROTATED: size', lsz, '< B_LOGSIZE', base, '-> kit line age above is from the new log only')
print('PRECHECK', '판정 불가(B_LOGSIZE 인자 없음)' if base is None else ('판정 불가(로그 회전)' if rotated else ('GO' if ok else 'WAIT')))
