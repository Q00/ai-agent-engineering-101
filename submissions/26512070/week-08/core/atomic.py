"""Files that are never seen half-written, and appends that never interleave.

- write_json: write a temp file in the same folder, then os.replace() it over the target.
  A reader sees the old file or the new one, never a mix (the kit writes .market/ the same way).
- append_line: one line, appended under an OS file lock, so two processes that share a
  file cannot interleave bytes. The bus rule is still one writer per file (HARNESS.md 2.2);
  the lock is the safety net behind it.

On Windows a file that another process has open cannot be replaced or locked for a
moment, so both retry briefly before giving up.
"""
import contextlib
import json
import os
import tempfile
import threading
import time
from pathlib import Path

RETRIES = 150
RETRY_SLEEP = 0.02  # seconds; 150 tries is about three seconds (4 processes hammering one file took ~1 s per lock at worst)

if os.name == "nt":
    import msvcrt

    def _lock(f):
        f.seek(0)
        msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)

    def _unlock(f):
        f.seek(0)
        msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
else:
    import fcntl

    def _lock(f):
        fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _unlock(f):
        fcntl.flock(f.fileno(), fcntl.LOCK_UN)


def _retry(fn):
    for i in range(RETRIES):
        try:
            return fn()
        except (PermissionError, BlockingIOError, OSError) as e:
            if isinstance(e, FileNotFoundError) or i == RETRIES - 1:
                raise
            time.sleep(RETRY_SLEEP)


def write_json(path, obj):
    """Replace path with obj as JSON, atomically."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=1)
            f.flush()
            os.fsync(f.fileno())
        _retry(lambda: os.replace(tmp, path))
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def read_json(path, default=None):
    """The JSON in path, or default when the file does not exist. Raises ValueError on broken JSON."""
    path = Path(path)

    def read():
        with open(path, encoding="utf-8") as f:
            return json.load(f)

    try:
        return _retry(read)
    except FileNotFoundError:
        return default


def _append_locked(f, data):
    _retry(lambda: _lock(f))                 # byte 0 is the lock; in "a+b" mode writes go to the end
    try:
        f.seek(0, os.SEEK_END)
        f.write(data)
        f.flush()
    finally:
        _unlock(f)


def append_line(path, line: str):
    """Append one line (a trailing newline is added) under a file lock. Opens and closes the file each time;
    a process that appends often keeps an Appender instead."""
    if "\n" in line:
        raise ValueError("append_line takes a single line")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a+b") as f:
        _append_locked(f, (line + "\n").encode("utf-8"))


class Appender:
    """Keeps one file open for appending. Opening a file per line cost ~24 ms on Windows (500 events in
    11.8 s), which a decision that logs several events cannot afford."""

    def __init__(self):
        self.path, self.f = None, None
        # 11/9: the seller's threads share one writer; at the day change byte 0 of the new day's file stayed
        # locked and every reader of it (the meta cycle) failed for 9 h - one thread at a time
        self.mu = threading.Lock()

    def append(self, path, line: str):
        if "\n" in line:
            raise ValueError("append takes a single line")
        path = Path(path)
        with self.mu:
            if path != self.path:
                self._close()
                path.parent.mkdir(parents=True, exist_ok=True)
                self.f, self.path = open(path, "a+b"), path
            _append_locked(self.f, (line + "\n").encode("utf-8"))

    def close(self):
        with self.mu:
            self._close()

    def _close(self):
        if self.f is not None:
            self.f.close()
        self.path, self.f = None, None
