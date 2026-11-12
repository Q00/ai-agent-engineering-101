"""Read-only, bounded cursor export of the official public market ledger.

API verification found `after` is supported and each response is capped at 500;
`before` and `limit` did not constrain the result. Probe responses are preserved.
No market order, join, trade, review or other state-changing request is made.
"""
from datetime import datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path

import httpx

HERE = Path(__file__).resolve().parent
cutoff = (HERE / 'board.json').stat().st_mtime
pages = HERE / 'ledger-pages'
pages.mkdir(exist_ok=False)
cursor = 115000
manifest = {'source': 'https://market.ourolabs.ai/api/ledger', 'initial_after': cursor,
            'analysis_cutoff_utc': datetime.fromtimestamp(cutoff, timezone.utc).isoformat(),
            'max_pages': 80, 'read_only': True, 'pages': []}
try:
    with httpx.Client(timeout=20, trust_env=False) as client:
        for index in range(manifest['max_pages']):
            response = client.get(manifest['source'], params={'after': cursor})
            response.raise_for_status()
            data = response.json()
            assert isinstance(data, list) and data
            seqs = [row['seq'] for row in data]
            assert seqs == list(range(cursor + 1, seqs[-1] + 1)), seqs[:3]
            filename = f'after-{cursor}.json.gz'
            with gzip.open(pages / filename, 'wb') as stream:
                stream.write(response.content)
            manifest['pages'].append({'after': cursor, 'file': filename, 'rows': len(data),
                'first_seq': seqs[0], 'last_seq': seqs[-1],
                'sha256_response_bytes': hashlib.sha256(response.content).hexdigest(),
                'first_ts': data[0]['ts'], 'last_ts': data[-1]['ts']})
            cursor = seqs[-1]
            if max(row['ts'] for row in data) >= cutoff:
                manifest.update(passed=True, cutoff_reached=True)
                break
        else:
            raise RuntimeError('Bounded export did not reach analysis cutoff')
except BaseException as exc:
    manifest.update(passed=False, error=type(exc).__name__ + ': ' + str(exc)[:300])
    raise
finally:
    manifest['finished_at'] = datetime.now(timezone.utc).isoformat()
    (HERE / 'ledger-export-manifest.json').write_text(json.dumps(manifest, indent=2))
    print(json.dumps({key: value for key, value in manifest.items() if key != 'pages'}))
    print('Pages:', len(manifest['pages']), 'last_seq:', cursor)
