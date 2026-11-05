"""OS leases and cooperative shutdown for the student's own market process."""
from contextlib import contextmanager
from datetime import datetime
import _thread
import os
import threading
import time
import uuid
from .state import ROOT, atomic_json, read_json, policy


@contextmanager
def lease(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    stream = path.open('a+b')
    locked = False
    try:
        stream.seek(0, 2)
        if not stream.tell():
            stream.write(b'0')
            stream.flush()
        stream.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            locked = True
        except OSError:
            raise RuntimeError('Another process already holds ' + path.name) from None
        yield
    finally:
        if locked:
            stream.seek(0)
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream, fcntl.LOCK_UN)
        stream.close()


def market_active(root=ROOT):
    try:
        with lease(root / '.market/runtime.lock'):
            return False
    except RuntimeError:
        return True


@contextmanager
def market_session(root=ROOT):
    with lease(root / '.market/runtime.lock'):
        session = dict(pid=os.getpid(), session=uuid.uuid4().hex, started_at=time.time(),
                       launch_id=os.environ.get('R3_LAUNCH_ID'))
        atomic_json(root / '.market/runtime.json', session)
        done = threading.Event()
        stop_at = policy(root).get('end_at')
        end = datetime.fromisoformat(stop_at).timestamp() if stop_at else float('inf')

        def shutdown():
            while not done.wait(1):
                request = read_json(root / '.market/runtime-stop.json', {})
                requested = request.get('pid') == session['pid'] and request.get('session') == session['session']
                if requested or time.time() >= end:
                    print('[runtime] graceful shutdown requested', flush=True)
                    _thread.interrupt_main()
                    return

        monitor = threading.Thread(target=shutdown, daemon=True)
        monitor.start()
        try:
            yield
        finally:
            done.set()
            monitor.join(timeout=2)


def request_stop(pid, root=ROOT, *, launch_id=None):
    session = read_json(root / '.market/runtime.json', {})
    # A Windows venv launcher can have a different PID from its Python child.
    if session.get('pid') == pid or launch_id and session.get('launch_id') == launch_id:
        atomic_json(root / '.market/runtime-stop.json', session)
