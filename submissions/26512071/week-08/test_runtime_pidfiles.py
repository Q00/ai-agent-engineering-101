"""Compatibility PID mirrors must follow every kit generation without exposing environment."""
import tempfile,unittest
from pathlib import Path
from unittest.mock import patch,Mock
import market_runtime as rt
class PidMirrorTests(unittest.TestCase):
    def test_numeric_pid_files_replace_stale_values(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);join=root/'agent-market';logs=root/'logs'
            rt.write_pid_files(join,logs,111,222)
            self.assertEqual((join/'kit.pid').read_text().strip(),'111')
            self.assertEqual((logs/'trading-supervisor.pid').read_text().strip(),'222')
            rt.write_pid_files(join,logs,333,222)
            self.assertEqual((join/'kit.pid').read_text().strip(),'333')
            self.assertEqual(sorted(str(p.relative_to(root)) for p in root.rglob('*') if p.is_file()),['agent-market\\kit.pid','logs\\trading-supervisor.pid'])
    def test_each_successful_kit_spawn_updates_mirror(self):
        with tempfile.TemporaryDirectory() as t:
            args=rt.parse_args(['--public-url','https://example.trycloudflare.com','--join-dir',t])
            events=Mock();sup=rt.Supervisor(args,events)
            proc=Mock(pid=456)
            with patch.object(rt,'JobObject'),patch.object(rt,'spawn',return_value=proc),patch.object(rt,'write_pid_files') as write:
                sup._start_kit()
            write.assert_called_once_with(args.join_dir,rt.HERE/'logs',456,rt.os.getpid())
if __name__=='__main__':unittest.main()
