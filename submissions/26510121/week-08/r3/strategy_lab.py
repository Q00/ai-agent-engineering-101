"""LLM proposals enter bounded trials; only existing mature evaluators promote."""
import time
from .state import identity


def validate_buyer(patch):
    if not isinstance(patch, dict) or set(patch) != {
            'buyer_contract_probe_percent', 'buyer_contract_gap_power', 'buyer_contract_diverse_quotes'}:
        return False
    return (type(patch['buyer_contract_probe_percent']) is int and 2 <= patch['buyer_contract_probe_percent'] <= 20
            and type(patch['buyer_contract_gap_power']) is int and 1 <= patch['buyer_contract_gap_power'] <= 3
            and type(patch['buyer_contract_diverse_quotes']) is bool)


def profiles(journal):
    rules = journal.get('market_rules', {}).get('sha256')
    return {key: row['patch'] for key, row in journal.get('research-buyer-candidates', {}).items()
            if row.get('rules_hash') == rules and row.get('critic_approved') is True and validate_buyer(row.get('patch'))}


def register(journal, candidate, rules_hash, evidence, now=None):
    now = time.time() if now is None else now
    if rules_hash != journal.get('market_rules', {}).get('sha256'):
        return None
    patch = {'buyer_contract_probe_percent': candidate['buyer_probe_percent'],
             'buyer_contract_gap_power': candidate['buyer_gap_power'],
             'buyer_contract_diverse_quotes': candidate['buyer_diverse_quotes']}
    if not validate_buyer(patch):
        return None
    from .evolution import PROFILES
    if patch in PROFILES.values():
        return None
    key = 'research_' + identity([rules_hash, patch])[:16]
    rows = journal.get('research-buyer-candidates', {})
    if key not in rows:
        # Do not evict a profile an in-flight/active experiment may reference.
        if len(rows) >= 8:
            return None
        rows[key] = {'at': now, 'patch': patch, 'rules_hash': rules_hash, 'critic_approved': True,
                     'evidence': evidence, 'status': 'queued_for_existing_mature_evaluator'}
        journal.put('research-buyer-candidates', rows)
        journal.add('research_strategy_queued', dict(rows[key], profile=key), role='buyer')
    return key


def next_candidate(journal, champion, previous=None):
    from .evolution import PROFILES
    dynamic = profiles(journal)
    for name in reversed(list(dynamic)):
        if name not in (champion, previous):
            return name
    return next((name for name in PROFILES if name not in (champion, previous)), 'coverage_probe')


def shop_proposal(journal, config):
    row = journal.get('research-shop-candidate', {})
    value = row.get('service_level')
    if (not config.get('market_cycle_enabled') or row.get('rules_hash') != journal.get('market_rules', {}).get('sha256')
            or row.get('critic_approved') is not True or type(value) not in (int, float) or not .85 <= value <= .95
            or value == config.get('shop_stock_service_level', .9) or time.time() - row.get('at', 0) > 7200):
        return None
    from .autonomy import rule_effect
    if rule_effect(config, 'shop_stock_service_level', [config.get('shop_stock_service_level', .9), value])['status'] != 'behavior_changes':
        return None
    return value
