"""Conservative quality critic from our matured, verified purchases.

Peer grade claims are usable ONLY with the same grade required at checkout.
Public cross-sectional rates remain corroborating evidence, not causal estimates.
"""
import math
import time


def wilson(k, n, upper=True):
    if not n:
        return 1 if upper else 0
    z = 2.576  # 99% binomial interval; not protection against distribution shifts.
    centre = k / n + z * z / (2 * n)
    spread = z * math.sqrt(k / n * (1 - k / n) / n + z * z / (4 * n * n))
    return max(0, min(1, (centre + (spread if upper else -spread)) / (1 + z * z / n)))


def quality_book(journal, now=None):
    now = time.time() if now is None else now
    cached = journal.get('quality_risk_v4', {})
    if 0 <= now - cached.get('at', -1000) < 300:
        return cached['grades']
    # Reconciliation can append a true break AFTER a provisional mature result.
    # Any broken result wins; ambiguous attribution cannot become a healthy label.
    outcomes = {}
    for e in journal.events('outcome', 10000):
        previous = outcomes.get(e.get('order'))
        if previous is None or e.get('broken'):
            outcomes[e.get('order')] = e
    ambiguous = {o for e in journal.events('ambiguous_break', 2000) for o in e.get('orders', [])}
    breaks = {e.get('unit'): e for e in journal.events('public_broken', 20000)
              if 0 <= now - e['_ts'] < 172800 and e.get('unit')}
    grouped = {}
    for e in journal.events('purchase', 10000):
        if (e.get('kind') != 'goods' or e.get('grade') not in ('A', 'B', 'C') or
                not 21600 <= now - e.get('paid_at', now) <= 172800):
            continue
        outcome = outcomes.get(e.get('order'))
        exact = breaks.get(e.get('unit'))
        if e.get('order') in ambiguous and not exact:
            continue
        if not exact and (not outcome or not (outcome.get('matured') or outcome.get('broken'))):
            continue
        row = grouped.setdefault(e['grade'], {'n': 0, 'broken': 0, 'within3': 0})
        row['n'] += 1
        row['broken'] += bool(exact or outcome.get('broken'))
        if exact and type(exact.get('hours_after_sale')) in (int, float) and 0 <= exact['hours_after_sale'] <= 3:
            row['within3'] += 1
    journal.put('quality_risk_v4', {'at': now, 'grades': grouped})
    return grouped


def goods_risk(journal, grade, warranty, config, now=None):
    row = quality_book(journal, now).get(grade, {})
    n, k = row.get('n', 0), row.get('broken', 0)
    failure = config['unknown_goods_failure_stress']
    if n >= 10:
        failure = max(.05, wilson(k, n))
    # At least six hours covers the entire published break horizon. Partial
    # cover needs observed timings; never assume uniform failure times.
    covered = failure if warranty >= 6 else (wilson(row.get('within3', 0), n, False)
                                            if warranty >= 3 and n >= 20 else 0)
    return {'failure_upper': failure, 'covered_lower': min(failure, covered),
            'grade': grade, 'mature_samples': n, 'observed_broken': k,
            'source': 'own matured verified grade cohort, 48h; 99% Wilson bounds' if n >= 10 else
                      'explicit unknown-risk scenario; sparse or unspecified grade',
            'checkout_must_verify_grade': grade in ('A', 'B', 'C')}
