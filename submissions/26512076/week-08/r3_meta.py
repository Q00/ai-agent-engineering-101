"""Shared Round 3 API breaker and read-only checkout diagnostics."""
import argparse
from collections import Counter
from datetime import datetime, timedelta, timezone
import json
import math
import os
import re
from pathlib import Path
import time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import r3_common as c

KEY = 'meta_provider_v1'
TRANSIENT_LIMIT = 3
FAILURE_WINDOW = 60
BASE_PAUSE = 45
PROBE_LEASE = 50
OPTIONAL_ROLES = {'promotion', 'review'}
BUDGET_KEY = 'meta_model_budget_v1'


class ProviderPaused(RuntimeError):
    pass


def blocking_reason(role, state=None, now=None):
    state = c.get(KEY, {}) if state is None else state
    now = time.time() if now is None else now
    if state.get('until', 0) > now:
        return state.get('reason', 'provider_pause')
    if state.get('probe_until', 0) > now:
        return 'probe_in_progress'
    if state.get('until') and role in OPTIONAL_ROLES:
        return 'waiting_for_trading_probe'
    return None


def daily_budget(state, now):
    limit = max(1, int(os.getenv('R3_DAILY_MODEL_LIMIT', '500')))
    reset = state.get('reset_at', 0)
    counts = state.get('calls_by_role', {}) if reset > now else {}
    return {
        'limit': limit, 'used': sum(counts.values()), 'calls_by_role': counts,
        'reset_at': reset if reset > now else next_pacific_reset(now),
    }


def budget_block(role, budget):
    share = .6 if role in OPTIONAL_ROLES else .8 if role == 'buyer' else .98
    if budget['used'] >= max(1, math.floor(budget['limit'] * share)):
        return 'local_daily_budget'
    return None


def claim_call(role):
    """Lease one probe across seller, buyer and restock subprocesses."""
    now = time.time()
    with c.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT v FROM kv WHERE k=?', (KEY,)).fetchone()
        state = json.loads(row[0]) if row else {}
        row = db.execute('SELECT v FROM kv WHERE k=?', (BUDGET_KEY,)).fetchone()
        budget = daily_budget(json.loads(row[0]) if row else {}, now)
        reason = blocking_reason(role, state, now) or budget_block(role, budget)
        if reason is None:
            budget['calls_by_role'][role] = budget['calls_by_role'].get(role, 0) + 1
            db.execute('INSERT OR REPLACE INTO kv VALUES(?,?)', (BUDGET_KEY, json.dumps(budget)))
        if reason is None and state.get('until'):
            state['probe_until'] = now + PROBE_LEASE
            state['epoch'] = state.get('epoch', 0) + 1
            db.execute('INSERT OR REPLACE INTO kv VALUES(?,?)', (KEY, json.dumps(state)))
    if reason:
        raise ProviderPaused(reason)
    if state.get('until'):
        c.audit('meta', 'provider_probe', source_role=role)
    return state.get('epoch', 0)


def daily_quota_error(body):
    """Recognize provider quota IDs or an explicit daily limit message."""
    if isinstance(body, list):
        return any(daily_quota_error(item) for item in body)
    if not isinstance(body, dict):
        return False
    for field in ('quotaId', 'quotaMetric', 'quota_id', 'quota_metric', 'message', 'description'):
        value = body.get(field)
        if isinstance(value, str) and re.search(
                r'per[ _-]*day|requests\s*/\s*day|daily[ _-]*(?:quota|limit)|\brpd\b', value, re.I):
            return True
    return any(daily_quota_error(body.get(field)) for field in ('error', 'details', 'violations'))


def next_pacific_reset(now):
    try:
        pacific = ZoneInfo('America/Los_Angeles')
    except ZoneInfoNotFoundError:
        # Standard Windows Python may lack an IANA database. Use current US DST rules.
        def midnight(day):
            march = day.replace(month=3, day=1)
            november = day.replace(month=11, day=1)
            spring = march + timedelta(days=(6 - march.weekday()) % 7 + 7)
            fall = november + timedelta(days=(6 - november.weekday()) % 7)
            hour = 7 if spring < day <= fall else 8
            return datetime(day.year, day.month, day.day, hour, tzinfo=timezone.utc).timestamp()

        day = datetime.fromtimestamp(now - 8 * 3600, timezone.utc).date() + timedelta(days=1)
        return midnight(day if midnight(day) > now else day + timedelta(days=1))
    day = datetime.fromtimestamp(now, pacific).date() + timedelta(days=1)
    return datetime(day.year, day.month, day.day, tzinfo=pacific).timestamp()


def record_call(role, epoch, outcome, retry_after=None, *, daily_quota=False):
    now = time.time()
    event = None
    with c.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT v FROM kv WHERE k=?', (KEY,)).fetchone()
        state = json.loads(row[0]) if row else {}
        current = state.get('epoch', 0)
        if epoch != current and outcome != 'rate_limit':
            return
        if outcome == 'ok':
            if not state.get('until'):
                return
            state = {'epoch': current + 1}
            event = 'provider_recovered'
        elif outcome == 'rate_limit':
            try:
                pause = float(retry_after)
            except (TypeError, ValueError):
                pause = 60
            pause = max(10, pause) if math.isfinite(pause) else 60
            daily_active = state.get('reason') == 'daily_quota' and state.get('until', 0) > now
            until = max(state.get('until', 0), now + pause)
            if daily_quota:
                until = max(until, next_pacific_reset(now) + 5)
            state.update(epoch=current + 1,
                         reason='daily_quota' if daily_quota or daily_active else 'rate_limit',
                         until=until, probe_until=0, failures=[])
            event = 'provider_paused'
        else:
            is_probe = bool(state.get('until'))
            if outcome != 'transient' and not is_probe:
                return
            failures = [ts for ts in state.get('failures', []) if now - ts <= FAILURE_WINDOW]
            failures.append(now)
            state['failures'] = failures
            if is_probe or len(failures) >= TRANSIENT_LIMIT:
                pause = min(180, max(BASE_PAUSE, state.get('pause', 0) * 2))
                state.update(epoch=current + 1, reason='provider_failure',
                             until=now + pause, probe_until=0, pause=pause)
                event = 'provider_paused'
        db.execute('INSERT OR REPLACE INTO kv VALUES(?,?)', (KEY, json.dumps(state)))
    if event:
        c.audit('meta', event, source_role=role, reason=state.get('reason'), until=state.get('until'))


def tail_rows(path, limit=512 * 1024):
    try:
        with path.open('rb') as stream:
            size = stream.seek(0, 2)
            start = max(0, size - limit)
            stream.seek(start)
            if start:
                stream.readline()
            for line in stream:
                try:
                    row = json.loads(line)
                except (ValueError, UnicodeError):
                    continue
                if isinstance(row, dict):
                    yield row
    except OSError:
        return


def checkout_status(path, now=None):
    """Classify mature signed receipts by the latest observable checkout stage."""
    now = time.time() if now is None else now
    by_task = {}
    stock_conflicts = set()
    observed = False
    terminal = {'checkout_completed', 'checkout_canceled', 'checkout_rejected', 'checkout_exception'}
    for row in tail_rows(path):
        at, task, event = c.stamp(row.get('time')), row.get('task'), row.get('event')
        if not 0 <= now - at <= 1800:
            continue
        observed = True
        if not isinstance(task, str) or not task:
            continue
        if (event == 'checkout_rejected' and row.get('reason') in ('unit_missing_or_held', 'out_of_stock')
                and now - at <= 900):
            stock_conflicts.add(task)
        if event in terminal or event in ('receipt_observed', 'checkout_request', 'checkout_created'):
            by_task.setdefault(task, {})[event] = at
    counts = Counter()
    for events in by_task.values():
        signed_at = events.get('receipt_observed')
        if signed_at is None:
            continue
        if 'checkout_completed' in events:
            counts['settled'] += 1
        elif now - signed_at < 180:
            counts['pending'] += 1
        elif any(name in events for name in terminal):
            counts['canceled_or_rejected'] += 1
        elif 'checkout_created' in events:
            counts['checkout_created_no_payment_confirmation'] += 1
        elif 'checkout_request' in events:
            counts['checkout_request_no_result'] += 1
        else:
            counts['no_checkout_observed'] += 1
    return {'trace_recent': observed, 'last_30_minutes': dict(counts),
            'stock_conflict_tasks_15m': len(stock_conflicts)}


def trial_block_reason(checkout):
    if checkout['stock_conflict_tasks_15m'] >= 3:
        return 'repeated_checkout_stock_conflicts'
    if time.time() - c.get('recent_ledger_fetched_at', 0) > 180:
        return 'ledger_snapshot_stale'
    return None


def supply_wait_until(inv, hint, now):
    """Only defer when a server snapshot or a 429 hint specifies a future window."""
    restock = inv.get('restock') or {}
    opens, closes = c.stamp(restock.get('opens_at')), c.stamp(restock.get('closes_at'))
    if restock.get('open') is True and opens <= now < closes:
        return 0
    if restock.get('open') is False and opens > now:
        return opens
    return c.stamp(hint.get('opens_at'))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Show provider and checkout status without making API calls.')
    parser.add_argument('--state', type=Path, default=c.STATE)
    args = parser.parse_args()
    state = c.get(KEY, {})
    print(json.dumps({'provider_pause_reason': blocking_reason('buyer', state),
                      'pause_remaining_seconds': max(0, round(state.get('until', 0) - time.time())),
                      'provider_probe_pending': bool(state.get('until')),
                      'daily_model_budget': daily_budget(c.get(BUDGET_KEY, {}), time.time()),
                      'checkout': checkout_status(args.state / 'seller-checkout.jsonl')}, indent=2))
