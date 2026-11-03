import os, re, subprocess, sys
port = sys.argv[1] if len(sys.argv) > 1 else ''
base = int(sys.argv[2]) if len(sys.argv) > 2 and sys.argv[2].isdigit() else None
ps = subprocess.run(['ps', '-eo', 'pid=,pgid=,args='], capture_output=True, text=True).stdout.splitlines()
procs = [l.split(None, 2) for l in ps if len(l.split(None, 2)) == 3]
kit = [p for p in procs if 'market_kit.py' in p[2]]
sel = [p for p in procs if 'hooks/seller.py' in p[2]]
bad = []
print('kit lines', len(kit)); [print('  KIT', *p) for p in kit]
print('seller lines', len(sel)); [print('  SELLER', *p) for p in sel]
if len(kit) != 1: bad.append('kit lines != 1')
if not sel: bad.append('seller lines == 0')
pg = sorted({p[1] for p in sel})
if len(pg) != 1: bad.append(f'seller PGIDs {pg} (must be exactly one)')
if kit and pg and kit[0][1] in pg: bad.append('SELLER_PGID == KIT_PGID')
if kit and re.search(r'--deployed|--buyer-only', kit[0][2]): bad.append('kit started with --deployed/--buyer-only (no watchdog)')
ports = sorted({m for p in sel for m in re.findall(r'--port[ =](\d+)', p[2])})
lsz = os.path.getsize('logs/market-kit.log')
rotated = base is not None and lsz < base
print('kit log size', lsz, '| B_LOGSIZE', base, '| ROTATED (smaller than baseline): reading the new log from the start, not STOP' if rotated else '')
klog = [m[1] for m in (re.search(r'\[shop\] your seller on port (\d+)', l) for l in open('logs/market-kit.log', encoding='utf-8', errors='replace')) if m]
print('seller --port', ports, '| kit log last seller port', klog[-1] if klog else None, '| SELLER_PORT', port)
if ports != [port]: bad.append('seller --port != SELLER_PORT')
if rotated and not klog: print('kit log seller port: no [shop] line in the rotated log (not STOP; seller --port still checked)')
elif not klog or klog[-1] != port: bad.append('kit log seller port != SELLER_PORT')
if len(pg) == 1:
    mem = [p for p in procs if p[1] == pg[0]]
    print('PGID', pg[0], 'members', len(mem)); [print('  MEMBER', *p) for p in mem]
    if any('hooks/seller.py' not in p[2] for p in mem): bad.append('PGID group has a non-seller member')
print('KIT_PID', kit[0][0] if len(kit) == 1 else None, 'KIT_PGID', kit[0][1] if len(kit) == 1 else None)
print('SELLER_PGID', pg[0] if len(pg) == 1 else None)
print('GATE', 'GO' if not bad else 'STOP: ' + '; '.join(bad))
