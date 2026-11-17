"""Preserve live logs byte-for-byte in gzip without editing or rotating originals.
The old tracked market-kit.log is retained for the unchanged course CI contract.
Run from any directory. All output stays in this student's submission directory.
"""
from pathlib import Path
from datetime import datetime
import gzip
import hashlib
import json
import re

HERE = Path(__file__).resolve().parent
AUDIT = HERE / 'logs/report-audit-20261027-150717/summary.json'
STAMP = datetime.now().astimezone()
OUT = HERE / 'logs' / ('submission-evidence-' + STAMP.strftime('%Y%m%d-%H%M%S'))
OUT.mkdir(exist_ok=False)
audit = json.loads(AUDIT.read_text(encoding='utf-8'))

# Compare potential private claim values without ever displaying or storing them.
script = Path.home() / 'agent-market/rejoin.ps1'
secret_values = []
if script.exists():
    source = script.read_text(encoding='utf-8-sig')
    secret_values = [m.group(2).encode('utf-8') for m in re.finditer(
        r'(?im)^\s*\$env:MARKET_OWNER\s*=\s*([\"\x27])([^\"\x27]+)\1', source)
        if len(m.group(2)) >= 8]
patterns = [rb'-----BEGIN (?:[A-Z]+ )?PRIVATE KEY-----',
            rb'gh[pousr]_[A-Za-z0-9]{30,}', rb'github_pat_[A-Za-z0-9_]{30,}',
            rb'sk-ant-[A-Za-z0-9_-]{20,}']
manifest = {'captured_at': STAMP.isoformat(), 'policy': 'Full original bytes, no truncation/redaction/log edits. Canonical tracked market-kit.log remains the earlier CI-compatible log; latest complete raw evidence is gzip below.', 'files': {}}
for name in ['logs/market-kit.log', 'memory.jsonl', 'sales.jsonl', 'talks.jsonl', 'reviews-seen.json']:
    raw = (HERE / name).read_bytes()
    assert not any(re.search(pattern, raw) for pattern in patterns), 'Potential credential detected; packaging stopped: ' + name
    assert not any(value in raw for value in secret_values), 'Private claim value detected; packaging stopped: ' + name
    current_hash = hashlib.sha256(raw).hexdigest()
    previous = audit['files'].get(name)
    prefix_ok = None
    if previous:
        prefix_ok = hashlib.sha256(raw[:previous['bytes']]).hexdigest() == previous['sha256']
        assert prefix_ok, 'Report snapshot is not a byte-identical prefix: ' + name
    dest = OUT / (Path(name).name + '.gz')
    with dest.open('wb') as f:
        with gzip.GzipFile(filename='', mode='wb', fileobj=f, mtime=0) as zipped:
            zipped.write(raw)
    with gzip.open(dest, 'rb') as f:
        assert hashlib.sha256(f.read()).hexdigest() == current_hash
    assert dest.stat().st_size < 50 * 1024 * 1024, 'Archive exceeds packaging size cap'
    manifest['files'][name] = {'archive': dest.relative_to(HERE).as_posix(), 'raw_bytes': len(raw),
                             'raw_sha256': current_hash, 'compressed_bytes': dest.stat().st_size,
                             'report_snapshot_prefix_verified': prefix_ok,
                             'report_snapshot_bytes': previous['bytes'] if previous else None,
                             'report_snapshot_sha256': previous['sha256'] if previous else None}
(OUT / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
print(json.dumps({'directory': str(OUT), **manifest}, ensure_ascii=False, indent=2))
