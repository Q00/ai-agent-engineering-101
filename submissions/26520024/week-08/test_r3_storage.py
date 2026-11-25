"""Offline storage routing, no state migration or real market startup."""
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import socket
import tempfile
import unittest
from unittest.mock import patch

from r3_common import DEFAULT_POLICY, ROOT, read_json
from r3_market_kit import launch_directory
from r3_memory import Memory
from r3_seller import Reservations
from r3_seller_lifecycle import RequestJournal
from r3_checkout import CheckoutGate
from r3_status import inspect_state
from r3_storage import configure_database, database_dir, exclusive_lock, require_local


class StorageTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.md = self.root / 'kit-state'
        self.local = self.root / 'local-db'

    def test_all_three_databases_share_local_route_not_kit_directory(self):
        configure_database(self.md, self.local)
        with Memory(self.md) as memory:
            memory.record('market_settle', '42', {'seq': 42, 'source': 'public_ledger'})
        reservations = Reservations(self.md)
        journal = RequestJournal(self.md)
        gate = CheckoutGate(self.md)
        self.assertEqual(reservations.path, gate.path)
        self.assertEqual(journal.path.parent, self.local)
        self.assertEqual(database_dir(self.md), self.local)
        status = inspect_state(self.md)
        self.assertEqual(status['database_directory'], str(self.local))
        self.assertEqual(status['databases']['r3-memory.sqlite3']['public_ledger_max_seq'], 42)
        self.assertFalse(list(self.md.glob('*.sqlite3')))
        reservations.reconcile({'round': 3, 'units': []}, [], 'offline', 100)
        self.assertTrue((self.md / 'seller-stock-status.json').is_file())
        self.assertEqual(configure_database(self.md, self.local), self.local)

    def test_read_only_status_does_not_initialize_storage(self):
        status = inspect_state(self.md)
        self.assertFalse(self.md.exists())
        self.assertTrue(all(not row['exists'] for row in status['databases'].values()))

    def test_network_or_ephemeral_storage_rejected_before_configuration(self):
        from subprocess import CompletedProcess
        for fs in ('nfs4', 'nfs', 'cifs', 'tmpfs'):
            with patch('r3_storage.subprocess.run', return_value=CompletedProcess([], 0, fs+'\n', '')):
                with self.assertRaises(RuntimeError):
                    configure_database(self.md, self.local)
        self.assertFalse(self.local.exists())
        self.assertFalse(self.md.exists())

    def test_missing_filesystem_probe_fails_closed(self):
        with patch('r3_storage.subprocess.run', side_effect=FileNotFoundError):
            with self.assertRaises(RuntimeError):
                require_local(self.local)

    def test_existing_database_is_never_silently_moved_or_discarded(self):
        with Memory(self.md) as memory:
            memory.record('offline_fixture', 'kept', {'fixture': True})
        with self.assertRaises(RuntimeError):
            configure_database(self.md, self.local)
        with Memory(self.md) as memory:
            self.assertIsNotNone(memory.get('offline_fixture', 'kept'))
        self.assertFalse((self.md / 'db-location.json').exists())

    def test_saved_location_cannot_be_overridden_or_recreated_if_lost(self):
        configure_database(self.md, self.local)
        with self.assertRaises(RuntimeError):
            configure_database(self.md, self.root / 'another')
        moved = self.root / 'moved-db'
        self.local.rename(moved)
        with self.assertRaises(RuntimeError):
            database_dir(self.md)
        self.assertFalse(self.local.exists())

    def test_different_host_or_agent_cannot_adopt_same_database(self):
        configure_database(self.md, self.local)
        with patch('r3_storage.socket.gethostname', return_value='different-host'):
            with self.assertRaises(RuntimeError):
                database_dir(self.md)
        with self.assertRaises(RuntimeError):
            configure_database(self.root / 'other-agent', self.local)
        self.assertEqual(read_json(self.local / 'db-owner.json')['host'], socket.gethostname())

    def test_duplicate_kit_lock_rejected_then_released(self):
        configure_database(self.md, self.local)
        path = self.local / 'market-kit.lock'
        with exclusive_lock(path):
            with self.assertRaises(RuntimeError):
                with exclusive_lock(path):
                    self.fail('A second kit must not enter')
        with exclusive_lock(path):
            pass

    def test_launcher_accepts_only_this_agent_and_local_checkout(self):
        self.assertEqual(launch_directory(['join', '--dir', str(ROOT), '--name', 'offline']), ROOT)
        for args in (['join', '--dir', str(self.root)],
                     ['join', '--dir', str(ROOT), '--deployed=https://offline.example'],
                     ['join', '--dir', str(ROOT), '--deployed', 'https://offline.example'],
                     ['starter', '--dir', str(ROOT)]):
            with self.assertRaises(SystemExit):
                launch_directory(args)

    def test_example_policy_matches_current_defaults(self):
        self.assertEqual(read_json(ROOT / 'r3_policy.example.json'), DEFAULT_POLICY)

    def test_concurrent_different_destinations_cannot_split_one_agent(self):
        def configure(path):
            try:
                return configure_database(self.md, path)
            except RuntimeError:
                return None
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(configure, [self.local, self.root / 'different-db']))
        successful = [result for result in results if result is not None]
        self.assertEqual(len(successful), 1)
        self.assertEqual(database_dir(self.md), successful[0])

    def test_shared_readable_destination_is_not_used_for_checkout_tokens(self):
        self.local.mkdir(mode=0o755)
        self.local.chmod(0o755)
        with self.assertRaises(RuntimeError):
            configure_database(self.md, self.local)
        self.assertFalse((self.md / 'db-location.json').exists())
