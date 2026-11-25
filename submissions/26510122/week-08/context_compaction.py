"""Small deterministic context views; original event files are never rewritten.

Budgets measure serialized UTF-8 bytes, not estimated provider tokens. Keep
current facts outside these historical-memory budgets. No LLM summaries or RAG.
"""
from __future__ import annotations

from datetime import datetime
import json

MAX_CASES = 3
MAX_TURNS = 4
BUYER_MEMORY_BYTES = 3200
BUYER_BRIEF_BYTES = 1600
SELLER_MEMORY_BYTES = 3600
BUYER_PROMPT_BYTES = 32_000
RESTOCK_HISTORY_BYTES = 3000


def size(value) -> int:
    return len(json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode('utf-8'))


def text(value, limit: int = 120):
    if value is None:
        return None
    value = str(value)
    return value if len(value) <= limit else value[:limit] + '…'


def epoch(value):
    try:
        return datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()
    except (ValueError, AttributeError):
        return None


def hours_left(expires, at):
    when = epoch(at)
    if type(expires) not in (int, float) or when is None:
        return None
    return round(max(0., expires - when) / 3600, 2)


def price_ranges(rows: list[dict]) -> dict:
    groups = {}
    for row in rows:
        price = row.get('settled_price', row.get('price'))
        if type(price) not in (int, float) or price <= 0:
            continue
        grade = row.get('settled_grade') or row.get('grade') or 'unknown'
        grade = grade if grade in ('A', 'B', 'C') else 'unknown'
        group = groups.setdefault(grade, {'count': 0, 'min': price, 'max': price})
        group['count'] += 1
        group['min'], group['max'] = min(group['min'], price), max(group['max'], price)
    return groups


def select_cases(rows: list[dict], limit: int = MAX_CASES, *, outcome_key='outcome') -> list[dict]:
    """Recent first, retaining a contrasting outcome when one exists."""
    limit = max(0, min(limit, MAX_CASES))
    if not rows or not limit:
        return []
    newest = list(reversed(rows))
    selected = [newest[0]]
    contrast = next((r for r in newest[1:] if r.get(outcome_key) != newest[0].get(outcome_key)), None)
    if contrast and limit > 1:
        selected.append(contrast)
    for row in newest[1:]:
        if len(selected) >= limit:
            break
        if all(row is not other for other in selected):
            selected.append(row)
    return selected


def bounded(view: dict, case_fields: tuple[str, ...], budget: int) -> dict:
    """Remove whole optional cases, never cut a serialized object or an outcome."""
    view = {**view, **{key: list(view.get(key, [])) for key in case_fields}}
    omitted = 0
    while size(view) > budget:
        key = next((k for k in reversed(case_fields) if view[k]), None)
        if key is None:
            raise ValueError('required memory summary exceeds its byte budget')
        view[key].pop(); omitted += 1
    # The counts/price ranges still cover the full history, including dropped cases.
    if omitted:
        view['cases_omitted_for_budget'] = omitted
        while size(view) > budget:
            key = next((k for k in reversed(case_fields) if view[k]), None)
            if key is None:
                view.pop('cases_omitted_for_budget')
                break
            view[key].pop()
            view['cases_omitted_for_budget'] += 1
    return view
