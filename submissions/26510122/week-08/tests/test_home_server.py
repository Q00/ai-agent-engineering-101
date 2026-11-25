import hashlib
import io
import json
from pathlib import Path
import sqlite3
import tarfile
import tempfile
import unittest
from unittest.mock import patch
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from ops.home_server import export_bundle, restore_bundle, render, assert_stopped


class HomeServerTests(unittest.TestCase):
    def fixture(self,root):
        root.mkdir();(root/'state').mkdir();(root/'logs').mkdir()
        key=Ed25519PrivateKey.generate().private_bytes(serialization.Encoding.PEM,
                  serialization.PrivateFormat.PKCS8,serialization.NoEncryption())
        external=root/'state/keys';external.mkdir()
        for k in ('shop_key.pem','buyer_key.pem'):(external/k).write_bytes(key)
        for name in ('seller-runtime','buyer-runtime'):
            runtime=root/'state'/name;runtime.mkdir();(runtime/'state').mkdir();(runtime/'release').mkdir()
            (runtime/'release/code.py').write_text('frozen source')
            (runtime/'release.json').write_text(json.dumps({'sha256':{'code.py':hashlib.sha256(b'frozen source').hexdigest()}}))
            (runtime/'agent.json').write_text('{"old_mac_path":true}')
            for k in ('shop_key.pem','buyer_key.pem'):(runtime/k).symlink_to(external/k)
            (runtime/'state/portfolio.json').write_text('{"unknown_intents":[1]}')
            (runtime/'logs').symlink_to(root/'logs',target_is_directory=True)
            (runtime/'running.json').write_text('{"supervisor_pid":9999999}')
            with sqlite3.connect(runtime/'state/test.sqlite3') as db:
                db.execute('CREATE TABLE memory(value TEXT)');db.execute("INSERT INTO memory VALUES ('kept')")
        (root/'logs/shop-events.jsonl').write_text('{"evidence":"original"}\n')
        owner=root/'state/owner-code';owner.write_text('test credential only')
        return owner

    def test_roundtrip_materializes_keys_preserves_db_state_logs_rebinds_paths(self):
        with tempfile.TemporaryDirectory() as d:
            source=Path(d)/'mac';owner=self.fixture(source)
            bundle=source/'state/migration/private.handoff.tar.gz'
            with patch('ops.home_server.subprocess.check_output',return_value='testcommit\n'):
                export_bundle(source,bundle,owner)
            self.assertEqual(bundle.stat().st_mode & 0o777,0o600)
            dest=Path(d)/'linux';dest.mkdir();target_owner=dest/'credentials/owner-code'
            restore_bundle(dest,bundle,target_owner)
            self.assertEqual(target_owner.read_bytes(),owner.read_bytes())
            self.assertEqual(target_owner.stat().st_mode & 0o777,0o600)
            self.assertEqual((dest/'state/seller-runtime/logs').resolve(),(dest/'logs').resolve())
            for name in ('seller-runtime','buyer-runtime'):
                rt=dest/'state'/name
                self.assertFalse((rt/'shop_key.pem').is_symlink())
                self.assertFalse((rt/'running.json').exists())
                with sqlite3.connect(rt/'state/test.sqlite3') as db:
                    self.assertEqual(db.execute('SELECT value FROM memory').fetchone()[0],'kept')
                self.assertEqual((rt/'logs/shop-events.jsonl').read_text(),'{"evidence":"original"}\n')
                self.assertEqual(json.loads((rt/'state/portfolio.json').read_text())['unknown_intents'],[1])
                self.assertNotIn(str(source),(rt/'agent.json').read_text())
            render(dest,dest/'state/systemd','/home/user/bin/codex')
            units=list((dest/'state/systemd').glob('*.service'));self.assertEqual(len(units),3)
            for service in units:
                self.assertIn(f'WorkingDirectory={dest}\n',service.read_text())
            buyer=(dest/'state/buyer-runtime/agent.json').read_text();self.assertNotIn('--life',buyer)
            with self.assertRaises(FileExistsError):restore_bundle(dest,bundle,target_owner)

    def test_active_source_refused(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)/'source';owner=self.fixture(root)
            with patch('ops.home_server.alive',return_value=True):
                with self.assertRaisesRegex(ValueError,'Stop source services'):
                    export_bundle(root,root/'state/private.handoff.tar.gz',owner)

    def test_procurement_worker_blocks_export_even_without_live_supervisor(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)/'source';owner=self.fixture(root)
            status=root/'state/seller-runtime/running.json'
            status.write_text(json.dumps({'supervisor_pid':9999999,'procurement_pid':12345}))
            with patch('ops.home_server.alive',side_effect=lambda pid:pid==12345):
                with self.assertRaisesRegex(ValueError,'procurement_pid'):
                    export_bundle(root,root/'state/private.handoff.tar.gz',owner)
            self.assertFalse((root/'state/private.handoff.tar.gz').exists())

    def test_scheduler_heartbeat_blocks_export_with_stale_running_record(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)/'source';owner=self.fixture(root)
            (root/'state/seller-runtime/state/procurement-scheduler-status.json').write_text(
                json.dumps({'pid':12345,'status':'planning'}))
            with patch('ops.home_server.alive',side_effect=lambda pid:pid==12345):
                with self.assertRaisesRegex(ValueError,'Stop source services'):
                    export_bundle(root,root/'state/private.handoff.tar.gz',owner)

    def test_procurement_console_evidence_survives_migration(self):
        with tempfile.TemporaryDirectory() as d:
            source=Path(d)/'source';owner=self.fixture(source)
            evidence='synthetic procurement timeout evidence\n'
            (source/'logs/procurement-console.log').write_text(evidence)
            bundle=source/'state/migration/private.handoff.tar.gz'
            with patch('ops.home_server.subprocess.check_output',return_value='testcommit'):
                export_bundle(source,bundle,owner)
            dest=Path(d)/'target';dest.mkdir()
            restore_bundle(dest,bundle,dest/'owner-code')
            self.assertEqual((dest/'logs/procurement-console.log').read_text(),evidence)

    def test_unsafe_archive_rejected_before_install(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);bundle=root/'evil.tar.gz'
            with tarfile.open(bundle,'w:gz') as archive:
                info=tarfile.TarInfo('../escape');info.size=1;archive.addfile(info,io.BytesIO(b'x'))
            with self.assertRaisesRegex(ValueError,'Unsafe'):
                restore_bundle(root,bundle,root/'owner-code')
            self.assertFalse((root/'state/seller-runtime').exists())

    def test_sqlite_wal_is_backed_up_and_bad_release_never_installed(self):
        with tempfile.TemporaryDirectory() as d:
            source=Path(d)/'mac';owner=self.fixture(source)
            db=sqlite3.connect(source/'state/buyer-runtime/state/test.sqlite3')
            try:
                db.execute('PRAGMA journal_mode=WAL')
                db.execute("INSERT INTO memory VALUES ('wal committed')");db.commit()
                bundle=source/'state/migration/wal.handoff.tar.gz'
                with patch('ops.home_server.subprocess.check_output',return_value='testcommit'):
                    export_bundle(source,bundle,owner)
                dest=Path(d)/'linux';dest.mkdir()
                restore_bundle(dest,bundle,dest/'owner-code')
                with sqlite3.connect(dest/'state/buyer-runtime/state/test.sqlite3') as restored:
                    self.assertEqual(restored.execute('SELECT COUNT(*) FROM memory').fetchone()[0],2)
                (source/'state/buyer-runtime/release/code.py').write_text('corrupted')
                bad=source/'state/migration/bad.handoff.tar.gz'
                with patch('ops.home_server.subprocess.check_output',return_value='testcommit'):
                    export_bundle(source,bad,owner)
                fresh=Path(d)/'fresh';fresh.mkdir()
                with self.assertRaisesRegex(ValueError,'integrity mismatch'):
                    restore_bundle(fresh,bad,fresh/'owner-code')
                self.assertFalse((fresh/'state/seller-runtime').exists())
                self.assertFalse((fresh/'owner-code').exists())
            finally:db.close()
