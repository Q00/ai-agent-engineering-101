"""Local correlation for decisions, applied actions and authoritative outcomes.

This module does not choose prices or count a receipt as payment. ContextVars
isolate concurrent seller tasks and propagate into the model worker thread.
The original role-specific JSONL files remain the source of truth.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from functools import lru_cache
import hashlib
import json
from pathlib import Path
import uuid


VERSION = 'decision-audit-v1'
_current = ContextVar('market_decision_audit', default=None)
LINKS = ('role', 'episode_id', 'decision_id', 'parent_decision_id', 'selection_id',
         'attempt_id', 'attempt_index', 'order', 'task_id', 'shop', 'shop_name',
         'sku', 'score_version', 'policy_version', 'source_commit', 'model')


@lru_cache(maxsize=1)
def release_commit():
    """Trust a frozen release identifier only when this module's hash matches."""
    source=Path(__file__).resolve()
    try:
        manifest=json.loads((source.parent.parent/'release.json').read_text())
        if manifest.get('sha256',{}).get(source.name)==hashlib.sha256(source.read_bytes()).hexdigest():
            return manifest.get('source_commit')
    except (OSError,ValueError,TypeError):
        pass
    return None


@contextmanager
def scope(role, writer, **fields):
    token = _current.set({'role': role, 'writer': writer, 'source_commit':release_commit(), **fields})
    try:
        yield
    finally:
        _current.reset(token)


def bind(**fields):
    current = _current.get()
    if current is not None:
        _current.set({**current, **fields})


def links():
    current = _current.get() or {}
    return {key: current[key] for key in LINKS if current.get(key) is not None}


def enrich(fields):
    """Never attach the current customer's decision to another task's event."""
    context = links()
    if fields.get('outcome_link_status') in ('ambiguous','unmatched','legacy_or_unmatched'):
        return fields
    if any(fields.get(key) is not None and context.get(key) is not None
           and fields[key] != context[key] for key in ('task_id', 'order', 'episode_id', 'attempt_id')):
        return fields
    return {**context, **fields}


def begin(phase, **fields):
    current = _current.get()
    if current is None:
        return None
    decision_id = uuid.uuid4().hex
    _current.set({**current, **fields, 'decision_id': decision_id,
                  'phase': phase, 'guards': [], 'proposal': None})
    return decision_id


def _write(event, **fields):
    current = _current.get()
    if current is not None and current.get('decision_id'):
        current['writer'](event, **links(), audit_version=VERSION,
                          phase=current.get('phase'), **fields)


def proposed(action, *, source='model', reason=None):
    current = _current.get()
    if current is None or not current.get('decision_id'):
        return
    _current.set({**current, 'proposal': dict(action)})
    _write('decision_proposed', proposed=dict(action), source=source, reason=reason)


def guard(reason, **details):
    current = _current.get()
    if current is not None and current.get('decision_id'):
        entry = {'reason': reason, **details}
        _current.set({**current, 'guards': [*current.get('guards', []), entry]})
        _write('decision_guard', guard=entry)


def executed(action, *, status='sent', reason=None, **fields):
    """Call after applying a choice or successfully sending a protocol action.

    A failed send is recorded with status=failed; it is never labelled sent.
    Selection-only application uses status=applied, before network execution.
    """
    current = _current.get() or {}
    _write('decision_executed', executed={'action': action, **fields},
           execution_status=status, reason=reason,
           proposed=current.get('proposal'), guards=current.get('guards', []))


def buyer_outcome_links(history, *, order, shop, sku, price=None, task_id=None):
    """Join only one exact receipt-bearing attempt; ambiguity stays unlinked.

    Payments do not always carry an A2A task. In that case order, transport,
    SKU and exact receipt price must uniquely identify a new audited attempt.
    Names, product similarity and a nearest timestamp are never join keys.
    """
    matches = {}
    for row in history:
        if (row.get('event') != 'negotiation' or row.get('result') != 'receipt_pending'
                or not row.get('attempt_id') or row.get('order') != order
                or row.get('shop') != shop or row.get('sku') != sku):
            continue
        if task_id is not None:
            if row.get('task_id') != task_id:
                continue
        elif price is None or row.get('receipt_price') != price:
            continue
        if price is not None and row.get('receipt_price') != price:
            continue
        matches[row['attempt_id']] = row
    if len(matches) != 1:
        return {'outcome_link_status': 'ambiguous' if matches else 'unmatched'}
    row = next(iter(matches.values()))
    return {**{key: row[key] for key in LINKS if row.get(key) is not None},
            'outcome_link_status': 'exact_task' if task_id is not None else 'unique_receipt_attempt'}


def journeys(rows):
    """Rebuild complete episodes without editing raw evidence or guessing failure.

    Each outcome retains its own attempt/decision links. The episode groups all
    decisions contributing to a trade, rather than crediting only its last turn.
    Historical events lacking decision IDs are not invented model decisions.
    """
    groups = {}
    for row in rows:
        episode = row.get('episode_id')
        if not episode:
            continue
        group = groups.setdefault(episode, {'episode_id': episode, 'role': row.get('role'),
                    'decisions': {}, 'outcomes': [], 'status': 'unknown'})
        decision = row.get('decision_id')
        event = row.get('event')
        if decision and event in ('decision_proposed', 'decision_guard', 'decision_executed'):
            item = group['decisions'].setdefault(decision, {'decision_id': decision, 'events': []})
            item['events'].append(row)
        if event in ('settled', 'payment', 'negotiation', 'negotiation_expired',
                     'rejected', 'cancelled', 'receipt_signed', 'checkout_unpaid',
                     'portfolio_approval','portfolio_execution','portfolio_intent_reconciled'):
            group['outcomes'].append(row)
    result = []
    for group in groups.values():
        outcomes = group['outcomes']
        if group['episode_id'].startswith('procurement:'):
            group['kind']='procurement'
            reconciled=[r for r in outcomes if r['event']=='portfolio_intent_reconciled']
            group['status']=('resale_observed' if any(r.get('source')=='settled' and r.get('ledger') is not None for r in reconciled)
                             else 'arrival_observed' if reconciled else
                             'emitted_pending' if any(r.get('emitted_units',0)>0 for r in outcomes) else 'blocked_or_held')
            group['decisions']=list(group['decisions'].values())
            if group['decisions']:result.append(group)
            continue
        paid = [r for r in outcomes if (r['event'] == 'settled'
                or r['event'] == 'payment' and r.get('paid') is True) and r.get('ledger') is not None]
        if paid:
            group['status'] = 'paid'
            group['ledgers'] = sorted({r['ledger'] for r in paid})
        elif any(r['event'] == 'payment' and r.get('paid') is False for r in outcomes):
            group['status'] = 'checkout_refused'
        elif any(r['event'] == 'receipt_signed' or r.get('result') == 'receipt_pending' for r in outcomes):
            group['status'] = 'receipt_pending'
        elif any(r['event'] == 'negotiation_expired' for r in outcomes):
            group['status'] = 'expired_unknown'
        elif any(r['event'] == 'rejected' for r in outcomes):
            group['status'] = 'rejected'
        elif any(r['event'] == 'cancelled' for r in outcomes):
            group['status'] = 'cancelled_unknown'
        group['decisions'] = list(group['decisions'].values())
        if group['decisions']:
            result.append(group)
    return result
