"""Future local send phases, not delivery acknowledgments or settlement.

Full SHA256 keys are pseudonyms joinable by holders of the original keys.
Never retain receipt, response, exception text, terms, price or account data.
"""
import asyncio
import contextlib
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import time
import uuid


class SendObservation:
    def __init__(self, task, unit, sku):
        self.keys = None
        try:
            if any(type(v) is not str or not 1 <= len(v) <= 2048 for v in (task, unit)):
                return
            if type(sku) is not str or not re.fullmatch(r'G[0-9a-f]{8}', sku):
                return
            self.keys = dict(task_sha256=hashlib.sha256(task.encode()).hexdigest(),
                             unit_sha256=hashlib.sha256(unit.encode()).hexdigest(),
                             sku=sku, attempt=uuid.uuid4().hex)
        except Exception:
            pass

    def _append(self, phase, error_enum='none'):
        try:
            if (self.keys is None or phase not in ('artifact_before', 'artifact_returned', 'artifact_raised',
                                                  'complete_before', 'complete_returned', 'complete_raised')
                    or error_enum not in ('none', 'exception', 'cancelled')):
                return
            row = dict(self.keys, at=time.time(), phase=phase, error_enum=error_enum)
            data = (json.dumps(row, allow_nan=False, separators=(',', ':')) + '\n').encode()
            base = Path(os.environ.get('MARKET_DIR') or Path(__file__).parent / '.market')
            path = base / 'measure' / 'seller_send.jsonl'
            path.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
            try:
                if not stat.S_ISREG(os.fstat(fd).st_mode):
                    return
                os.fchmod(fd, 0o600)
                os.write(fd, data)
            finally:
                os.close(fd)
        except Exception:
            pass

    @contextlib.contextmanager
    def phase(self, name):
        self._append(name + '_before')
        try:
            yield
        except asyncio.CancelledError:
            self._append(name + '_raised', 'cancelled')
            raise
        except Exception:
            self._append(name + '_raised', 'exception')
            raise
        else:
            self._append(name + '_returned')
