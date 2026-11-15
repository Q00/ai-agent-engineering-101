"""Single writer for bounded Shop-only overlays; never rewrites trading/buyer files.

Advisors persist proposals only. This store verifies evidence, baseline hash and
CAS version under flock, archives an immutable revision, then atomically moves
one active pointer. Readers fail to the existing shop policy, never stop trading.
"""
from contextlib import contextmanager
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import tempfile
import time
import uuid
import fcntl

ROOT = Path(__file__).resolve().parent
CONFIG_KEYS = {'schema', 'enabled', 'mode', 'interval_seconds', 'event_min_seconds',
               'model_enabled', 'model_profile', 'model_timeout_seconds', 'max_calls_per_day',
               'proposal_ttl_seconds', 'experiment_seconds', 'evaluation_seconds', 'min_tasks', 'min_settlements'}
SKU = re.compile(r'[GF][0-9a-f]{8}')
IDENTIFIER = re.compile(r'[a-zA-Z0-9_-]{1,100}')
HASH = re.compile(r'[0-9a-f]{64}')
REVISION = re.compile(r'(?:[0-9a-f]{32}|baseline-[0-9a-f]{20})')
COLLECTIONS = {'history', 'snapshots', 'decisions', 'requests', 'nonces',
               'evaluations', 'outcomes', 'outbox', 'observations', 'errors'}


def numeric(x):
    return type(x) in (int, float) and math.isfinite(x)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                   separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def matches(pattern, value):
    return isinstance(value, str) and pattern.fullmatch(value) is not None


def guard_state_path(path):
    """Reject redirected market state, allowing legitimate system/root aliases."""
    path = Path(os.path.abspath(path))
    parts = path.parts
    if '.market' in parts:
        index = parts.index('.market')
        for end in range(index + 1, len(parts) + 1):
            if Path(*parts[:end]).is_symlink():
                raise ValueError('symlink market state refused')
    if path.is_symlink():
        raise ValueError('symlink state refused')
    return path


def read_json(path, limit=4_000_000):
    path = guard_state_path(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as handle:
        if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
            raise ValueError('state must be a regular file')
        raw = handle.read(limit + 1)
    if len(raw) > limit:
        raise ValueError('oversize state')
    return json.loads(raw)


def atomic_write(path, value):
    """Unique temporary file, fsync, replace; interrupted writes keep old state."""
    path = guard_state_path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.is_symlink() or path.parent.is_symlink():
        raise ValueError('symlink destination refused')
    raw = (json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True) + '\n').encode()
    fd, tmp = tempfile.mkstemp(prefix='.ops-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as out:
            out.write(raw)
            out.flush()
            os.fsync(out.fileno())
        os.replace(tmp, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def config_at(root=ROOT):
    cfg = read_json(Path(root) / 'shop-ops-config.json', 8192)
    if not isinstance(cfg, dict) or set(cfg) != CONFIG_KEYS or type(cfg['schema']) is not int or cfg['schema'] != 1:
        raise ValueError('invalid shop operations config')
    if type(cfg['enabled']) is not bool or type(cfg['model_enabled']) is not bool:
        raise ValueError('explicit enable flags required')
    if cfg['mode'] not in ('propose', 'auto') or cfg['model_profile'] not in ('sol', 'luna', 'opus5.5'):
        raise ValueError('invalid operations mode/profile')
    for key, lo, hi in (
        ('interval_seconds', 1800, 86400), ('event_min_seconds', 300, 1800),
        ('model_timeout_seconds', 10, 60), ('max_calls_per_day', 1, 48),
        ('proposal_ttl_seconds', 60, 900), ('experiment_seconds', 3600, 21600),
        ('evaluation_seconds', 1800, 10800), ('min_tasks', 20, 200), ('min_settlements', 5, 100)):
        if type(cfg[key]) is not int or not lo <= cfg[key] <= hi:
            raise ValueError('invalid operations limit: ' + key)
    if cfg['evaluation_seconds'] > cfg['experiment_seconds']:
        raise ValueError('evaluation must precede expiry')
    return cfg


def baseline_hash(root):
    from shop_policy import policy_at
    return digest(policy_at(Path(root)))


def state_dir(root):
    return guard_state_path(Path(root) / '.market' / 'shop-ops')


def baseline_state(root):
    baseline = baseline_hash(root)
    return {'schema': 1, 'version': 'baseline-' + baseline[:20], 'baseline_hash': baseline,
            'changes': [], 'round': 3, 'created_at': 0, 'expires_at': 0, 'decision_id': None}


def validate_changes(changes):
    if not isinstance(changes, list) or not 1 <= len(changes) <= 2:
        raise ValueError('one or two SKU price experiments only')
    seen = set()
    for c in changes:
        if not isinstance(c, dict) or set(c) != {'sku', 'grade', 'warranty_hours', 'before', 'after'}:
            raise ValueError('unknown policy key')
        if not isinstance(c['sku'], str) or not SKU.fullmatch(c['sku']) or c['sku'] in seen:
            raise ValueError('invalid/duplicate SKU')
        if c['grade'] not in ('A', 'B') or type(c['warranty_hours']) is not int or not 0 <= c['warranty_hours'] <= 6:
            raise ValueError('C grade/warranty changes excluded')
        if type(c['before']) is not int or c['before'] != 0 or type(c['after']) is not int or c['after'] not in (-1, -2):
            raise ValueError('price-only delta at most two percent; no compounding')
        seen.add(c['sku'])


def current_state(root):
    path = state_dir(root) / 'active.json'
    if not path.exists():
        return baseline_state(root)
    s = read_json(path, 32768)
    required = {'schema', 'version', 'baseline_hash', 'changes', 'round', 'created_at',
                'expires_at', 'decision_id', 'parent_version', 'snapshot_id', 'rules_version', 'reason'}
    if (not isinstance(s, dict) or set(s) != required or type(s['schema']) is not int or s['schema'] != 1
            or not isinstance(s['version'], str) or not re.fullmatch(r'[0-9a-f]{32}', s['version'])
            or type(s['round']) is not int or s['round'] != 3
            or not isinstance(s['changes'], list)
            or not numeric(s['created_at']) or not numeric(s['expires_at'])
            or not s['created_at'] <= s['expires_at'] <= s['created_at'] + 21600):
        raise ValueError('invalid active policy')
    if (not matches(HASH, s['baseline_hash']) or not matches(REVISION, s['parent_version'])
            or s['rules_version'] != '3.6'
            or s['reason'] not in ('experiment', 'operator_rollback', 'technical_error', 'expired', 'evaluated_regression')
            or s['decision_id'] is not None and not matches(re.compile(r'[0-9a-f]{32}'), s['decision_id'])
            or s['snapshot_id'] is not None and not matches(HASH, s['snapshot_id'])
            or s['changes'] and (s['decision_id'] is None or s['snapshot_id'] is None)):
        raise ValueError('invalid active policy identity')
    if s['changes']:
        validate_changes(s['changes'])
    return s


def read_overlay(root=ROOT, *, now=None, rnd=3):
    now = time.time() if now is None else now
    try:
        if not config_at(root)['enabled']:
            return None
        s = current_state(root)
        if (s['round'] != rnd or s['baseline_hash'] != baseline_hash(root) or not s['changes']
                or not 0 <= now - s['created_at'] or now >= s['expires_at']):
            return None
        return s
    except (OSError, ValueError, KeyError, TypeError):
        return None


@contextmanager
def writer_lock(root):
    folder = state_dir(root)
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    if folder.is_symlink():
        raise ValueError('symlink operations directory')
    fd = os.open(folder / 'writer.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'a') as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield


def immutable_record(root, collection, identifier, value):
    if not isinstance(collection, str) or collection not in COLLECTIONS or not matches(IDENTIFIER, identifier):
        raise ValueError('invalid record identifier')
    path = guard_state_path(state_dir(root) / collection / (identifier + '.json'))
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    # Never overwrite decisions/snapshots even on a repeated request.
    raw = (json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False) + '\n').encode()
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as out:
        out.write(raw)
        out.flush()
        os.fsync(out.fileno())
    directory = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
    return path


def validate_snapshot(snapshot, now, *, expected=None):
    if not isinstance(snapshot, dict) or not numeric(snapshot.get('generated_at')):
        raise ValueError('invalid observation')
    if not 0 <= now - snapshot['generated_at'] <= 900:
        raise ValueError('stale observation')
    if expected is not None and snapshot.get('snapshot_id') != expected:
        raise ValueError('wrong observation')
    identifier = snapshot.get('snapshot_id')
    if not matches(HASH, identifier) or digest({k: v for k, v in snapshot.items() if k != 'snapshot_id'}) != identifier:
        raise ValueError('observation content hash mismatch')
    if snapshot.get('round') != 3 or snapshot.get('rules_version') != '3.6':
        raise ValueError('unsupported round/rules')
    if snapshot.get('application_ready') is not True:
        raise ValueError('incomplete or stale evidence')


def validate_proposal(proposal, cfg):
    fields = {'schema', 'decision_id', 'base_version', 'baseline_hash', 'snapshot_id', 'rules_version',
              'created_at', 'expires_at', 'action', 'family', 'changes', 'diagnosis', 'evidence',
              'expected_effect', 'evaluation', 'stop_conditions', 'request_id'}
    if (not isinstance(proposal, dict) or set(proposal) != fields
            or type(proposal['schema']) is not int or proposal['schema'] != 1):
        raise ValueError('invalid proposal envelope')
    if (not matches(re.compile(r'[0-9a-f]{32}'), proposal['decision_id'])
            or not matches(REVISION, proposal['base_version'])
            or not matches(HASH, proposal['baseline_hash']) or not matches(HASH, proposal['snapshot_id'])
            or proposal['request_id'] is not None and not matches(re.compile(r'[a-zA-Z0-9_-]{8,80}'), proposal['request_id'])):
        raise ValueError('invalid proposal identity')
    for key, limit in (('diagnosis', 800), ('expected_effect', 500)):
        if not isinstance(proposal[key], str) or not 1 <= len(proposal[key]) <= limit:
            raise ValueError('invalid proposal explanation')
    evidence = proposal['evidence']
    if (not isinstance(evidence, dict) or set(evidence) != {'snapshot_id', 'targets', 'coverage'}
            or evidence['snapshot_id'] != proposal['snapshot_id'] or not isinstance(evidence['coverage'], dict)
            or not isinstance(evidence['targets'], list) or len(evidence['targets']) > 2
            or any(not isinstance(t, str) or len(t) > 80 for t in evidence['targets'])):
        raise ValueError('invalid evidence references')
    evaluation = proposal['evaluation']
    if (not isinstance(evaluation, dict) or set(evaluation) != {'at', 'min_tasks', 'min_settlements', 'metrics', 'success'}
            or not numeric(proposal['created_at']) or not numeric(evaluation['at'])
            or not proposal['created_at'] + cfg['evaluation_seconds'] <= evaluation['at'] <= proposal['created_at'] + cfg['experiment_seconds']
            or type(evaluation['min_tasks']) is not int or not cfg['min_tasks'] <= evaluation['min_tasks'] <= 200
            or type(evaluation['min_settlements']) is not int or not cfg['min_settlements'] <= evaluation['min_settlements'] <= 100
            or not isinstance(evaluation['success'], str) or not 1 <= len(evaluation['success']) <= 1000):
        raise ValueError('invalid evaluation requirements')
    metrics = evaluation['metrics']
    if (not isinstance(metrics, list) or any(not isinstance(m, str) for m in metrics)
            or len(metrics) != 5 or set(metrics) != {'refund_adjusted_profit_per_visit', 'settled_revenue_per_hour',
                'confirmed_sales', 'response_latency', 'refund_rate'}):
        raise ValueError('invalid evaluation metrics')
    stops = proposal['stop_conditions']
    if not isinstance(stops, list) or not 1 <= len(stops) <= 8 or any(not isinstance(s, str) or not 1 <= len(s) <= 300 for s in stops):
        raise ValueError('invalid stop conditions')
    if len(json.dumps(proposal, ensure_ascii=False, allow_nan=False)) > 32768:
        raise ValueError('oversize proposal')


def eligible_change(c, snapshot):
    for row in snapshot.get('candidates', []):
        if (isinstance(row, dict) and all(row.get(k) == c[k] for k in ('sku', 'grade', 'warranty_hours'))
                and row.get('eligible') is True and type(row.get('available_units')) is int
                and row['available_units'] >= 2):
            return True
    return False


class PolicyStore:
    def __init__(self, root=ROOT):
        self.root = Path(root)

    def _publish(self, current, *, changes, now, expires_at, decision_id, snapshot_id, rules_version, reason):
        new = {'schema': 1, 'version': uuid.uuid4().hex, 'parent_version': current['version'],
               'baseline_hash': baseline_hash(self.root), 'round': 3, 'changes': changes,
               'created_at': now, 'expires_at': expires_at, 'decision_id': decision_id,
               'snapshot_id': snapshot_id, 'rules_version': rules_version, 'reason': reason}
        # History is authoritative recovery material. Failed pointer replacement
        # leaves at most an orphan revision, not a half-applied active policy.
        previous = state_dir(self.root) / 'history' / (current['version'] + '.json')
        if not previous.exists():
            immutable_record(self.root, 'history', current['version'], current)
        immutable_record(self.root, 'history', new['version'], new)
        atomic_write(state_dir(self.root) / 'active.json', new)
        return new

    def apply(self, proposal, snapshot, *, now=None, authorized=False):
        now = time.time() if now is None else now
        with writer_lock(self.root):
            cfg = config_at(self.root)
            if not cfg['enabled'] or cfg['mode'] != 'auto' and not authorized:
                raise ValueError('proposal-only mode: authenticated apply required')
            legacy = read_json(self.root / 'advisor-config.json', 8192)
            if not isinstance(legacy, dict) or legacy.get('enabled') is not False:
                raise ValueError('legacy advisor must be explicitly disabled')
            current = current_state(self.root)
            validate_proposal(proposal, cfg)
            if current['changes'] and current['baseline_hash'] != baseline_hash(self.root):
                raise ValueError('active baseline changed; explicit restore required')
            if proposal['base_version'] != current['version'] or proposal['baseline_hash'] != baseline_hash(self.root):
                raise ValueError('policy version conflict')
            if not all(numeric(proposal.get(k)) for k in ('created_at', 'expires_at')) or not (
                    0 <= now - proposal['created_at'] <= cfg['proposal_ttl_seconds']
                    and now < proposal['expires_at'] <= proposal['created_at'] + cfg['proposal_ttl_seconds']):
                raise ValueError('stale proposal')
            if proposal['action'] != 'experiment' or proposal['family'] != 'opening_price':
                raise ValueError('only bounded opening-price experiments are enabled')
            if current['changes'] and now < current['expires_at']:
                raise ValueError('another experiment is still active')
            if current.get('reason') == 'expired':
                evaluation = read_json(state_dir(self.root) / 'evaluation-control' / (current['decision_id'] + '.json'))
                if evaluation.get('terminal') is not True:
                    raise ValueError('previous experiment is awaiting mature evaluation')
            validate_snapshot(snapshot, now)
            # The original immutable snapshot must exist and have the same rules.
            original = read_json(state_dir(self.root) / 'snapshots' / (proposal['snapshot_id'] + '.json'))
            validate_snapshot(original, now, expected=proposal['snapshot_id'])
            if original['generated_at'] > proposal['created_at']:
                raise ValueError('proposal predates its observation')
            if proposal['rules_version'] != snapshot['rules_version'] or proposal['rules_version'] != original['rules_version']:
                raise ValueError('rules changed')
            validate_changes(proposal['changes'])
            if set(proposal['evidence']['targets']) != {f"{c['sku']}:{c['grade']}:{c['warranty_hours']}" for c in proposal['changes']}:
                raise ValueError('proposal target references disagree')
            if any(not eligible_change(c, original) or not eligible_change(c, snapshot) for c in proposal['changes']):
                raise ValueError('target is no longer eligible')
            # All TTL and evaluation resource limits are code/config controlled.
            return self._publish(current, changes=proposal['changes'], now=now,
                expires_at=now + cfg['experiment_seconds'], decision_id=proposal['decision_id'],
                snapshot_id=proposal['snapshot_id'], rules_version=proposal['rules_version'], reason='experiment')

    def restore(self, expected_version, *, now=None, reason='operator_rollback', authorized=False):
        """Restore baseline overlay, not arbitrary archived settings; CAS prevents old evaluations overwriting new policy."""
        now = time.time() if now is None else now
        if not authorized:
            raise ValueError('authenticated rollback required')
        if reason not in ('operator_rollback', 'technical_error', 'expired', 'evaluated_regression'):
            raise ValueError('invalid restore reason')
        with writer_lock(self.root):
            current = current_state(self.root)
            if current['version'] != expected_version:
                raise ValueError('policy version conflict')
            return self._publish(current, changes=[], now=now, expires_at=now,
                                 decision_id=current['decision_id'], snapshot_id=current.get('snapshot_id'),
                                 rules_version=current.get('rules_version', '3.6'), reason=reason)
