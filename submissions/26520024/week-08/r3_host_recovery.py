"""Explicit, offline partial recovery after a host-local DB becomes unavailable.

Never claims to restore old checkout sessions or request responses. Old units
remain held; projected memories require matching original paid evidence before
being used as purchase history. No market writes or model calls occur here.
"""
import argparse
from collections import Counter
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import socket
import sqlite3

from r3_common import atomic_json, parse_json, read_json, state_dir
from r3_memory import Memory, packed
from r3_seller import Reservations
from r3_seller_lifecycle import RequestJournal
from r3_storage import DATABASES, configure_database, require_local


def log_records(paths):
    for path in paths:
        with Path(path).open(encoding='utf-8', errors='replace') as stream:
            for line in stream:
                start = line.find('{')
                if start < 0:
                    continue
                try:
                    row = parse_json(line[start:])
                    stamp = datetime.fromisoformat(row['time']).timestamp()
                except (ValueError, KeyError, TypeError):
                    continue
                if isinstance(row, dict):
                    yield row, stamp


def contexts(state):
    for field in ('personal_records', 'memory'):
        value = state.get(field)
        if isinstance(value, dict):
            yield value
    for candidate in state.get('candidates', []):
        if isinstance(candidate, dict) and isinstance(candidate.get('personal_records'), dict):
            yield candidate['personal_records']


def restore_memories(memory, source, logs):
    source = Path(source)
    payments = {}
    for line in (source / 'payments.jsonl').read_text().splitlines():
        row = parse_json(line)
        if isinstance(row.get('order'), str) and type(row.get('paid')) is bool:
            payments[row['order']] = row
    dates = {}
    projected = []
    counts = Counter()
    for row, stamp in log_records(logs):
        if isinstance(row.get('order'), str):
            dates[row['order']] = min(dates.get(row['order'], stamp), stamp)
        if row.get('event') == 'r3_seller_action':
            data = {k: row[k] for k in ('task_id', 'unit', 'sku', 'action', 'price') if k in row}
            counts['seller_action'] += memory.record('seller_action',
                'recovered:' + hashlib.sha256(packed(data).encode()).hexdigest(), data, ts=stamp)
        if row.get('event') == 'decision_input' and isinstance(row.get('state'), dict):
            for context in contexts(row['state']):
                for event in context.get('recent', []) + context.get('pending', []):
                    if isinstance(event, dict) and isinstance(event.get('data'), dict):
                        projected.append(event)
    fallback = (source / 'payments.jsonl').stat().st_mtime
    paid_by_ledger = {p['ledger']: p for p in payments.values()
                      if p.get('paid') is True and type(p.get('ledger')) is int}
    catalog = read_json(source / 'catalog.json', {})
    for key, payment in payments.items():
        data = dict(payment, recovered_from='original_payments_jsonl',
                    timestamp_basis='order_log' if key in dates else 'file_mtime_upper_bound')
        counts['payment'] += memory.record('payment', key, data, ts=dates.get(key, fallback))
    # Prefer the richest projection. These are decision memories, never usable
    # signed receipts or a substitute for the lost checkout database.
    projected.sort(key=lambda e: len(packed(e)), reverse=True)
    for event in projected:
        kind, key, data = event.get('kind'), event.get('key'), dict(event['data'])
        stamp = event.get('ts')
        if not isinstance(key, str) or type(stamp) not in (int, float):
            continue
        settled, receipt = data.get('settled', {}), data.get('receipt', {})
        if not settled and kind == 'settlement':
            settled = {k: data[k] for k in ('ledger', 'sku', 'value', 'grade', 'expires_at',
                                           'warranty_hours', 'unit', 'name') if k in data}
        pay = payments.get(str(data.get('order')), paid_by_ledger.get(settled.get('ledger'), {}))
        price = receipt.get('price', data.get('price'))
        confirmed = (kind == 'settlement' and pay.get('paid') is True and
                     type(settled.get('ledger')) is int and pay.get('ledger') == settled['ledger'] and
                     str(settled['ledger']) == key and pay.get('price') == price and
                     pay.get('item') == settled.get('sku') and
                     data.get('shop_url', pay.get('shop')) == pay.get('shop'))
        if confirmed:
            settled.setdefault('name', catalog.get(settled['sku'], {}).get('name'))
            data.update(order=pay['order'], shop_url=pay['shop'], sku=settled['sku'],
                        unit=settled.get('unit'), settled=settled,
                        receipt=receipt or {'price': price, 'item': settled['sku']},
                        observed_at=data.get('observed_at', stamp),
                        recovery_scope='paid_corroborated_prompt_projection_not_signed_checkout')
            data.pop('review', None)
            counts['settlement'] += memory.record('settlement', key, data, ts=stamp)
        else:
            data.update(recovered_kind=kind,
                        recovery_scope='partial_prompt_projection_not_payment_or_checkout_proof')
            counts['partial_context'] += memory.record('recovered_context', str(kind)+':'+key, data, ts=stamp)
    broken = source / 'broken.jsonl'
    if broken.is_file():
        for line in broken.read_text().splitlines():
            row = parse_json(line)
            stamp = row.get('broke_at')
            if isinstance(row.get('name'), str) and type(stamp) in (int, float):
                data = dict(row, candidate_ledgers=[], attribution='unresolved_name_only')
                counts['broken'] += memory.record('broken', hashlib.sha256(packed(row).encode()).hexdigest(), data, ts=stamp)
    sent = source / 'outbox.sent.jsonl'
    if sent.is_file():
        for line in sent.read_text().splitlines():
            row = parse_json(line)
            body = row.get('sent', {})
            refs = body.get('refs')
            if row.get('status') != 200 or not isinstance(refs, list) or not refs:
                continue
            key = ('purchase-' + str(refs[0]) if len(refs) == 1 else
                   'aggregate-' + hashlib.sha256(packed(refs).encode()).hexdigest()[:20])
            counts['forum_queued'] += memory.record('forum_queued', key, {'refs': refs, 'recovered_from': 'sent_outbox'}, ts=sent.stat().st_mtime)
    memory.reconcile()
    return dict(counts)


def hold_old_units(reservations, inventory, report):
    units = {u['unit']: u for u in inventory.get('units', [])}
    for row in report.get('units', []):
        if row.get('unit') and row.get('status') in ('settled', 'signed_pending', 'quarantined'):
            units.setdefault(row['unit'], row)
    with reservations.transaction() as db:
        for unit, row in units.items():
            data = {'sku': row.get('sku'), 'recovery_hold': True,
                    'original_status': row.get('status', 'unknown'),
                    'reason': 'Original host DB unavailable; no checkout or cancellation reconstructed'}
            db.execute('INSERT INTO deals VALUES (?,?,?,?,?,?)',
                       ('host-recovery:'+unit, unit, 'recovery_hold', 0, None, packed(data)))
    return len(units)


def recover(md, destination, backup):
    md, destination, backup = state_dir(md), Path(destination).resolve(), Path(backup).resolve()
    pointer = read_json(md / 'db-location.json')
    original = read_json(backup / 'original-market/db-location.json')
    if pointer != original or not isinstance(pointer, dict) or pointer.get('host') == socket.gethostname():
        raise RuntimeError('Recovery requires the unchanged backed-up foreign-host pointer')
    if Path(pointer['path']).exists():
        raise RuntimeError('Original storage is accessible; use a real offline migration instead')
    if destination.exists():
        raise RuntimeError('Use a new, empty recovery database destination')
    require_local(destination)
    stage = backup / 'rebuilt-state'
    stage.mkdir(mode=0o700)
    configure_database(stage, destination)
    source = backup / 'original-market'
    for name in ('payments.jsonl', 'broken.jsonl', 'catalog.json', 'forum.json'):
        path = source / name
        if path.is_file():
            (stage / name).write_bytes(path.read_bytes())
    with Memory(stage) as memory:
        counts = restore_memories(memory, source,
                                 [backup/'original-logs/market-kit.log.1', backup/'original-logs/market-kit.log'])
    held = hold_old_units(Reservations(stage), read_json(source/'inventory.json', {}),
                          read_json(source/'seller-stock-status.json', {}))
    RequestJournal(stage)
    for name in DATABASES:
        with sqlite3.connect(destination / name) as db:
            if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise RuntimeError('Recovered database integrity check failed')
    result = {'mode': 'partial_recovery_not_original_database', 'host': socket.gethostname(),
              'database_directory': str(destination), 'backup': str(backup),
              'restored_events': counts, 'held_old_units_including_sold_tombstones': held,
              'missing': ['original checkout sessions', 'original request responses', 'unlogged memories'],
              'old_pointer': pointer}
    atomic_json(destination/'recovery-manifest.json', result)
    # All validation completes before the operational pointer changes.
    if read_json(md/'db-location.json') != original:
        raise RuntimeError('Operational pointer changed during recovery')
    atomic_json(destination/'db-owner.json', {'state_directory': str(md), 'host': socket.gethostname()})
    atomic_json(md/'db-location.json', {'path': str(destination), 'host': socket.gethostname()})
    atomic_json(md/'host-recovery.json', result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state', required=True)
    parser.add_argument('--db-dir', required=True)
    parser.add_argument('--backup', required=True)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    if not args.apply:
        parser.error('Explicit --apply is required for partial recovery')
    os.umask(0o077)
    print(json.dumps(recover(args.state, args.db_dir, args.backup), indent=2))


if __name__ == '__main__':
    main()
