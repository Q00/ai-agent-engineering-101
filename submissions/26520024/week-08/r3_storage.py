"""Keep SQLite and its locks on one host-local filesystem, separate from kit files."""
from contextlib import contextmanager
import fcntl
import hashlib
import os
from pathlib import Path
import socket
import subprocess
import tempfile

from r3_common import atomic_json, read_json, state_dir

DATABASES = ('r3-memory.sqlite3', 'r3_seller.sqlite3', 'r3_seller_requests.sqlite3')
LOCAL_FILESYSTEMS = {'ext2', 'ext3', 'ext4', 'xfs', 'btrfs', 'zfs', 'overlay'}


def require_local(path):
    probe = Path(path).resolve()
    while not probe.exists():
        probe = probe.parent
    try:
        result = subprocess.run(['findmnt', '-T', str(probe), '-n', '-o', 'FSTYPE'],
                                capture_output=True, text=True, timeout=5, check=True)
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError('Cannot verify local SQLite storage with findmnt') from exc
    if result.stdout.strip() not in LOCAL_FILESYSTEMS:
        raise RuntimeError('SQLite requires persistent host-local storage; use the guarded kit launcher '
                           'and R3_DB_DIR if home is not local (NFS/CIFS/tmpfs are rejected)')


def database_dir(md, *, write=False):
    """Resolve only. Missing/moved configured storage is never silently recreated."""
    md = state_dir(md)
    pointer = read_json(md / 'db-location.json')
    path = md
    if pointer is not None:
        if (not isinstance(pointer, dict) or pointer.get('host') != socket.gethostname() or
                not isinstance(pointer.get('path'), str) or not Path(pointer['path']).is_absolute()):
            raise RuntimeError('Database location is invalid or belongs to another host')
        path = Path(pointer['path']).resolve()
        owner = read_json(path / 'db-owner.json')
        if not path.is_dir() or owner != {'state_directory': str(md), 'host': socket.gethostname()}:
            raise RuntimeError('Configured database directory is missing or belongs to another agent')
    if write:
        require_local(path)
    return path


@contextmanager
def exclusive_lock(path):
    """Fail rather than start a second kit against the same local accounting state."""
    with Path(path).open('a') as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError('Another process owns this market database lock') from exc
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


def configure_database(md, destination=None):
    """Live launcher only: persist a host-bound pointer; never migrate/delete existing DBs."""
    md = state_dir(md)
    if (md / 'db-location.json').exists():
        path = database_dir(md, write=True)
        if destination and Path(destination).expanduser().resolve() != path:
            raise RuntimeError('Changing an existing database location requires an offline migration')
        return path
    suffix = hashlib.sha256(str(md).encode()).hexdigest()[:16]
    path = (Path(destination).expanduser() if destination else
            Path.home() / '.local' / 'state' / 'ai-agent-engineering-101' / suffix).resolve()
    if path == md or any((md / (name + tail)).exists()
                         for name in DATABASES for tail in ('', '-wal', '-shm', '-journal')):
        raise RuntimeError('Existing kit-directory databases require an explicit offline migration; not copied')
    require_local(path)
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.stat().st_mode & 0o077:
        raise RuntimeError('Database directory must be private (mode 0700) because it contains checkout tokens')
    with exclusive_lock(path / 'db-config.lock'):
        owner = {'state_directory': str(md), 'host': socket.gethostname()}
        previous = read_json(path / 'db-owner.json')
        if previous not in (None, owner):
            raise RuntimeError('Database destination is already assigned to another agent')
        if previous is None and any((path / name).exists() for name in DATABASES):
            raise RuntimeError('Unowned databases exist at destination; refusing to adopt them')
        atomic_json(path / 'db-owner.json', owner)
        md.mkdir(parents=True, exist_ok=True)
        # Publish without replacement: concurrent launches with different R3_DB_DIR
        # values must not split the same agent's reservations across two databases.
        with tempfile.TemporaryDirectory(prefix='.db-location-', dir=md) as temporary:
            staged = Path(temporary) / 'location.json'
            atomic_json(staged, {'path': str(path), 'host': socket.gethostname()})
            try:
                os.link(staged, md / 'db-location.json')
            except FileExistsError:
                if database_dir(md, write=True) != path:
                    raise RuntimeError('Another launch configured a different database location')
    return path
