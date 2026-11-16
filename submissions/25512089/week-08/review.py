"""Optional post-settlement review hook. Reviews actual fulfillment only."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def summarize(deal: dict[str, Any]) -> dict[str, Any]:
    settled = deal.get('settled') or {}
    if not isinstance(settled, dict):
        return {'stars': 3, 'review': 'Settlement details unavailable; no reliable quality judgment.'}
    if settled.get('claim_honest') is False:
        return {'stars': 1, 'review': 'Grade claim was contradicted by settlement evidence.'}
    value = settled.get('value')
    if isinstance(value, (int, float)) and not isinstance(value, bool) and value <= 0:
        return {'stars': 2, 'review': 'Purchased item had no usable value for this order.'}
    grade = settled.get('grade')
    if grade in ('A', 'B'):
        return {'stars': 5, 'review': f'Settled product, verified grade {grade}.'}
    if grade == 'C':
        return {'stars': 4, 'review': 'Settled product arrived with grade C as recorded.'}
    return {'stars': 4, 'review': 'Settlement confirmed; no verified quality issue.'}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--deal', required=True)
    args = parser.parse_args()
    try:
        deal = json.loads(Path(args.deal).read_text(encoding='utf-8'))
        if not isinstance(deal, dict):
            deal = {}
    except (OSError, ValueError):
        deal = {}
    print(json.dumps(summarize(deal), ensure_ascii=False))


if __name__ == '__main__':
    main()
