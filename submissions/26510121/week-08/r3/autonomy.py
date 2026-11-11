"""Event-driven analytical roles and typed skill composition, with no LLM calls.

Only trusted primitives may be composed. New trade rules remain proposals until
independent economic evidence exists; official rules and money guards are fixed.
"""
from collections import Counter
import hashlib
import json
import math
import threading
import time

from .state import identity, atomic_json

REVISION = 'autonomy-v14-fast-facts-1'
ROLES = {
    'shop_main': 'fresh inventory, reservations, quote and checkout guards',
    'buyer_main': 'bounded execution, receipt verification and one payment path',
    'director': 'deduplicate issues, bound work and retain prior valid context',
    'need_assessor': 'distinguish missing data, repeated failure and missing capability',
    'skill_router': 'select applicable, versioned trusted primitive plans or none',
    'context_curator': 'typed facts with provenance, version and expiry; no raw chats',
    'market_portfolio': 'external demand, stock exposure, cash and inventory proposals',
    'buyer_path': 'feasible contracts, technical failures and bounded recovery',
    'quality_reputation': 'mature unit quality and account-linked reviews',
    'tool_builder': 'compose shallow analytical skills from trusted primitives',
    'evaluator': 'check data contracts and evidence; never self-approve trade rules',
    'release_controller': 'publish validated facts; defer unverified trade changes',
}
PRIMITIVES = {'measure', 'diagnose', 'propose', 'validate_facts'}
ISSUES = {'stock_coverage', 'buyer_price_gap', 'buyer_transport', 'buyer_preparation',
          'shop_rating_holdout', 'feed_stale', 'buyer_protocol'}


class BackgroundLane:
    """One daemon worker and at most one job; repeated ticks are coalesced."""
    def __init__(self, name):
        self.name, self.busy, self.coalesced, self.last_error = name, False, 0, None
        self.lock = threading.Lock()

    def submit(self, function):
        with self.lock:
            if self.busy:
                self.coalesced += 1
                return False
            self.busy = True
        def run():
            try:
                self.last_error = None
                function()
            except Exception as exc:
                self.last_error = type(exc).__name__
            finally:
                with self.lock:
                    self.busy = False
        threading.Thread(target=run, name=self.name, daemon=True).start()
        return True


def compile_skill(issue, rules_hash):
    if issue not in ISSUES:
        raise ValueError('unsupported_skill_issue')
    steps = (['measure', 'diagnose', 'validate_facts'] if issue in {'feed_stale', 'shop_rating_holdout'}
             else ['measure', 'diagnose', 'propose', 'validate_facts'])
    skill = {'issue': issue, 'steps': steps, 'rules_hash': rules_hash,
             'effects': 'analysis_only', 'max_steps': 4, 'revision': REVISION}
    skill['id'] = identity(skill)
    return skill


def validate_skill(skill):
    if skill.get('issue') not in ISSUES or skill.get('effects') != 'analysis_only':
        return False
    steps = skill.get('steps', [])
    return (1 <= len(steps) <= 4 and all(s in PRIMITIVES for s in steps)
            and steps[-1] == 'validate_facts' and skill.get('revision') == REVISION)


def rule_effect(config, parameter, values):
    """Reject changes hidden by higher-priority rules before any economic trial."""
    from .shop_growth import target_stock, inventory_limit
    signatures = []
    for value in values:
        cfg = dict(config, **{parameter: value})
        if parameter == 'inventory_target_units':
            signature = inventory_limit(cfg)
        elif parameter == 'shop_stock_service_level':
            if type(value) not in (int, float) or not .8 <= value <= .99:
                return {'status': 'rejected_out_of_bounds'}
            signature = [target_stock(rate, paid, cfg) for rate in (1, 5, 10, 20) for paid in (0, 3)]
        else:
            return {'status': 'requires_domain_evaluation'}
        signatures.append(signature)
    return {'status': 'no_effect' if len({identity(s) for s in signatures}) < 2 else 'behavior_changes',
            'signatures': signatures, 'economic_superiority_verified': False}


def run_skill(skill, facts, config=None):
    """Interpret a generated data plan; there is no eval/import/shell capability."""
    if not validate_skill(skill):
        raise ValueError('invalid_skill_plan')
    result = {'skill_id': skill['id'], 'issue': skill['issue'], 'completed_steps': []}
    for step in skill['steps']:
        if step == 'measure':
            result['fact_fields'] = sorted(facts)
        elif step == 'diagnose':
            result['diagnosis'] = skill['issue']
        elif step == 'propose':
            parameter, values = {
                'stock_coverage': ('shop_stock_service_level', [.85, .9, .95]),
                'buyer_price_gap': ('buyer_contract_gap_power', [1, 2, 3]),
                'buyer_transport': ('buyer_rfq_goods_seconds', [3, 4, 5]),
            }.get(skill['issue'], (None, []))
            result['proposal'] = {'parameter': parameter, 'candidate_values': values,
                'status': 'shadow_requires_independent_validation', 'execution_allowed': False}
            if parameter and config:
                result['proposal']['structural_check'] = rule_effect(config, parameter, values)
        elif step == 'validate_facts':
            result['analysis_contract_valid'] = all(type(v) in (int, float) and math.isfinite(v) for v in facts.values())
        result['completed_steps'].append(step)
    return result


def policy_version(config):
    return identity({k: config.get(k) for k in ('version', 'shop_strategy_revision', 'buyer_strategy_revision',
                                             'execution_revision', 'autonomy_v14_enabled')})


def valid_packet(packet, rules_hash, config, now=None):
    try:
        return _valid_packet(packet, rules_hash, config, now)
    except (TypeError, ValueError, KeyError, OverflowError):
        return False


def _valid_packet(packet, rules_hash, config, now=None):
    now = time.time() if now is None else now
    if not isinstance(packet, dict) or set(packet) != {'schema', 'as_of', 'expires_at', 'rules_hash',
            'policy_version', 'data_version', 'evidence_ids', 'fact_or_hypothesis', 'facts', 'issues', 'skill_ids', 'source_as_of'}:
        return False
    if (packet['schema'] != REVISION or packet['rules_hash'] != rules_hash or not rules_hash
            or packet['policy_version'] != policy_version(config) or packet['fact_or_hypothesis'] != 'observed_facts'
            or not 0 <= now - packet['as_of'] <= 180 or not now < packet['expires_at'] <= packet['as_of'] + 180):
        return False
    if set(packet['facts']) != {'stock_units', 'stock_skus', 'food_quotes', 'goods_quotes', 'goods_above_cap',
                                'preparation_p95', 'technical_failures', 'protocol_failures', 'verified_review_accounts'}:
        return False
    values = packet['facts'].values()
    return (all(type(x) in (int, float) and math.isfinite(x) and x >= 0 for x in values)
            and packet['facts']['stock_skus'] <= packet['facts']['stock_units']
            and packet['facts']['goods_above_cap'] <= packet['facts']['goods_quotes']
            and all(issue in ISSUES for issue in packet['issues'])
            and len(packet['issues']) <= 8 and len(packet['skill_ids']) <= 8
            and type(packet['data_version']) is int and packet['data_version'] >= 0
            and 0 < len(packet['evidence_ids']) <= 8
            and all(isinstance(x, str) and len(x) <= 160 for x in packet['evidence_ids'])
            and set(packet['source_as_of']) == {'inventory', 'framework', 'quality', 'feed'}
            and all(x is None or type(x) in (int, float) and math.isfinite(x) and x <= now + 5
                    for x in packet['source_as_of'].values())
            and len(json.dumps(packet, ensure_ascii=False).encode()) <= 3500)


def context(journal, config, now=None, role=None, rules_hash=None):
    if rules_hash is None:
        rules = journal.get('market_rules', {})
        rules_hash = rules.get('sha256') if isinstance(rules, dict) else None
    packet = journal.snapshot('autonomy-v14-context.json', {})
    if not valid_packet(packet, rules_hash, config, now):
        return None
    fields = ({'stock_units', 'stock_skus', 'verified_review_accounts'} if role == 'shop' else
              {'food_quotes', 'goods_quotes', 'goods_above_cap', 'technical_failures', 'protocol_failures', 'preparation_p95'})
    issues = [issue for issue in packet['issues'] if issue == 'feed_stale' or
              (issue.startswith('shop_') or issue.startswith('stock_')) == (role == 'shop')]
    return {k: v for k, v in dict(packet, facts={k: v for k, v in packet['facts'].items() if k in fields},
                                 issues=issues).items() if k != 'skill_ids'}


def update(journal, config, now=None):
    now = time.time() if now is None else now
    previous = journal.get('autonomy-v14:state', {})
    if 0 <= now - previous.get('at', 0) < 60:
        return previous
    rules = journal.get('market_rules', {})
    rule_hash = rules.get('sha256')
    if not rule_hash or rules.get('compatible') is not True:
        return {'status': 'deferred_rules_unverified'}
    feed = journal.get('public_feed_caught_up_at', 0)
    feed_current = 0 <= now - feed <= 180
    inventory = journal.snapshot('inventory.json', {})
    framework = journal.get('framework-plan', {})
    quality = journal.get('shop-rating-shadow', {})
    preparations = [r for r in journal.events('buyer_preparation', 200) if 0 <= now - r['_ts'] <= 3600]
    attempts = [r for r in journal.events('attempt', 1000) if 0 <= now - r['_ts'] <= 3600]
    durations = sorted(r['seconds'] for r in preparations if type(r.get('seconds')) in (int, float) and r['seconds'] >= 0)
    segments = framework.get('segments', {})
    goods, food = segments.get('goods', {}), segments.get('food', {})
    units = inventory.get('units', [])
    pos = (len(durations) - 1) * .95 if durations else 0
    lower = int(pos)
    preparation_p95 = (durations[lower] + (durations[min(lower + 1, len(durations) - 1)] - durations[lower]) * (pos - lower)) if durations else 0
    facts = {'stock_units': len(units), 'stock_skus': len({u['sku'] for u in units}),
        'food_quotes': food.get('quotes', 0), 'goods_quotes': goods.get('quotes', 0),
        'goods_above_cap': goods.get('above_cap', 0),
        'preparation_p95': preparation_p95,
        'technical_failures': sum(r.get('kind') in {'ReadTimeout', 'ConnectError', 'HTTPStatusError'} or
            r.get('kind') == 'rfq_failed' and r.get('error_type') in {'ReadTimeout', 'ConnectError', 'HTTPStatusError'} for r in attempts),
        'protocol_failures': sum(r.get('reason') in {'ambiguous_ask', 'quote_sku_mismatch', 'receipt_integrity_failure',
            'conflicting_offer_terms', 'quote_grade_changed', 'quote_warranty_changed', 'certificate_unavailable'} for r in attempts),
        'verified_review_accounts': quality.get('verified_accounts', 0)}
    issues = []
    if not feed_current:
        issues.append('feed_stale')
    if facts['stock_units'] < config.get('shop_inventory_ceiling', 180) // 2:
        issues.append('stock_coverage')
    if facts['goods_quotes'] >= 20 and facts['goods_above_cap'] / facts['goods_quotes'] > .8:
        issues.append('buyer_price_gap')
    if facts['technical_failures'] >= 5:
        issues.append('buyer_transport')
    if facts['protocol_failures'] >= 3:
        issues.append('buyer_protocol')
    if facts['preparation_p95'] > 1:
        issues.append('buyer_preparation')
    issues.append('shop_rating_holdout')  # No forecast becomes a confirmed rating gain.
    library = dict(previous.get('library', {}))
    for issue in issues:
        skill = compile_skill(issue, rule_hash)
        if not validate_skill(skill):
            raise ValueError('generated_skill_failed_contract')
        old = library.get(skill['id'], {})
        library[skill['id']] = dict(skill, uses=old.get('uses', 0) + 1, last_used=now)
    # Bounded active catalogue; retired definitions remain in append-only events.
    library = dict(sorted(library.items(), key=lambda pair: pair[1]['last_used'], reverse=True)[:24])
    selected = [row['id'] for row in library.values() if row['issue'] in issues and row['rules_hash'] == rule_hash]
    executions = [run_skill(library[skill_id], facts, config) for skill_id in selected]
    for execution in executions:
        journal.add('autonomy_v14_skill_execution', execution, role='system')
    evidence = ['snapshot:inventory.json', 'kv:framework-plan', 'kv:shop-rating-shadow']
    evidence += [r['_event_id'] for r in (preparations[:3] + attempts[:2]) if r.get('_event_id')]
    packet = {'schema': REVISION, 'as_of': now, 'expires_at': now + 180, 'rules_hash': rule_hash,
        'policy_version': policy_version(config), 'data_version': journal.get('public_ledger_seq'),
        'evidence_ids': evidence, 'fact_or_hypothesis': 'observed_facts', 'facts': facts,
        'issues': issues, 'skill_ids': selected}
    inventory_path = journal.md / 'inventory.json'
    packet['source_as_of'] = {'inventory': inventory_path.stat().st_mtime if inventory_path.exists() else None,
        'framework': framework.get('at'), 'quality': quality.get('at'), 'feed': feed or None}
    valid = valid_packet(packet, rule_hash, config, now)
    # These are hypotheses, never instructions Main may execute as new rules.
    candidates = [{'issue': issue, 'status': 'shadow_requires_economic_validation',
        'candidate_id': identity([rule_hash, issue]), 'self_approval_allowed': False}
        for issue in issues if issue in {'stock_coverage', 'buyer_price_gap'}]
    old_issues = set(previous.get('issues', []))
    resolved = sorted(old_issues - set(issues))
    result = {'at': now, 'revision': REVISION, 'status': 'published_facts' if valid and feed_current else 'deferred',
        'issues': issues, 'library': library, 'candidates': candidates,
        'resolved_observational_only': resolved, 'role_ids': list(ROLES),
        'executed_skills': len(executions),
        'paid_model_calls': 0, 'arbitrary_generated_code_enabled': False,
        'strategy_promotion_authority': 'existing_matured_champion_challenger_evaluator'}
    if valid and feed_current:
        journal.put('autonomy-v14:context', packet)
        atomic_json(journal.md / 'autonomy-v14-context.json', packet)
    journal.put('autonomy-v14:state', result)
    journal.add('autonomy_v14_evaluation', result, role='system')
    return result
