"""Append-only seller evidence and compact context for the next model call.

A signed receipt is deliberately distinct from a settled sale. Settlement is
observed from the kit log, whose [acp] line includes the shipped unit and ledger
number. No receipt-only result is counted as revenue or conversion.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Iterator, Set
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
from typing import Any
import decision_audit
from context_compaction import (MAX_TURNS, SELLER_MEMORY_BYTES, bounded, hours_left,
                                price_ranges, select_cases, text)


EVENTS = Path("logs/shop-events.jsonl")
KIT_LOG = Path("logs/market-kit.log")
CURSOR = Path("state/settlement-cursor.json")
SETTLED = re.compile(r"\[acp\] settled .*? at (?P<price>\d+), ledger #(?P<ledger>\d+), "
                     r"unit (?P<unit>[^\s,]+) grade (?P<grade>[ABC])")


# record() writes this prefix. Older or differently formatted rows still use
# the JSON decoder, including rows with a numeric timestamp.
EVENT_PREFIX = re.compile(rb'^\{"at":"[^"\\]*","event":"([a-z][a-z0-9_]*)"')
RECEIPT_EVENTS = frozenset({'receipt_signed', 'settled'})
ATTEMPT_EVENTS = RECEIPT_EVENTS | {
    'opening', 'offer', 'counter', 'checkout_unpaid', 'rejected', 'cancelled',
    'negotiation_expired',
}


def iter_events(path: Path = EVENTS, *, kinds: Set[str] | None = None) -> Iterator[dict[str, Any]]:
    """Read append-only evidence in order without keeping unrelated records."""
    try:
        source = path.open('rb')
    except FileNotFoundError:
        return
    encoded = {kind.encode() for kind in kinds} if kinds is not None else None
    with source:
        for line in source:
            if encoded is not None:
                match = EVENT_PREFIX.match(line)
                if match and match[1] not in encoded:
                    continue
            try:
                row = json.loads(line.decode('utf-8', errors='replace'))
            except ValueError:
                continue
            if isinstance(row, dict) and isinstance(kind := row.get('event'), str) and kind:
                if kinds is None or kind in kinds:
                    yield row


def events(path: Path = EVENTS, *, kinds: Set[str] | None = None) -> list[dict[str, Any]]:
    return list(iter_events(path, kinds=kinds))


def record(event: str, *, path: Path = EVENTS, **fields: Any) -> dict[str, Any]:
    row = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "event": event, **decision_audit.enrich(fields)}
    path.parent.mkdir(parents=True, exist_ok=True)
    data = (json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n").encode()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    try:
        if os.write(fd, data) != len(data):
            raise OSError("short event-log write")
    finally:
        os.close(fd)
    return row


def sync_settlements(kit_log: Path = KIT_LOG, *, event_log: Path = EVENTS,
                     cursor_path: Path = CURSOR) -> int:
    """Import only complete new [acp] settlement lines; survive log rotation."""
    if not kit_log.exists():
        return 0
    stat = kit_log.stat()
    try:
        cursor = json.loads(cursor_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        cursor = {}
    offset = (int(cursor["offset"]) if cursor.get("inode") == stat.st_ino
              and 0 <= cursor.get("offset", -1) <= stat.st_size else 0)
    added = 0
    seen = None
    receipts_by_unit = defaultdict(list)
    with kit_log.open(encoding="utf-8", errors="replace") as source:
        source.seek(offset)
        while True:
            before = source.tell()
            line = source.readline()
            if not line:
                break
            if not line.endswith("\n"):
                source.seek(before)             # the kit is still writing this line
                break
            match = SETTLED.search(line)
            if not match:
                continue
            if seen is None:
                # Most requests find no new settlement. Only load the linkage
                # evidence when the kit actually has a line to import.
                seen = set()
                for row in iter_events(event_log, kinds=RECEIPT_EVENTS):
                    if row['event'] == 'settled' and 'ledger' in row:
                        seen.add(int(row['ledger']))
                    elif row['event'] == 'receipt_signed' and row.get('unit'):
                        receipts_by_unit[row['unit']].append(row)
            ledger = int(match["ledger"])
            if ledger in seen:
                continue
            matches={row.get('task_id'):row for row in receipts_by_unit.get(match['unit'],[])
                     if row.get('price')==int(match['price']) and row.get('task_id')}
            receipt=next(iter(matches.values())) if len(matches)==1 else {}
            record("settled", path=event_log, ledger=ledger, unit=match["unit"],
                   grade=match["grade"], price=int(match["price"]),
                   sku=receipt.get("sku"), task_id=receipt.get("task_id"),
                   warranty_hours=receipt.get('warranty_hours',0),score_version=receipt.get('score_version'),
                   **{key:receipt[key] for key in decision_audit.LINKS
                      if key not in ('task_id','sku','score_version') and receipt.get(key) is not None},
                   outcome_link_status=('exact_receipt_unit_price' if receipt.get('decision_id') else
                                        'ambiguous' if len(matches)>1 else 'legacy_or_unmatched'),
                   outcome_source='kit_acp_settlement')
            seen.add(ledger)
            added += 1
        next_offset = source.tell()
    cursor_path.parent.mkdir(parents=True, exist_ok=True)
    temp = cursor_path.with_suffix(".tmp")
    temp.write_text(json.dumps({"inode": stat.st_ino, "offset": next_offset}), encoding="utf-8")
    os.replace(temp, cursor_path)
    return added


def _attempts(rows: Iterable[dict], *, skus: Set[str] | None = None) -> list[dict]:
    """Rebuild linked observations; numeric history remains in the event file."""
    attempts: dict[str, dict[str, Any]] = {}
    settled_by_task: dict[str, dict[str, Any]] = {}
    for row in rows:
        task = row.get("task_id")
        kind = row["event"]
        if kind == "settled" and task:
            settled_by_task[task] = row
        elif kind == "opening" and task:
            if skus is not None and row.get('sku') not in skus:
                attempts.pop(task, None)
                continue
            attempts[task] = {"task_id": task, "sku": row.get("sku"),
                              "grade": row.get("grade"), "category": row.get("category"),
                              "unit": row.get("unit"), "opening_ask": row.get("ask"),
                              "list_price": row.get("list_price"), "at": row.get("at"),
                              'cost':row.get('cost'),'expires_at':row.get('expires_at'),
                              'hours_left_at_opening':hours_left(row.get('expires_at'),row.get('at')),
                              "offers": [], "counters": [], 'offer_count':0,'counter_count':0,"outcome": "OPEN"}
        elif task in attempts and kind == "offer":
            a=attempts[task];a['offers']=(a['offers']+[row.get('price')])[-MAX_TURNS:];a['offer_count']+=1
        elif task in attempts and kind == "counter":
            a=attempts[task];a['counters']=(a['counters']+[row.get('ask')])[-MAX_TURNS:];a['counter_count']+=1
        elif task in attempts and kind == "receipt_signed":
            attempts[task]["outcome"] = "RECEIPT_PENDING"
            attempts[task]["agreed_price"] = row.get("price")
            attempts[task]['grade_at_receipt'] = row.get('grade')
            expires=row.get('expires_at',attempts[task].get('expires_at'))
            attempts[task]['hours_left_at_receipt'] = hours_left(expires,row.get('at'))
        elif task in attempts and kind=='checkout_unpaid':
            attempts[task]['outcome']='CHECKOUT_UNPAID'
            attempts[task]['reason']=text(row.get('reason'))
        elif task in attempts and kind in {"rejected", "cancelled"}:
            attempts[task]["outcome"] = kind.upper()
            attempts[task]['reason'] = text(row.get('reason'))
        elif task in attempts and kind=='negotiation_expired':
            attempts[task]['outcome']='EXPIRED_UNKNOWN'
            attempts[task]['reason']=text(row.get('reason'))
    for task, settlement in settled_by_task.items():
        if task in attempts:
            attempts[task]["outcome"] = "SETTLED"
            attempts[task]["settled_price"] = settlement["price"]
            attempts[task]["ledger"] = settlement["ledger"]
            attempts[task]['settled_grade'] = settlement.get('grade')
    return list(attempts.values())


def trade_attempts(skus: Set[str], *, path: Path = EVENTS) -> list[dict]:
    """Build candidate-SKU histories in one pass, including task-only outcomes."""
    return _attempts(iter_events(path, kinds=ATTEMPT_EVENTS), skus=skus)


def compact_context(sku: str, grade: str, *, path: Path = EVENTS,
                    recent_limit: int = 3, max_bytes: int = SELLER_MEMORY_BYTES,
                    attempts: list[dict] | None = None) -> dict[str, Any]:
    """Exact aggregates plus a few condition-preserving transaction examples."""
    if attempts is None:
        attempts = trade_attempts({sku}, path=path)
    def observed_grade(a):
        return a.get('settled_grade') or a.get('grade_at_receipt') or a.get('grade')

    relevant = [a for a in attempts if a['sku']==sku and observed_grade(a)==grade]
    broader = [a for a in attempts if a['sku']==sku and observed_grade(a)!=grade]
    counts = defaultdict(int)
    for a in relevant:
        counts[a["outcome"]] += 1
    view = {
        "sku": sku, "grade": grade, "same_grade_attempts": len(relevant),
        "same_sku_other_grade_attempts": len(broader),
        "outcomes": dict(counts),
        'settled_prices_by_grade':price_ranges([a for a in relevant if a['outcome']=='SETTLED']),
        "recent_same_grade": select_cases(relevant,recent_limit),
        "recent_other_grade": select_cases(broader,min(2,recent_limit)),
        "caution": "A receipt is pending until [acp] settled; OPEN is unknown, not a failed sale. "
                   "Hours left are historical quote/receipt conditions, not current expiry. "
                   "Examples are observations, not permanent rules; visits are not total demand.",
    }
    return bounded(view,('recent_same_grade','recent_other_grade'),max_bytes)


def recent_trade_context(history: list[dict], skus: set[str], *, max_bytes=3000) -> dict:
    """Filter and link trades BEFORE limiting cases; snapshots cannot crowd them out."""
    rows=[a for a in _attempts(history) if a['sku'] in skus]
    confirmed=defaultdict(int)
    for a in rows:
        if a['outcome']=='SETTLED':confirmed[a['sku']]+=1
    cases=select_cases(rows)
    return bounded({'confirmed_by_sku':dict(confirmed),'cases':cases,
                    'note':'Selected candidate SKUs only; receipts pending and incomplete trades are not sales. '
                           'Historical grade/expiry conditions must accompany price comparisons.'},('cases',),max_bytes)
