"""Signed purchase coverage and claim intake; never transfers money."""
import base64
import calendar
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from uuid import uuid4

from cryptography.hazmat.primitives import serialization
from cryptography.exceptions import InvalidSignature
from google.protobuf.json_format import MessageToDict
from google.protobuf.message import Message


def canon(data):
    return json.dumps(data, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode('utf-8')


def policy(root, now=None):
    value = json.loads((Path(root) / 'satisfaction_event.json').read_text(encoding='utf-8'))
    if value.get('enabled') is not True:
        return None
    if (value.get('benefit') != 'one_calendar_month_full_refund'
            or value.get('refund_percent') != 100 or value.get('review_required') is not False
            or value.get('payout') != 'manual_external'):
        raise ValueError('Full refund policy must be independent of ratings and paid manually')
    start, end = (datetime.fromisoformat(value[key]) for key in ('starts_at', 'ends_at'))
    if start.tzinfo is None or end.tzinfo is None or start >= end:
        raise ValueError('Refund event requires a timezone-aware purchase interval')
    return value if start <= (now or datetime.now(timezone.utc)) < end else None


def month_deadline(purchased):
    if purchased.tzinfo is None:
        raise ValueError('Purchase time must specify timezone')
    year, month = purchased.year + (purchased.month == 12), purchased.month % 12 + 1
    return purchased.replace(year=year, month=month,
                             day=min(purchased.day, calendar.monthrange(year, month)[1]))


def coverage_path(root, task):
    return Path(root) / 'logs/refund-coverage' / (hashlib.sha256(task.encode('utf-8')).hexdigest() + '.json')


def cover_purchase(root, receipt, terms, now=None):
    purchased = now or datetime.now(timezone.utc)
    # Calculate calendar months in the published KST timezone, not UTC dates.
    purchased = purchased.astimezone(datetime.fromisoformat(terms['starts_at']).tzinfo)
    body = {'kind': 'satisfaction_refund_coverage', 'task': receipt['task'], 'item': receipt['item'],
            'paid_amount': receipt['price'], 'purchased_at': purchased.isoformat(),
            'claim_deadline': month_deadline(purchased).isoformat(), 'refund_percent': 100,
            'review_required': False, 'payout': 'manual_external', 'notice': terms['notice']}
    key = serialization.load_pem_private_key((Path(root) / 'shop_key.pem').read_bytes(), password=None)
    envelope = {**body, 'sig_terms': base64.b64encode(key.sign(canon(body))).decode('ascii')}
    path = coverage_path(root, receipt['task'])
    path.parent.mkdir(parents=True, exist_ok=True)
    # Keep each purchase's original terms, even if policy changes afterwards.
    with path.open('x', encoding='utf-8') as stream:
        json.dump({'receipt': receipt, 'coverage': envelope}, stream, ensure_ascii=False, indent=2)
    return envelope


def request_data(parts):
    for part in parts:
        value = getattr(getattr(part, 'root', part), 'data', None)
        if isinstance(value, Message):
            value = MessageToDict(value)
        if isinstance(value, dict) and value.get('kind') == 'refund_request':
            return value
    return None


def receive_request(root, request, now=None):
    """Validate coverage and receipt; identity/market confirmation precede payment."""
    receipt = request.get('receipt')
    if not isinstance(receipt, dict) or not isinstance(receipt.get('task'), str):
        raise ValueError('Provide the original signed receipt')
    reason = request.get('reason')
    if not isinstance(reason, str) or not reason.strip() or len(reason) > 2000:
        raise ValueError('Provide a dissatisfaction reason within the request limit')
    try:
        saved = json.loads(coverage_path(root, receipt['task']).read_text(encoding='utf-8'))
    except FileNotFoundError as error:
        raise ValueError('No coverage found for this purchase; earlier event benefits remain separate') from error
    if receipt != saved['receipt']:
        raise ValueError('Receipt does not match the original covered purchase')
    # Protobuf transports represent numeric values as floats. Use the exact
    # original integer representation after matching every receipt field.
    receipt = saved['receipt']
    key = serialization.load_pem_private_key((Path(root) / 'shop_key.pem').read_bytes(), password=None)
    try:
        body = {name: receipt[name] for name in ('shop', 'item', 'price', 'task')}
        key.public_key().verify(base64.b64decode(receipt['sig_seller'], validate=True), canon(body))
    except (InvalidSignature, KeyError, ValueError, TypeError) as error:
        raise ValueError('Receipt signature is invalid') from error
    coverage = saved['coverage']
    current = now or datetime.now(timezone.utc)
    directory = Path(root) / 'logs/refund-requests'
    # One pending claim per purchase; receipt copying cannot create repeat payouts.
    path = directory / (hashlib.sha256(receipt['task'].encode('utf-8')).hexdigest() + '.json')
    if path.exists():
        record = json.loads(path.read_text(encoding='utf-8'))
        return {name: record[name] for name in ('claim_id', 'task', 'received_at', 'amount', 'status', 'payout', 'paid')}
    if current > datetime.fromisoformat(coverage['claim_deadline']):
        raise ValueError('The one-calendar-month claim deadline has passed')
    directory.mkdir(parents=True, exist_ok=True)
    record = {'claim_id': uuid4().hex, 'task': receipt['task'], 'received_at': current.isoformat(),
              'amount': receipt['price'], 'reason': reason.strip(), 'receipt': receipt,
              'claim_deadline': coverage['claim_deadline'], 'status': 'pending_buyer_and_market_verification',
              'payout': 'manual_external', 'paid': False}
    try:
        with path.open('x', encoding='utf-8') as stream:
            json.dump(record, stream, ensure_ascii=False, indent=2)
    except FileExistsError:
        record = json.loads(path.read_text(encoding='utf-8'))
    return {name: record[name] for name in ('claim_id', 'task', 'received_at', 'amount', 'status', 'payout', 'paid')}
