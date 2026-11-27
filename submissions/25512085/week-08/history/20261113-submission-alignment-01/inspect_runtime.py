"""Read-only VM probe. Never imports application modules or reads runtime data."""
from pathlib import Path
import ast
import base64
import datetime
import hashlib
import json
import os
import re
import subprocess

ROOT = Path('/home/comalswjd/ai-agent-engineering-101')
REL = 'submissions/25512085/week-08/'
NAMES = ['seller.py', 'store.py', 'seller_cash_policy.py', '_cash_demand.py',
         '_cash_forum.py', '_cash_pricing.py', '_cash_risk.py', 'restock.py',
         'market_buyer.py', 'buyer.py', 'review.py', 'forum_writer.py',
         'seller_forum.py', 'buyer_outcomes.py', 'seller_reputation.py',
         'seller_market_research.py', 'adaptive_strategy.py',
         'guarded_market_kit.py', 'visibility_guard.py', 'run_guarded_market.sh',
         'seller_cash_policy.json']
PATHS = [REL + n for n in NAMES] + ['scripts/market_kit.py', 'scripts/a2a.py']
def git(*args):
    return subprocess.check_output(['git', *args], cwd=ROOT).decode().strip()
def utc(t=None):
    return datetime.datetime.fromtimestamp(t, datetime.timezone.utc).isoformat() if t is not None else datetime.datetime.now(datetime.timezone.utc).isoformat()
head = git('rev-parse', 'HEAD')
sources = {}
for name in PATHS:
    p = ROOT / name
    if not p.is_file():
        sources[name] = {'exists': False}
        continue
    blob = p.read_bytes()
    assert len(blob) < 700000
    text = blob.decode('utf-8')
    assert not re.search(r'-----BEGIN (?:\w+ )?PRIVATE KEY-----', text)
    assert not re.search(r'(?:sk-proj-|ghp_|AIza)[A-Za-z0-9_-]{20,}', text)
    imports = []
    if name.endswith('.py'):
        tree = ast.parse(text)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import): imports.extend(n.name for n in node.names)
            elif isinstance(node, ast.ImportFrom): imports.append(node.module or '')
    sources[name] = {'exists': True, 'bytes': len(blob), 'mtime_utc': utc(p.stat().st_mtime),
                     'sha256': hashlib.sha256(blob).hexdigest(), 'imports': sorted(set(imports)),
                     'source_base64': base64.b64encode(blob).decode()}
config = json.loads((ROOT / REL / 'agent.json').read_text())
safe_config = {}
for role in ('seller', 'buyer', 'market_buyer', 'review', 'restock', 'forum'):
    value = config.get(role)
    if not isinstance(value, dict): continue
    row = {}
    cmd = value.get('cmd')
    if isinstance(cmd, str):
        if re.search(r'(?i)(token|api.key|secret|password|authorization|cookie|export\s|=sk-)', cmd):
            row['cmd'] = '[withheld: sensitive-command pattern]'
        else: row['cmd'] = cmd
    for k in ('model', 'model_name'):
        if isinstance(value.get(k), str) and re.fullmatch(r'[A-Za-z0-9_.:/#-]{1,100}', value[k]): row[k] = value[k]
    safe_config[role] = row
rows = []
btime = next(int(s.split()[1]) for s in Path('/proc/stat').read_text().splitlines() if s.startswith('btime '))
hz = os.sysconf('SC_CLK_TCK')
for proc in Path('/proc').glob('[0-9]*'):
    try:
        args = (proc / 'cmdline').read_bytes().decode().split('\0')[:-1]
        fields = (proc / 'stat').read_text().rsplit(')', 1)[1].split()
        role = next((r for r, n in [('kit', 'guarded_market_kit.py'), ('kit', 'market_kit.py'), ('seller', 'seller.py'), ('buyer', 'buyer.py'), ('market_buyer', 'market_buyer.py'), ('restock', 'restock.py'), ('review', 'review.py'), ('forum', 'forum_writer.py')] if any(a == n or a.endswith('/' + n) for a in args)), None)
        if args and Path(args[0]).name == 'cloudflared': role = 'tunnel'
        if role:
            rows.append({'role': role, 'pid': int(proc.name), 'ppid': int(fields[1]), 'start_utc': utc(btime + int(fields[19]) / hz),
                         'cwd': str((proc / 'cwd').resolve()), 'exe': str((proc / 'exe').resolve())})
    except (OSError, ValueError, UnicodeError): pass
logs = []
for n in ('market-kit.log', 'market-kit.log.1'):
    p = ROOT / REL / 'logs' / n
    if p.exists():
        s = p.stat()
        logs.append({'name': n, 'bytes': s.st_size, 'mtime_utc': utc(s.st_mtime), 'inode': s.st_ino})
result = {'observed_utc': utc(), 'head': head, 'head_after': git('rev-parse', 'HEAD'),
          'dirty_allowlisted': git('diff', 'HEAD', '--name-only', '--', *PATHS).splitlines(),
          'staged_allowlisted': git('diff', '--cached', '--name-only', '--', *PATHS).splitlines(),
          'sources': sources, 'agent_commands_models_only': safe_config,
          'processes': sorted(rows, key=lambda p: p['pid']), 'logs_stat_only': logs,
          'python': __import__('sys').version.split()[0]}
assert result['head'] == result['head_after']
print(json.dumps(result))
