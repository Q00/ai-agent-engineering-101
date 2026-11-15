"""Offline atomic/CAS/authentication failure tests with isolated fixture state."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

import shop_ops_commands as commands
import shop_ops_policy as policy
from shop_operations import make_proposal

ROOT = Path(__file__).resolve().parent
NOW = 100000


class OperationsStoreTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir=ROOT / '.market')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        for name in ('shop-policy.json', 'advisor-config.json', 'shop-ops-config.json'):
            shutil.copyfile(ROOT / name, self.root / name)
        self.cfg = policy.config_at(self.root)
        self.state = policy.current_state(self.root)
        self.obs = {'generated_at': NOW, 'round': 3, 'rules_version': '3.6', 'application_ready': True,
                    'sources': {}, 'candidates': [{'sku': 'G12345678', 'grade': 'A', 'warranty_hours': 6,
                                                  'eligible': True, 'available_units': 2}]}
        self.obs['snapshot_id'] = policy.digest(self.obs)
        policy.immutable_record(self.root, 'snapshots', self.obs['snapshot_id'], self.obs)
        self.proposal = make_proposal(self.root, self.obs, self.state,
            {'action': 'experiment', 'targets': ['G12345678:A:6'],
             'diagnosis': 'Synthetic confirmed price rejections', 'expected_effect': 'Observe conversion'}, self.cfg)
        self.store = policy.PolicyStore(self.root)

    def apply(self, proposal=None, obs=None, **kwargs):
        return self.store.apply(proposal or self.proposal, obs or self.obs,
                                now=kwargs.pop('now', NOW), authorized=True, **kwargs)

    def test_atomic_replace_failure_keeps_old_active_then_retry_succeeds(self):
        with patch('shop_ops_policy.os.replace', side_effect=OSError('disk unavailable')):
            with self.assertRaises(OSError):
                self.apply()
        self.assertEqual(policy.current_state(self.root)['version'], self.state['version'])
        result = self.apply()
        self.assertEqual(policy.current_state(self.root), result)
        history = policy.state_dir(self.root) / 'history'
        self.assertTrue((history / (self.state['version'] + '.json')).exists())

    def test_concurrent_proposals_from_same_base_apply_only_once(self):
        def apply_once(_):
            try:
                return self.apply()['version']
            except ValueError:
                return None
        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(apply_once, range(2)))
        self.assertEqual(sum(v is not None for v in outcomes), 1)

    def test_stale_proposal_observation_and_changed_baseline_are_rejected(self):
        with self.assertRaises(ValueError):
            self.apply(now=NOW + self.cfg['proposal_ttl_seconds'] + 1)
        stale = {**self.obs, 'generated_at': NOW-901}
        stale['snapshot_id'] = policy.digest({k:v for k,v in stale.items() if k != 'snapshot_id'})
        with self.assertRaises(ValueError):
            self.apply(obs=stale)
        baseline = json.loads((self.root / 'shop-policy.json').read_text())
        baseline['grade_margin_percent']['A'] += 1
        policy.atomic_write(self.root / 'shop-policy.json', baseline)
        with self.assertRaises(ValueError):
            self.apply()
        self.assertFalse((policy.state_dir(self.root) / 'active.json').exists())

    def test_snapshot_content_and_original_time_are_bound_to_proposal(self):
        changed = deepcopy(self.obs)
        changed['candidates'][0]['available_units'] = 100
        with self.assertRaises(ValueError):
            self.apply(obs=changed)
        with self.assertRaises(ValueError):
            self.apply({**self.proposal, 'created_at': NOW-1})

    def test_policy_validation_prevents_traversal_warranty_expansion_and_weak_evaluation(self):
        for updates in ({'snapshot_id': '../../outside'}, {'decision_id': '../outside'},
                        {'request_id': 'x/../../outside'}, {'schema': True},
                        {'evaluation': {**self.proposal['evaluation'], 'min_tasks': 1}},
                        {'changes': [{**self.proposal['changes'][0], 'after': -3}]},
                        {'changes': [{**self.proposal['changes'][0], 'grade': 'C'}]},
                        {'changes': [{**self.proposal['changes'][0], 'warranty_hours': 72}]}):
            with self.subTest(updates=updates), self.assertRaises(ValueError):
                self.apply({**self.proposal, **updates})

    def test_restore_is_version_checked_and_atomic(self):
        active = self.apply()
        with patch('shop_ops_policy.os.replace', side_effect=OSError('disk unavailable')):
            with self.assertRaises(OSError):
                self.store.restore(active['version'], now=NOW+1, authorized=True)
        self.assertEqual(policy.current_state(self.root)['version'], active['version'])
        restored = self.store.restore(active['version'], now=NOW+1, authorized=True)
        self.assertEqual(restored['changes'], [])
        with self.assertRaises(ValueError):
            self.store.restore(active['version'], now=NOW+2, authorized=True)

    def test_propose_mode_and_legacy_advisor_block_automatic_apply(self):
        with self.assertRaises(ValueError):
            self.store.apply(self.proposal, self.obs, now=NOW)
        legacy = json.loads((self.root / 'advisor-config.json').read_text())
        policy.atomic_write(self.root / 'advisor-config.json', {**legacy, 'enabled': True})
        with self.assertRaises(ValueError):
            self.apply()

    def test_symlinked_market_and_collection_are_rejected(self):
        outside = self.root / 'outside'
        outside.mkdir()
        events = policy.state_dir(self.root) / 'errors'
        events.symlink_to(outside, target_is_directory=True)
        with self.assertRaises(ValueError):
            policy.immutable_record(self.root, 'errors', 'event123', {'x': 1})
        with self.assertRaises(ValueError):
            policy.immutable_record(self.root, '../outside', 'event123', {'x': 1})
        self.assertFalse(list(outside.iterdir()))
        second = self.root / 'second'
        second.mkdir()
        (second / '.market').symlink_to(outside, target_is_directory=True)
        with self.assertRaises(ValueError):
            with policy.writer_lock(second):
                pass


class OperationsCommandTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir=ROOT / '.market')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.key = self.root / 'operator-key'
        commands.init_key(self.key)

    def envelope(self, request_id='request_123'):
        return commands.signed_request('status', key_path=self.key, request_id=request_id, now=NOW)

    def test_authentication_permissions_age_and_replay(self):
        envelope = self.envelope()
        with self.assertRaises(ValueError):
            commands.accept(self.root, {**envelope, 'command': 'rm -rf'}, key_path=self.key, now=NOW)
        with self.assertRaises(ValueError):
            commands.accept(self.root, envelope, key_path=self.key, now=NOW+301)
        self.key.chmod(0o644)
        with self.assertRaises(ValueError):
            commands.accept(self.root, envelope, key_path=self.key, now=NOW)
        self.key.chmod(0o600)
        accepted = commands.accept(self.root, envelope, key_path=self.key, now=NOW)
        self.assertEqual(accepted['nonce'], envelope['nonce'])
        with self.assertRaises(ValueError):
            commands.accept(self.root, envelope, key_path=self.key, now=NOW)

    def test_durable_request_preserves_nonce_after_index_write_failure(self):
        envelope = self.envelope()
        real = policy.immutable_record
        def write(root, collection, identifier, value):
            if collection == 'nonces':
                raise OSError('interrupted nonce index')
            return real(root, collection, identifier, value)
        with patch('shop_ops_commands.immutable_record', side_effect=write), self.assertRaises(OSError):
            commands.accept(self.root, envelope, key_path=self.key, now=NOW)
        other = {k:v for k,v in envelope.items() if k != 'signature'}
        other['request_id'] = 'request_456'
        other['signature'] = commands.signature(other, commands.key_bytes(self.key))
        with self.assertRaises(ValueError):
            commands.accept(self.root, other, key_path=self.key, now=NOW)

    def test_state_and_decision_cannot_regress_and_final_result_is_immutable(self):
        commands.accept(self.root, self.envelope(), key_path=self.key, now=NOW)
        rid, decision = 'request_123', 'a'*32
        commands.transition(self.root, rid, 'analysing', now=NOW)
        commands.transition(self.root, rid, 'proposed', now=NOW, decision_id=decision)
        with self.assertRaises(ValueError):
            commands.transition(self.root, rid, 'applied', now=NOW, decision_id='b'*32)
        commands.transition(self.root, rid, 'verifying', now=NOW)
        with self.assertRaises(ValueError):
            commands.transition(self.root, rid, 'analysing', now=NOW)
        final = commands.transition(self.root, rid, 'completed', now=NOW, result='done')
        stale = commands.transition(self.root, rid, 'verifying', now=NOW+1, result='stale')
        self.assertEqual(final, stale)


if __name__ == '__main__':
    unittest.main()
