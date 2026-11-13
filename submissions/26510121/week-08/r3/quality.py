"""Temporal holdout gating for account-linked paid-review predictions.

This is a predictive planning surrogate, never a causal effect of a price or
warranty, and never an exact reconstruction of the official stars_rank formula.
Failed/unsupported models remain shadow and cannot alter a quote's objective.
"""
from collections import defaultdict
import statistics
import time
from .state import atomic_json, identity

FILE = 'quality-model-v16.json'


def bucket(grade, warranty, ratio):
    return str(grade)+':'+('insured' if warranty >= 6 else 'short' if warranty > 0 else 'none')+':'+str(min(15,max(0,int(ratio*10))))


def fit(rows):
    accounts = defaultdict(list); groups = defaultdict(lambda: defaultdict(list))
    for r in rows:
        accounts[r['author']].append(r['stars']); groups[r['bucket']][r['author']].append(r['stars'])
    baseline = statistics.mean(statistics.mean(v) for v in accounts.values()) if accounts else 3
    models = {}
    for key, users in groups.items():
        n = sum(map(len, users.values()))
        if n < 8 or len(users) < 3: continue
        mean = statistics.mean(statistics.mean(v) for v in users.values())
        models[key] = {'mean': (mean*len(users)+baseline*3)/(len(users)+3), 'accounts': len(users), 'reviews': n}
    return {'baseline': baseline, 'groups': models, 'accounts': len(accounts)}


def predict(model, row):
    return model['groups'].get(row['bucket'], {}).get('mean', model['baseline'])


def account_loss(rows, model, baseline=False):
    errors = defaultdict(list)
    for r in rows:
        expected = model['baseline'] if baseline else predict(model, r)
        errors[r['author']].append((r['stars']-expected)**2)
    return statistics.mean(statistics.mean(v) for v in errors.values()) if errors else None


def update(journal, config, now=None):
    if not config.get('objective_feedback_enabled'): return None
    now = time.time() if now is None else now
    old = journal.snapshot(FILE, {})
    if 0 <= now-old.get('at', 0) < 60: return old
    catalog = journal.snapshot('catalog.json', {})
    sales = {r['_event_id'].partition('settlement:')[2]: r for r in journal.events('shop_settled', 10000)}
    deduplicated = {}
    for review in journal.events('shop_review_observed', 10000):
        if (review.get('source') != 'verified_market_result_and_accepted_own_task'
                or not review.get('identity_known') or not review.get('author') or not 0 <= now-review['_ts'] <= 172800):
            continue
        sale = sales.get(str(review.get('ledger')))
        value = catalog.get((sale or {}).get('sku'), {}).get('list_price')
        if not sale or type(value) not in (int, float) or value <= 0: continue
        stars = review.get('stars')
        if type(stars) not in (int, float) or not 1 <= stars <= 5: continue
        deduplicated.setdefault(str(review['ledger']), {'author': review['author'], 'stars': stars, 'at': review['_ts'],
            'bucket': bucket(sale.get('grade'), sale.get('warranty_hours') or 0, sale['price']/value)})
    rows = sorted(deduplicated.values(), key=lambda r:r['at'])
    split = int(len(rows)*.7); train, test = rows[:split], rows[split:]
    model = fit(train)
    mse, baseline = account_loss(test, model), account_loss(test, model, True)
    test_accounts = len({r['author'] for r in test})
    covered = sum(r['bucket'] in model['groups'] for r in test)
    ready = (len(train) >= 50 and len(test) >= 20 and model['accounts'] >= 8 and test_accounts >= 5
             and covered >= len(test)*.7 and mse is not None and baseline > 0 and mse < baseline*.95)
    summary = {'at': now, 'mode': 'validated_predictive_surrogate' if ready else 'shadow',
        'train_reviews': len(train), 'holdout_reviews': len(test), 'train_accounts': model['accounts'],
        'holdout_accounts': test_accounts, 'covered_holdout_reviews': covered,
        'holdout_mse': mse, 'baseline_mse': baseline, 'temporal_split': .7,
        'official_stars_rank_mapping_verified': False, 'not_causal': True}
    full = fit(rows)
    result = {'schema': 1, 'at': now, 'expires_at': now+300, 'rules_hash': journal.get('market_rules', {}).get('sha256'),
        'summary': summary, 'model': full, 'uncertainty_stars': (mse**.5 if mse is not None else 5)}
    result['checksum'] = identity(result)
    atomic_json(journal.md/FILE, result); journal.put('quality-holdout-summary', summary)
    journal.add('quality_holdout_evaluation', summary)
    return result


def augment(journal, config, unit, candidates, now=None):
    if config.get('shop_reputation_v24_enabled'):
        from .reputation import augment as account_augment
        return account_augment(journal,config,unit,candidates,now)
    if not config.get('objective_feedback_enabled'): return candidates
    now = time.time() if now is None else now
    from .decision_view import fact
    row = fact(journal, 'quality-model-v16', lambda: journal.snapshot(FILE, {}))
    if (row.get('schema') != 1 or row.get('summary', {}).get('mode') != 'validated_predictive_surrogate'
            or not 0<=now-journal.get('board_checked_at',0)<180
            or row.get('rules_hash') != journal.get('market_rules', {}).get('sha256')
            or not 0 <= now-row.get('at', 0) < 300 or now >= row.get('expires_at', 0)
            or row.get('checksum') != identity({k:v for k,v in row.items() if k != 'checksum'})):
        return candidates
    own = next((r for r in journal.snapshot('board.json', {}).get('shops', []) if r.get('name') == 'seochanit'), {})
    current = own.get('stars')
    if type(current) not in (int, float): return candidates
    for candidate in candidates:
        if candidate.get('action') == 'hold': continue
        key = bucket(unit.get('grade'), candidate.get('warranty_hours') or 0, candidate['price']/max(1,unit['list_price']))
        model = row['model']['groups'].get(key)
        if model is None: continue
        difference = model['mean']-current
        # Refuse to monetize a point estimate smaller than its observed RMS
        # error. Use the large published review count to avoid assuming every
        # new review is an independent new voting account.
        conservative = (1 if difference >= 0 else -1)*max(0, abs(difference)-row['uncertainty_stars'])
        completion = candidate.get('evidence', {}).get('quote_completion', {}).get('completion', 1)
        delta = max(-.001, min(.001, completion*conservative/max(20, own.get('reviews', 0)+1)))
        candidate['expected_delta']['stars_rank'] = delta
        candidate['evidence']['rating_model'] = {'prediction': model['mean'], 'holdout_mse': row['summary']['holdout_mse'],
            'baseline_mse': row['summary']['baseline_mse'], 'planning_stars_rank_delta': delta,
            'official_mapping_verified': False, 'not_causal': True}
    return candidates
