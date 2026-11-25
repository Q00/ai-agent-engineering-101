"""Truthful quality snapshots; claims must survive the reserved payment interval."""
import json
import math
from pathlib import Path


def quality(unit, market_dir, now, hold_seconds=600):
    grade = unit.get('grade')
    if unit.get('kind') == 'goods':
        return {'grade':grade, 'claim':grade if grade in ('A','B','C') else None,
                'hours_left':None, 'reason':'fixed_goods_grade'}
    try:
        catalog = json.loads((Path(market_dir)/'catalog.json').read_text())
        shelf = catalog[unit['sku']]['shelf_hours']
        expiry = unit['expires_at']
        if (type(shelf) not in (int,float) or type(expiry) not in (int,float)
                or not math.isfinite(shelf) or not math.isfinite(expiry) or shelf <= 0):
            raise ValueError('invalid shelf life')
        left = expiry-now
        def at(seconds):
            ratio = seconds/(shelf*3600)
            return 'A' if ratio >= .8 else 'B' if ratio >= .45 else 'C'
        current = at(left)
        safe = left > hold_seconds+30 and current == at(left-hold_seconds-30)
        return {'grade':current, 'claim':current if safe else None,
                'hours_left':max(0.,math.floor(left/36)/100),
                'reason':'stable_through_receipt_hold' if safe else 'food_boundary_or_expiry_during_hold'}
    except (OSError,ValueError,KeyError,TypeError):
        return {'grade':grade, 'claim':None, 'hours_left':None,
                'reason':'food_shelf_life_unavailable'}


def description(unit, view):
    text = f"SKU {unit['sku']}; grade {view['grade']} at quote"
    if view['hours_left'] is not None:
        text += f"; shelf life left {view['hours_left']:.2f}h"
    return text + '; certificate included'
