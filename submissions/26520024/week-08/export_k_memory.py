"""Export K's original memory as a consistent, private, read-only SQLite backup.

Run with system python3 on daintlabK. Does not import the market agent, read
credentials, change the shared DB pointer, or start/stop any agent.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import sqlite3
import tempfile

ROOT = Path(__file__).resolve().parent
DEFAULT_SOURCE = Path('/home/uichan/.local/state/ai-agent-engineering-101/ae668c97a60065fc/r3-memory.sqlite3')


def export_memory(source, output_root):
    source, output_root = Path(source).resolve(), Path(output_root).resolve()
    if not source.is_file():
        raise FileNotFoundError('Original memory database does not exist at the supplied path')
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    target = output_root / stamp
    target.mkdir(parents=True, mode=0o700)
    target.chmod(0o700)
    # Build and validate on a local disk first: never run a WAL database on NAS.
    with tempfile.TemporaryDirectory(prefix='week08-k-memory-') as temporary:
        local = Path(temporary) / 'r3-memory.sqlite3'
        with sqlite3.connect(source.as_uri() + '?mode=ro', uri=True) as src:
            src.execute('PRAGMA query_only=ON')
            with sqlite3.connect(local) as dst:
                src.backup(dst)
                dst.execute('PRAGMA journal_mode=DELETE')
                if dst.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                    raise RuntimeError('Exported memory failed the SQLite integrity check')
                counts = dict(dst.execute('SELECT kind,COUNT(*) FROM events GROUP BY kind'))
                first, last = dst.execute('SELECT MIN(ts),MAX(ts) FROM events').fetchone()
        destination = target / 'r3-memory.sqlite3'
        shutil.copyfile(local, destination)
        destination.chmod(0o600)
    result = {'host': socket.gethostname(), 'source': str(source),
              'backup': str(destination), 'created_utc': stamp, 'integrity': 'ok',
              'sha256': hashlib.sha256(destination.read_bytes()).hexdigest(),
              'event_counts': counts, 'first_event_ts': first, 'last_event_ts': last,
              'source_opened_read_only': True, 'backup_journal_mode': 'delete'}
    manifest = target / 'manifest.json'
    manifest.write_text(json.dumps(result, indent=2) + '\n')
    manifest.chmod(0o600)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=DEFAULT_SOURCE)
    parser.add_argument('--output-root', type=Path, default=ROOT / '.market/k-evidence')
    args = parser.parse_args()
    if socket.gethostname() != 'daintlabK':
        parser.error('Run this export on daintlabK; J cannot access K local storage')
    os.umask(0o077)
    print(json.dumps(export_memory(args.source, args.output_root), indent=2))


if __name__ == '__main__':
    main()
