"""The export must retain committed WAL data without changing the source."""
import hashlib
from pathlib import Path
import sqlite3
import tempfile
import unittest

from export_k_memory import export_memory


class MemoryExportTests(unittest.TestCase):
    def test_committed_wal_is_copied_and_original_is_unchanged(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / 'source.sqlite3'
            with sqlite3.connect(source) as writer:
                writer.execute('PRAGMA journal_mode=WAL')
                writer.execute('CREATE TABLE events(kind TEXT,ts REAL,data TEXT)')
                writer.execute("INSERT INTO events VALUES('seller_failure',100,'{}')")
                writer.commit()
                wal = Path(str(source) + '-wal')
                self.assertTrue(wal.is_file())
                before = [hashlib.sha256(p.read_bytes()).hexdigest() for p in (source, wal)]
                result = export_memory(source, root / 'private-exports')
                self.assertEqual(result['event_counts'], {'seller_failure': 1})
                self.assertEqual(before, [hashlib.sha256(p.read_bytes()).hexdigest() for p in (source, wal)])
                backup = Path(result['backup'])
                self.assertEqual(backup.stat().st_mode & 0o777, 0o600)
                with sqlite3.connect(backup) as reader:
                    self.assertEqual(reader.execute('PRAGMA journal_mode').fetchone()[0], 'delete')
                    self.assertEqual(reader.execute('SELECT COUNT(*) FROM events').fetchone()[0], 1)
