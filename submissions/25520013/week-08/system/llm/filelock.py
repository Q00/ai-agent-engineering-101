"""Single-host exclusive file lock + atomic JSON write for the LLM budget files.

locked(path): context manager. Holds a per-path threading.Lock (threads of one process) AND fcntl.flock(LOCK_EX)
on "<path>.lock" (processes on the same host). Read -> modify -> write must all happen inside it.
atomic_write_json(path, obj): tmp name ".<name>.<pid>.<thread id>.<uuid4 hex>.tmp" in the same directory, fsync,
os.replace. A failed write removes its tmp file.

PREMISE: one host, one local filesystem. flock is not a cross-host lock (NFS / two machines sharing a file are out
of scope); the budget is a single-host file-lock budget."""
import contextlib
import fcntl
import json
import os
import threading
import uuid
from pathlib import Path

_GUARD = threading.Lock()
_THREAD_LOCKS = {}


def _thread_lock(path):
    key = os.path.realpath(path)
    with _GUARD:
        return _THREAD_LOCKS.setdefault(key, threading.Lock())


@contextlib.contextmanager
def locked(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with _thread_lock(path):
        fd = os.open(str(path) + ".lock", os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


def atomic_write_json(path, obj):
    path = Path(path)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.{uuid.uuid4().hex}.tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(json.dumps(obj, ensure_ascii=False))
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp)
        raise
