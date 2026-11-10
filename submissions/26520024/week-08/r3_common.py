"""Round 3 contracts. Importing this module has no effects."""
import json
import math
import os
from pathlib import Path
import tempfile
from r3_clock import clock as time

ROOT = Path(__file__).resolve().parent
DEFAULT_POLICY = {
    'reserve_cash_fraction': .25, 'purchase_budget_fraction': .10,
    'max_sku_units': 3, 'max_total_units': 40, 'max_category_fraction': .35,
    'quote_ttl_seconds': 180, 'warranty_hours': 0, 'liquidation_hours': 3,
    'trading_deadline': '2026-11-17T23:59:00+09:00', 'min_food_buffer_minutes': 5,
    'max_buyer_turns': 6, 'buyer_timeout_seconds': 240,
    'analysis_interval_seconds': 900, 'restock_cooldown_seconds': 900,
    'commitment_ttl_seconds': 1800, 'trend_half_life_seconds': 21600,
    'min_turnover_hours': 6, 'public_observation_enabled': True,
    'forum_enabled': True, 'reflection_enabled': False,
    'reflection_interval_seconds': 1800,
    'fallback_enabled': True, 'model_call_seconds': 20,
    'model_cooldown_seconds': 300, 'supply_margin_seconds': 20,
    'adaptive_pricing': True, 'opening_price_fraction': .9,
    'max_inventory_fraction': .6,
    'buyer_min_saved': .05, 'buyer_required_warranty_hours': 0,
    'exploration_budget_fraction': .20,
    'competitive_tracking_enabled': True,
    'adaptive_replenishment': True,
    'adaptive_pilot_budget_fraction': .50,
}


def state_dir(path=None):
    return Path(path if path is not None else os.environ.get('MARKET_DIR', ROOT / '.market')).resolve()


def _pairs(items):
    out = {}
    for key, value in items:
        if key in out:
            raise ValueError('duplicate JSON key: ' + key)
        out[key] = value
    return out


def _constant(value):
    raise ValueError('non-finite JSON value: ' + value)


def parse_json(text):
    return json.loads(text, object_pairs_hook=_pairs, parse_constant=_constant)


def read_json(path, default=None, max_bytes=20_000_000):
    path = Path(path)
    try:
        with path.open('rb') as stream:
            raw = stream.read(max_bytes + 1)
    except FileNotFoundError:
        return default
    if len(raw) > max_bytes:
        raise ValueError('JSON exceeds input size limit: ' + path.name)
    return parse_json(raw.decode('utf-8'))


def atomic_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, ensure_ascii=False, allow_nan=False, indent=2) + '\n'
    fd, name = tempfile.mkstemp(prefix='.' + path.name + '-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def number(value):
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError('finite number required')
    return value


def money(value):
    value = number(value)
    if value < 0 or int(value) != value:
        raise ValueError('nonnegative whole-credit amount required')
    return int(value)


def load_policy(md=None):
    raw = read_json(state_dir(md) / 'policy.json', {})
    if not isinstance(raw, dict) or set(raw) - set(DEFAULT_POLICY):
        raise ValueError('invalid or unknown policy setting')
    policy = dict(DEFAULT_POLICY, **raw)
    for key, default in DEFAULT_POLICY.items():
        value = policy[key]
        if type(default) is bool:
            if type(value) is not bool:
                raise ValueError('boolean policy required: ' + key)
        elif key.endswith('_fraction'):
            if not 0 < number(value) <= 1:
                raise ValueError('fraction must be in (0,1]: ' + key)
        elif isinstance(default, (int, float)):
            if number(value) < 0 or (key.startswith('max_') and money(value) < 1):
                raise ValueError('invalid policy number: ' + key)
    if policy['buyer_timeout_seconds'] > 240 or policy['max_buyer_turns'] > 8:
        raise ValueError('buyer limits exceed kit-compatible bound')
    if policy['warranty_hours'] > 6:
        raise ValueError('warranty policy must be between 0 and 6 hours')
    if not 0 <= number(policy['buyer_min_saved']) < 1:
        raise ValueError('buyer_min_saved must be in [0,1)')
    if policy['buyer_required_warranty_hours'] > 72:
        raise ValueError('buyer warranty requirement cannot exceed 72h')
    if policy['trading_deadline'] is not None:
        from datetime import datetime
        deadline = policy['trading_deadline']
        if isinstance(deadline, str):
            dt = datetime.fromisoformat(deadline.replace('Z', '+00:00'))
            if dt.tzinfo is None:
                raise ValueError('trading_deadline requires an explicit timezone')
            policy['trading_deadline'] = dt.timestamp()
        else:
            number(deadline)
    return policy


def load_catalog(md=None):
    catalog = read_json(state_dir(md) / 'catalog.json', {})
    if not isinstance(catalog, dict):
        raise ValueError('catalog must map SKU to metadata')
    for sku, item in catalog.items():
        if not isinstance(sku, str) or not sku or not isinstance(item, dict):
            raise ValueError('invalid catalog entry')
        if not isinstance(item.get('category'), str) or item.get('kind') not in ('food', 'goods'):
            raise ValueError('catalog entry lacks category/kind')
        if money(item.get('list_price')) <= 0:
            raise ValueError('catalog list price must be positive')
    return catalog


def hours_left(unit, now=None):
    expires = unit.get('expires_at')
    if expires is None:
        return None
    return (number(expires) - (time.time() if now is None else now)) / 3600


def effective_grade(unit, catalog=None, now=None):
    if unit.get('grade') not in ('A', 'B', 'C'):
        return 'expired'
    if unit.get('kind') != 'food':
        return unit.get('grade') if unit.get('grade') in ('A', 'B', 'C') else 'expired'
    remaining = hours_left(unit, now)
    if remaining is None or remaining <= 0:
        return 'expired'
    meta = (catalog or {}).get(unit.get('sku'), {})
    shelf = meta.get('shelf_hours') or unit.get('shelf_hours')
    if type(shelf) in (int, float) and math.isfinite(shelf) and shelf > 0:
        fraction = remaining / shelf
        return 'A' if fraction >= .8 else 'B' if fraction >= .45 else 'C'
    return 'C'


def load_inventory(md=None, now=None, max_age=45):
    path = state_dir(md) / 'inventory.json'
    now = time.time() if now is None else now
    try:
        stamp = path.stat().st_mtime
    except FileNotFoundError as exc:
        raise ValueError('inventory unavailable; no live stock decision') from exc
    if now - stamp > max_age or stamp - now > 60:
        raise ValueError('inventory snapshot is stale or from the future')
    inv = read_json(path)
    if not isinstance(inv, dict) or inv.get('round') != 3 or not isinstance(inv.get('units'), list):
        raise ValueError('Round 3 inventory required')
    money(inv.get('balance'))
    seen = set()
    for unit in inv['units']:
        if not isinstance(unit, dict) or not isinstance(unit.get('unit'), str) or not unit['unit']:
            raise ValueError('unit identity required')
        if unit['unit'] in seen:
            raise ValueError('duplicate inventory unit')
        seen.add(unit['unit'])
        if not isinstance(unit.get('sku'), str) or unit.get('kind') not in ('food', 'goods'):
            raise ValueError('unit SKU/kind required')
        money(unit.get('cost'))
        money(unit.get('list_price'))
        number(unit.get('supplied_at'))
        if unit['kind'] == 'food':
            number(unit.get('expires_at'))
    return inv


def require_live():
    if os.environ.get('R3_ENABLE_LIVE') != '1':
        raise SystemExit('Round 3 is locked. Deployment is not enabled; use offline tests or --dry-run.')
