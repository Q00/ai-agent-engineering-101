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
    from .outcomes import latest
    outcomes = latest(journal)
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
        if e.get('order') in ambiguous and not exact and not (outcome or {}).get('canonical'):
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


def shop_refund_risk(journal, grade, warranty, config, now=None):
    """Seller liability needs an UPPER covered-loss bound, unlike buyer recovery.

    Only fully observed six-hour own-sale cohorts enter the denominator.
    Unrefunded breaks still count when estimating a newly offered warranty.
    """
    now = time.time() if now is None else now
    if warranty <= 0:
        return {'refund_upper': 0, 'mature_samples': 0, 'covered_breaks': 0,
                'source': 'no warranty liability'}
    cached = journal.get('shop_quality_cohorts_v9', {})
    if not 0 <= now-cached.get('at', -1000) < 300:
        breaks = {e.get('unit'): e for e in journal.events('public_broken', 20000)
                  if 0 <= now-e['_ts'] <= 172800 and e.get('unit')}
        cohorts = {}
        seen = set()
        catalog = journal.snapshot('catalog.json', {})
        for e in journal.events('shop_settled', 10000):
            unit = e.get('unit'); g = e.get('grade')
            age = now-e.get('paid_at', e['_ts'])
            if (catalog.get(e.get('sku'),{}).get('kind')!='goods' or unit in seen or
                    g not in ('A', 'B', 'C') or not 21600 <= age <= 172800):
                continue
            seen.add(unit)
            row = cohorts.setdefault(g, {'n': 0, 'broken': 0, 'within3': 0, 'unknown_time': 0})
            row['n'] += 1
            broken = breaks.get(unit)
            if broken:
                row['broken'] += 1
                hours = broken.get('hours_after_sale')
                if type(hours) in (int, float) and 0 <= hours <= 3:
                    row['within3'] += 1
                elif type(hours) not in (int, float):
                    row['unknown_time'] += 1
        cached = {'at': now, 'grades': cohorts}
        journal.put('shop_quality_cohorts_v9', cached)
    row = cached['grades'].get(grade, {})
    n = row.get('n', 0)
    k = row.get('broken', 0) if warranty >= 6 else row.get('within3', 0)+row.get('unknown_time', 0)
    # Choices are 0/3/6h; unsupported partial cover retains the full-loss prior.
    if warranty not in (3, 6) and warranty < 6:
        k = row.get('broken', 0)
    upper = max(.01, wilson(k, n)) if n >= 20 else config['unknown_goods_failure_stress']
    return {'refund_upper': upper, 'mature_samples': n, 'covered_breaks': k,
            'grade': grade, 'warranty_hours': warranty,
            'source': 'own mature seller units, 48h; 99% Wilson upper bound' if n >= 20 else
                      'sparse seller cohort; explicit unknown-risk scenario'}
