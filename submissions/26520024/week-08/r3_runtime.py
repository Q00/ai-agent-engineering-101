"""Bounded model use and explicitly logged, validated degraded operation."""
import time
import uuid

from core import emit
from r3_common import atomic_json, load_policy, read_json, state_dir
from r3_memory import Memory


def choose(md, decision, system, state, validator, purpose, deadline, fallback, context=None):
    md = state_dir(md)
    policy = load_policy(md)
    def call(limit):
        if purpose.startswith('r3-'):
            return decision(system, state, validator, purpose, limit, context=context or {})
        return decision(system, state, validator, purpose, limit, context or {})
    if not policy['fallback_enabled']:
        return validator(call(deadline))
    marker = md / 'model-circuit.json'
    circuit = read_json(marker, {})
    # Never clear a failure on success: a concurrent success must not erase backoff.
    reason = 'circuit_open'
    if time.time() >= circuit.get('retry_after', 0):
        try:
            limit = min(deadline - 2, time.monotonic() + policy['model_call_seconds'])
            if limit <= time.monotonic():
                raise TimeoutError('No model time remains')
            return validator(call(limit))
        except (RuntimeError, ValueError, TypeError, KeyError, OSError, TimeoutError) as exc:
            reason = type(exc).__name__
            atomic_json(marker, {'retry_after': time.time() + policy['model_cooldown_seconds'],
                                 'reason': reason, 'purpose': purpose})
    if time.monotonic() >= deadline:
        raise TimeoutError('No time remains for validated fallback')
    result = validator(fallback())
    with Memory(md) as memory:
        memory.record('policy_fallback', uuid.uuid4().hex,
                      {'purpose': purpose, 'reason': reason, 'decision_source': 'deterministic_policy'})
    emit('r3_policy_fallback', purpose=purpose, reason=reason,
         decision_source='deterministic_policy')
    return result
