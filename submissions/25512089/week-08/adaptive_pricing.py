"""Adaptive Week-07 seller asking price from its OWN issued deals and review snapshot.

Data:
  .market/seller-trades.jsonl (generated on signed, completed receipts)
  .market/own-review-summary.json (verified snapshot from Agent Market dashboard)
  .market/pricing-decisions.jsonl (why each ask changed)
These state files are local, non-secret and intentionally not committed (.market is ignored).
"""
from __future__ import annotations

from collections import deque
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
STATE = ROOT / '.market'
TRADES = STATE / 'seller-trades.jsonl'
REVIEWS = STATE / 'own-review-summary.json'
PRICING = STATE / 'pricing-decisions.jsonl'


def _append_jsonl(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(exist_ok=True, parents=True)
    with path.open('a', encoding='utf-8') as file:
        file.write(json.dumps(data, ensure_ascii=False, separators=(',', ':')) + '\n')


def _recent_item_deals(item: str) -> list[int]:
    if not TRADES.exists():
        return []
    prices: deque[int] = deque(maxlen=20)
    try:
        with TRADES.open('r', encoding='utf-8') as stream:
            for line in stream:
                try:
                    trade = json.loads(line)
                    if trade.get('item') == item:
                        value = trade.get('price')
                        if type(value) is int and value > 0:
                            prices.append(value)
                except (json.JSONDecodeError, ValueError, TypeError):
                    continue
    except OSError:
        return []
    return list(prices)


def _review_summary() -> tuple[int, float | None]:
    try:
        snapshot = json.loads(REVIEWS.read_text(encoding='utf-8'))
        count = snapshot.get('review_count', 0)
        value = snapshot.get('avg_stars')
        if type(count) is not int or count < 0 or count > 1_000_000:
            return 0, None
        if value is None or not isinstance(value, (int, float)) or isinstance(value, bool):
            return count, None
        stars = float(value)
        return (count, stars) if 1 <= stars <= 5 else (count, None)
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return 0, None


def adjusted_ask(item: str, reserve: int, model_ask: int) -> tuple[int, dict[str, Any]]:
    """Use model's opening as baseline. Shift at most 6%; never breach reserve.

    REVIEW SIGNAL: Average buyer rating controls up to +/-3%.
    OWN TRADE SIGNAL: After 3 local completed receipts, recent average relative
    to reserve adjusts another +/-2%. Every input is an observed value.
    """
    prices = _recent_item_deals(item)
    review_count, avg_stars = _review_summary()
    rating_delta = 0.0
    if review_count >= 3 and avg_stars is not None:
        if avg_stars < 3.5:
            rating_delta = -0.03
        elif avg_stars >= 4.7:
            rating_delta = 0.025
        elif avg_stars >= 4.2:
            rating_delta = 0.015
    sales_delta = 0.0
    avg_sale = sum(prices) / len(prices) if prices else None
    if len(prices) >= 3:
        if avg_sale <= reserve * 1.02:
            sales_delta = -0.02
        elif avg_sale >= reserve * 1.05:
            sales_delta = 0.015
    delta = max(-0.06, min(0.06, rating_delta + sales_delta))
    before = max(reserve, int(model_ask))
    after = max(reserve, round(before * (1 + delta)))
    explanation = {
        'utc': datetime.now(timezone.utc).isoformat(timespec='seconds'),
        'item': item,
        'reserve': reserve,
        'model_ask': before,
        'final_ask': after,
        'delta_pct': round(delta * 100, 2),
        'recent_own_trades': len(prices),
        'recent_own_avg_price': round(avg_sale, 2) if avg_sale is not None else None,
        'review_count': review_count,
        'avg_stars': avg_stars,
        'review_delta_pct': round(rating_delta * 100, 2),
        'sales_delta_pct': round(sales_delta * 100, 2),
    }
    try:
        _append_jsonl(PRICING, explanation)
    except OSError as exc:
        print(f'[ADAPTIVE WARNING] could not save pricing evidence: {type(exc).__name__}', flush=True)
    return after, explanation


def record_signed_deal(*, item: str, price: int, task_id: str) -> None:
    """Store a completed A2A signed receipt. A counted market deal is stricter."""
    entry = {
        'utc': datetime.now(timezone.utc).isoformat(timespec='seconds'),
        'item': item,
        'price': price,
        'task': task_id,
        'source': 'seller_signed_completed_receipt',
    }
    try:
        _append_jsonl(TRADES, entry)
    except OSError as exc:
        print(f'[ADAPTIVE WARNING] could not save trade evidence: {type(exc).__name__}', flush=True)
