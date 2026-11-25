"""Model cadence is separate from observation, expiry checks and experiment evaluation."""
import hashlib
import json


def evidence_key(obs, state):
    # IDs and outcome changes detect new evidence even when rolling counts stay flat.
    # Exclude clocks, snapshot IDs, ages and latency noise: those advance without a new trade.
    tasks = sorted(obs.get('task_sample', []), key=lambda r: str(r.get('task_id', '')))
    candidates = [{k:r.get(k) for k in ('sku','grade','warranty_hours','eligible','available_units','reasons')}
                  for r in obs.get('candidates', [])]
    current = {k:obs.get('current', {}).get(k) for k in ('observed_tasks', 'confirmed_sales',
        'revenue', 'unconfirmed_receipts', 'mature_sales', 'refunded_sales', 'unknown_economics',
        'technical_errors', 'observed_refunds')}
    payload = {'round':obs.get('round'), 'rules':obs.get('rules_version'), 'policy':state['version'],
               'tasks':tasks, 'current':current,
               'candidates':sorted(candidates, key=lambda r: str((r['sku'],r['grade'],r['warranty_hours'])))}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def model_gate(obs, state, control, cfg, *, now, event=False, requested=False):
    fingerprint = evidence_key(obs, state)
    previous = control.get('last_model_at')
    if previous is not None and now < previous:
        return False, 'model_clock_regression', fingerprint
    if previous is not None and now-previous < cfg['event_min_seconds']:
        return False, 'model_minimum_spacing', fingerprint
    # Authenticated requests and freshly observed technical events can bypass
    # regular cadence, but never existing stale-data guards or the daily budget.
    if event or requested:
        return True, 'requested_or_technical_event', fingerprint
    if not any(r.get('eligible') is True for r in obs.get('candidates', [])):
        return False, 'no_eligible_experiment_targets', fingerprint
    if fingerprint == control.get('last_model_evidence'):
        return False, 'unchanged_evidence', fingerprint
    if previous is not None and now-previous < cfg.get('model_interval_seconds', 7200):
        return False, 'model_interval_wait', fingerprint
    return True, 'fresh_evidence_due', fingerprint
