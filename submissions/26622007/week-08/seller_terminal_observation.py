"""Best-effort local callback observations, never server settlement receipts.

Future observations only: full unsalted SHA256 keys join local memory task/unit
strings. They are pseudonymous, not anonymous against a holder of those strings.
No text, checkout response, warranty or account data enters this file.
"""
import hashlib
import json
import math
import os
from pathlib import Path
import re
import time


def observe(info, outcome, why=None, *, state=None):
    """Append one small allowlisted event; failures must not affect the callback.

    state is the market directory, explicitly injectable for isolated callers.
    Like the existing market modules, default to MARKET_DIR or local .market.
    No retries, diagnostics containing arguments, external calls or state writes
    other than this append. A missing/invalid payload produces no observation.
    """
    try:
        deal = info['deal']
        task, unit, sku, price = info['task'], deal['unit'], deal['id'], info['price']
        if any(type(v) is not str or not 1 <= len(v) <= 2048 for v in (task, unit)):
            return False
        if type(sku) is not str or not re.fullmatch(r'G[0-9a-f]{8}', sku):
            return False
        if type(price) not in (int, float) or not math.isfinite(price) or not 0 <= price <= 1e15:
            return False
        if outcome == 'paid':
            local = 'paid_late' if info.get('late') is True else 'paid'
            reason = 'local_inventory_observation'
        elif outcome == 'unpaid':
            local = 'unpaid'
            reason = 'unknown'
            if type(why) is str:
                if why == 'expired before payment':
                    reason = 'expired'
                elif re.fullmatch(r'not paid within [0-9]+ s \(watched [0-9]+ min more\)', why):
                    reason = 'timeout'
                elif why == 'checkout cancelled or refused':
                    reason = 'checkout_cancel_or_refusal'
        else:
            return False
        event = dict(at=time.time(), sku=sku,
                     task_sha256=hashlib.sha256(task.encode()).hexdigest(),
                     unit_sha256=hashlib.sha256(unit.encode()).hexdigest(),
                     local_outcome=local, reason_enum=reason, price=price)
        data = (json.dumps(event, allow_nan=False, separators=(',', ':')) + '\n').encode()
        base = Path(state) if state is not None else Path(os.environ.get('MARKET_DIR') or Path(__file__).parent / '.market')
        path = base / 'measure' / 'seller_terminal.jsonl'
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            return os.write(fd, data) == len(data)
        finally:
            os.close(fd)
    except Exception:
        return False
