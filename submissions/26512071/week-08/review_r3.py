"""Post-payment review from kit's actual settled facts; forum report only via opt-in forum_r3."""
import argparse
import json
from pathlib import Path


def review(deal):
    settled=deal.get('settled') or {}
    if not settled.get('ledger'):
        return {'stars':3,'review':'No Round 3 settlement evidence available; quality not assessed.'}
    grade=settled.get('grade')
    honest=settled.get('claim_honest')
    stars=1 if honest is False else 5 if grade=='A' else 4 if grade=='B' else 2
    return {'stars':stars,'review':f"Paid settlement #{settled['ledger']}: SKU {settled.get('sku')}, delivered grade {grade}, claim_honest={honest}. Future durability not yet known."}

def review_file(path):
    path=Path(path)
    deal=json.loads(path.read_text(encoding='utf-8'))
    try:
        from evidence_memory import sync
        sync(path.parent)
    except (OSError,ValueError,TypeError,KeyError):
        pass  # Preserve truthful review even when memory is locked/corrupt.
    try:
        import forum_terra
        terra = forum_terra.active(path.parent)
    except Exception:
        terra = False
    try:
        if terra:
            # terra-forum-v1 replaces the template report while active (never both): readback first, then one decision.
            forum_terra.reconcile(path.parent)
            forum_terra.maybe_post(path.parent)
        else:
            import forum_r3
            if forum_r3.enabled(path.parent):
                forum_r3.maybe_report(path.parent)
    except Exception:
        pass  # Opt-in forum side effect can never change the review.
    return review(deal)


if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--deal',required=True)
    try: result=review_file(ap.parse_args().deal)
    except (OSError,ValueError,TypeError): result=review({})
    print(json.dumps(result,ensure_ascii=False))
