"""First-hand buyer outcomes, separated from unsigned receipts and public reviews."""

from __future__ import annotations

from contextlib import closing
from collections import Counter
from datetime import datetime, timezone
import json
import os
import sqlite3
import time
from pathlib import Path
from typing import Any
import decision_audit
from context_compaction import (BUYER_MEMORY_BYTES, BUYER_BRIEF_BYTES, MAX_TURNS,
                                bounded, price_ranges, select_cases, text)


EVENTS = Path("logs/buyer-events.jsonl")
PAYMENTS = Path(".market/payments.jsonl")
BROKEN = Path(".market/broken.jsonl")


def read_events(path: Path = EVENTS) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    result = []
    with path.open(encoding="utf-8", errors="replace") as source:
        for line in source:
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if isinstance(event, dict) and isinstance(event.get("event"), str):
                result.append(event)
    return result


MEMORY_KINDS = frozenset({'shop_identity', 'payment', 'settled', 'negotiation', 'broken'})


class HistoryRows(list):
    reader_source = 'raw_log'


def read_history(path: Path = EVENTS, *, db_path=None, now=None):
    """Read a consistent indexed prefix plus its fresh raw tail; never write the DB.

    The raw log remains authoritative. A missing/stale/rotated index falls back
    to the source. Identity observations from every event are retained, even
    when the large decision payload is not relevant to purchase memory.
    """
    path = Path(path)
    now = time.time() if now is None else now
    db_path = Path(db_path) if db_path else path.parent.parent/'state/buyer.sqlite3'
    rows = HistoryRows()
    identities = set()

    def remember(event):
        if not isinstance(event, dict):
            return
        if event.get('shop_name') and event.get('shop'):
            identities.add((event.get('order'),event['shop'],event['shop_name']))
        if event.get('event') in MEMORY_KINDS:
            rows.append(event)
        elif event.get('event') == 'selection_opportunities':
            try:
                at = datetime.fromisoformat(event['at'].replace('Z','+00:00')).timestamp()
            except (KeyError,ValueError,TypeError,AttributeError):
                at = now  # unknown age must not discard a possible checkout guard
            if at >= now-15*60:
                rows.append(event)

    try:
        source = path.open('rb')
    except FileNotFoundError:
        return rows
    with source:
        stat = os.fstat(source.fileno())
        try:
            with closing(sqlite3.connect(db_path.resolve().as_uri()+'?mode=ro',uri=True,timeout=.5)) as db:
                db.execute('BEGIN')
                cursor = db.execute('SELECT device,inode,generation,offset FROM sources WHERE source=?',
                                    ('logs/buyer-events.jsonl',)).fetchone()
                if (not cursor or cursor[0]!=stat.st_dev or cursor[1]!=stat.st_ino
                        or not 0<=cursor[3]<=stat.st_size):
                    raise ValueError('index does not match the opened log')
                generation, offset = cursor[2:]
                last = db.execute('SELECT byte_offset,payload FROM events WHERE source=? AND generation=? '
                                  'AND byte_offset<? ORDER BY byte_offset DESC LIMIT 1',
                                  ('logs/buyer-events.jsonl',generation,offset)).fetchone()
                if last:
                    source.seek(last[0])
                    if json.loads(source.readline()) != json.loads(last[1]):
                        raise ValueError('indexed prefix changed')
                elif offset:
                    raise ValueError('indexed prefix has no verifiable event')
                query = """SELECT event,at,order_id,shop_url,shop_name,
                           CASE WHEN event IN ('shop_identity','payment','settled','negotiation','broken')
                             OR event='selection_opportunities' AND (at IS NULL OR at>=?)
                           THEN payload END
                           FROM events WHERE source=? AND generation=? AND byte_offset<?
                           ORDER BY byte_offset"""
                cutoff=datetime.fromtimestamp(now-15*60,timezone.utc).isoformat(timespec='seconds')
                for kind,at,order,url,name,payload in db.execute(
                        query,(cutoff,'logs/buyer-events.jsonl',generation,offset)):
                    if name and url:
                        identities.add((order,url,name))
                    if payload is not None:
                        remember(json.loads(payload))
                source.seek(offset)
                rows.reader_source='sqlite_prefix_and_raw_tail'
        except (OSError,sqlite3.Error,ValueError,TypeError):
            rows.clear()
            identities.clear()
            source.seek(0)
            rows.reader_source='raw_log'
        for line in source:
            if not line.endswith(bytes([10])):
                break  # an append in progress is not evidence
            try:
                remember(json.loads(line))
            except (ValueError,UnicodeError):
                continue
        rows.extend({'event':'identity_observation','order':order,'shop':url,'shop_name':name}
                    for order,url,name in sorted(identities,key=lambda x:tuple(str(v) for v in x)))
    return rows


class PreparedHistory(list):
    """Resolve identities and group first-hand evidence once per issued order."""
    def __init__(self, rows):
        super().__init__(rows)
        self.reader_source=getattr(rows,'reader_source','provided_history')
        self.alias_names={}
        self.order_names={}
        self.by_name={}
        self.by_url={}
        self.by_sku={}
        self.broken_names=Counter()
        self.opportunities={}
        for e in self:
            if e.get('shop_name') and e.get('shop'):
                self.alias_names.setdefault(e['shop'],set()).add(e['shop_name'])
                if e.get('order'):
                    self.order_names.setdefault((e['order'],e['shop']),set()).add(e['shop_name'])
            if e.get('event')=='broken':
                self.broken_names[e.get('name')]+=1
            if e.get('event')=='selection_opportunities' and e.get('order'):
                self.opportunities[e['order']]=e
        for e in self:
            if e.get('event') not in ('payment','settled','negotiation'):
                continue
            name=e.get('shop_name') or self.resolved(e.get('shop'),e.get('order'))
            if name:
                self.by_name.setdefault(name,[]).append(e)
            self.by_url.setdefault(e.get('shop'),[]).append(e)
            self.by_sku.setdefault(e.get('sku'),[]).append(e)

    def resolved(self,url,order=None):
        names=self.order_names.get((order,url)) or self.alias_names.get(url,set())
        return next(iter(names)) if len(names)==1 else None


def record(event: str, *, path: Path = EVENTS, **fields: Any) -> dict:
    path.parent.mkdir(parents=True, exist_ok=True)
    row = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "event": event, **decision_audit.enrich(fields)}
    data = (json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n").encode()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    try:
        if os.write(fd, data) != len(data):
            raise OSError("short buyer event write")
    finally:
        os.close(fd)
    return row


def register_shops(shops: list[dict], *, path: Path = EVENTS, history=None) -> None:
    """Use registry names as identity; URLs are transport aliases, not identity."""
    rows = read_history(path) if history is None else history
    seen = {(r.get('shop_name'), r.get('shop')) for r in rows if r['event']=='shop_identity'}
    for shop in shops:
        if not isinstance(shop,dict):
            continue
        name, url = shop.get('name'), shop.get('url')
        if isinstance(name,str) and name and isinstance(url,str) and url and (name,url) not in seen:
            row=record('shop_identity',path=path,shop_name=name,shop=url)
            if history is not None:history.append(row)
            seen.add((name,url))


def resolve_shop_name(shop_url: str, *, order: str | None = None,
                      history: list[dict] | None = None) -> str | None:
    rows = read_events() if history is None else history
    if order:
        names = {e['shop_name'] for e in rows if e.get('order')==order and e.get('shop')==shop_url and e.get('shop_name')}
        if len(names)==1:
            return next(iter(names))
    names = {e['shop_name'] for e in rows if e.get('shop')==shop_url and e.get('shop_name')}
    return next(iter(names)) if len(names)==1 else None


def import_payments(payment_path: Path = PAYMENTS, *, event_path: Path = EVENTS, history=None) -> int:
    """Import kit payment evidence once; a negotiated receipt is not a purchase."""
    if not payment_path.exists():
        return 0
    history = read_history(event_path) if history is None else history
    def key(e):
        return tuple(e.get(k) for k in ('order','shop','sku','price','paid','ledger','reason'))
    seen = {key(e) for e in history if e['event']=='payment'}
    count = 0
    with payment_path.open(encoding="utf-8", errors="replace") as source:
        for line in source:
            try:
                payment = json.loads(line)
            except ValueError:
                continue
            if not isinstance(payment, dict) or not payment.get("order"):
                continue
            normalized = {**payment,'sku':payment.get('item'),'paid':payment.get('paid') is True}
            if key(normalized) in seen:
                continue
            linked = decision_audit.buyer_outcome_links(history, order=payment['order'],
                        shop=payment.get('shop'), sku=payment.get('item'), price=payment.get('price'))
            added=record("payment", path=event_path, order=payment["order"],
                   shop_name=resolve_shop_name(payment.get('shop'),order=payment['order'],history=history),
                   shop=payment.get("shop"), sku=payment.get("item"),
                   price=payment.get("price"), paid=payment.get("paid") is True,
                   ledger=payment.get("ledger"), reason=payment.get("reason"),
                   **{k:v for k,v in linked.items() if k not in ('order','shop','shop_name','sku')},
                   outcome_source='kit_payment')
            history.append(added)
            seen.add(key(normalized))
            count += 1
    return count


def import_broken(broken_path: Path = BROKEN, *, event_path: Path = EVENTS, history=None) -> int:
    """Keep post-sale breakage evidence without guessing which seller caused it."""
    if not broken_path.exists():
        return 0
    history = read_history(event_path) if history is None else history
    seen = {(e.get("name"), e.get("broke_at")) for e in history
            if e["event"] == "broken"}
    count = 0
    with broken_path.open(encoding="utf-8", errors="replace") as source:
        for line in source:
            try:
                news = json.loads(line)
            except ValueError:
                continue
            if not isinstance(news, dict) or not news.get("name") or not news.get("broke_at"):
                continue
            key = (news["name"], news["broke_at"])
            if key in seen:
                continue
            added=record("broken", path=event_path, name=news["name"], broke_at=news["broke_at"],
                   outcome_link_status='unmatched',outcome_source='kit_breakage_notice')
            history.append(added)
            seen.add(key)
            count += 1
    return count


def context(shop_url: str, sku: str, *, shop_name: str | None = None,
            product_name: str | None = None, path: Path = EVENTS,
            history: list[dict] | None = None) -> dict[str, Any]:
    rows = read_history(path) if history is None else history
    rows = rows if isinstance(rows,PreparedHistory) else PreparedHistory(rows)
    name = shop_name or rows.resolved(shop_url)
    first_hand = rows.by_name.get(name,[]) if name else rows.by_url.get(shop_url,[])
    ledger_orders={e['ledger']:e['order'] for e in first_hand if e.get('ledger') is not None and e.get('order')}
    episodes = {}
    for i,e in enumerate(first_hand):
        key = (e.get('order') or ledger_orders.get(e.get('ledger')) or e.get('ledger') or f'legacy-{i}',e.get('sku'))
        episode = episodes.setdefault(key,{'order':e.get('order'),'sku':e.get('sku'),'outcome':'unknown'})
        episode['at'] = e.get('at');episode['_sequence'] = i
        if e['event']=='negotiation':
            offers=e.get('offers') if isinstance(e.get('offers'),list) else []
            episode.update({'offers':offers[-MAX_TURNS:],
                            'offer_count':len(offers), 'reason':text(e.get('reason'))})
            if episode['outcome'] not in ('paid','refused'):
                episode['outcome'] = e.get('result','unknown')
        elif e['event']=='payment':
            episode['payment_seen'] = True
            if e.get('paid') or episode['outcome']!='paid':
                episode.update({'outcome':'paid' if e.get('paid') else 'refused','price':e.get('price'),
                                'ledger':e.get('ledger'),'reason':text(e.get('reason'))})
        elif e['event']=='settled' and e.get('ledger') is not None:
            episode.update({k:e.get(k) for k in ('price','ledger','grade','value','warranty_hours',
                                                'expires_at','hours_left_at_recording')})
            episode['outcome'] = 'paid'
    same = [{k:v for k,v in e.items() if k!='_sequence'} for e in sorted(episodes.values(),key=lambda e:e['_sequence']) if e['sku']==sku]
    outcomes = Counter(e['outcome'] for e in same if e['outcome'] in ('paid','refused'))
    view = {'shop_name':text(name,80),'shop_payment_count':sum(e['outcome'] in ('paid','refused') for e in episodes.values()),
            'same_sku_payment_outcomes':dict(outcomes),
            'settled_prices_by_grade':price_ranges([e for e in same if e['outcome']=='paid']),
            'cases':select_cases(same),
            'observed_receipt_problems':sum(e.get('result')=='invalid_receipt' for e in first_hand if e['event']=='negotiation' and e.get('sku')==sku),
            'same_product_broken_reports':rows.broken_names[product_name] if product_name else 0,
            'note':'Only confirmed payment/settlement is paid. Cases are observations, not rules. '
                   'Breakage reports identify a product, not its seller. Unknown grade stays unknown.'}
    return bounded(view,('cases',),BUYER_MEMORY_BYTES)


def brief(memory: dict) -> dict:
    """Give the selector evidence behind a tool score, not just a score."""
    result = {k:memory.get(k) for k in ('shop_name','shop_payment_count','same_sku_payment_outcomes',
                                      'settled_prices_by_grade','observed_receipt_problems','same_product_broken_reports')}
    result['cases'] = memory.get('cases',[])[:2]
    result['note'] = 'First-hand observations; public reviews are separate claims. More history may be omitted.'
    return bounded(result,('cases',),BUYER_BRIEF_BYTES)
