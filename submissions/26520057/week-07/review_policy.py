"""Conservative local purchasing preference, not a finding of reviewer misconduct."""
import json
import re
from pathlib import Path

NORMAL = re.compile(r"receipt (?:matched|matches|agrees)|receipt.*matched the (?:agreed|final)|signed receipt matched", re.I)
REASON = re.compile(r"\b(?:but|however|although|slow|delay|expensive|overpriced|damage|broken|wrong|mismatch|missing|unfair|manipulation|quality|budget|offers|negotiation|haggling)\b", re.I)


def exclusions(rows):
    """Require >=3 unexplained 1/2-star normal-trade reviews, >=75% of an author's sample."""
    authors = {}
    seen = set()
    for r in rows:
        key = (r.get('author'), r.get('ts'), r.get('shop'), r.get('review'))
        if key in seen:
            continue
        seen.add(key)
        owner = (r.get('owner') or {}).get('login')
        if not owner:  # Do not guess which shop an anonymous buyer owns.
            continue
        bucket = authors.setdefault(owner.casefold(), {'total': 0, 'suspicious': []})
        bucket['total'] += 1
        text = r.get('review') or ''
        if r.get('stars') in (1, 2) and NORMAL.search(text) and not REASON.search(text):
            bucket['suspicious'].append(r)
    return {owner: x for owner, x in authors.items()
            if len(x['suspicious']) >= 3 and len(x['suspicious']) / x['total'] >= .75}


def filter_reviewers(shops, board, path):
    path = Path(path)
    try:
        previous = json.loads(path.read_text())
    except (OSError, ValueError):
        previous = {}
    rows = previous.get('rows', []) if previous.get('round') == board['round'] else []
    unique = {(r.get('author'), r.get('ts'), r.get('shop'), r.get('review')): r
              for r in rows + board.get('reviews', [])}
    rows = sorted(unique.values(), key=lambda r: r.get('ts', 0))[-1000:]
    denied = exclusions(rows)
    owners = {s['name']: ((s.get('owner') or {}).get('login') or '').casefold()
              for s in board['shops']}
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps({'round': board['round'], 'rows': rows}, ensure_ascii=False))
    temp.replace(path)
    kept = []
    for shop in shops:
        evidence = denied.get(owners.get(shop['name']))
        if evidence:
            print(f"[review-policy] skip {shop['name']}: {len(evidence['suspicious'])}/{evidence['total']} "
                  "observed reviews were unexplained 1/2-star normal-trade ratings; heuristic only", flush=True)
        else:
            kept.append(shop)
    print(f"[review-policy] sampled {len(rows)} reviews; excluded {len(shops)-len(kept)} candidates", flush=True)
    return kept
