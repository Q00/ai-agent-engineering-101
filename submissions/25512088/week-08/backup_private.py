"""Snapshot private market files; stop both agents first for a final migration."""
import argparse
import os
from pathlib import Path
import sqlite3
import tempfile
import zipfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    base = Path(__file__).resolve().parent
    root = base.parents[2]
    output = args.output.expanduser().resolve()
    if output.is_relative_to(root):
        parser.error('Choose an output outside the Git repository.')
    files = [f for f in base.rglob('*') if f.is_file() and
             ('.market' in f.relative_to(base).parts or 'logs' in f.relative_to(base).parts
              or f.suffix == '.pem' or f.name == '.env')]
    output.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'wb') as raw, tempfile.TemporaryDirectory() as tmp:
        with zipfile.ZipFile(raw, 'w', zipfile.ZIP_DEFLATED) as archive:
            for index, f in enumerate(sorted(files)):
                if f.name.endswith(('-wal', '-shm', '-journal')):
                    continue
                with f.open('rb') as stream:
                    is_db = stream.read(16) == b'SQLite format 3\x00'
                if is_db:
                    snapshot = Path(tmp) / str(index)
                    with sqlite3.connect(str(f)) as src:
                        with sqlite3.connect(snapshot) as dst:
                            src.backup(dst)
                            if dst.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                                raise RuntimeError('SQLite backup integrity failure')
                    archive.write(snapshot, f.relative_to(root))
                else:
                    archive.write(f, f.relative_to(root))
    with zipfile.ZipFile(output) as archive:
        if archive.testzip() is not None:
            raise RuntimeError('ZIP integrity failure')
        print(f'Created {output} ({len(archive.namelist())} files)')


if __name__ == '__main__':
    main()
