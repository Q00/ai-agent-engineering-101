import json
from pathlib import Path
import sys
import tempfile
import unittest

from market_supervisor import Supervisor, _alive


class RuntimeSupervisorTests(unittest.TestCase):
    def test_role_process_is_owned_checkpointed_and_stopped(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'logs').mkdir();(root/'.market').mkdir()
            command=[sys.executable,'-c','import time;time.sleep(30)']
            s=Supervisor(root,{'shop':command})
            p=s.start_role('shop')
            self.assertTrue(_alive(p.pid))
            pids=json.loads((root/'.market/supervisor/pids.json').read_text())
            self.assertEqual(pids['roles']['shop']['pid'],p.pid)
            self.assertEqual(s.control.db.execute(
                "SELECT COUNT(*) FROM recovery_checkpoints WHERE role='shop'").fetchone()[0],1)
            s.stop_role('shop',timeout=2)
            self.assertFalse(_alive(p.pid));s.control.close()

    def test_observer_events_are_structured_and_secret_free(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'logs').mkdir();(root/'.market').mkdir()
            s=Supervisor(root,{'shop':[sys.executable,'-c','pass']})
            s.emit('shop','validated',commit='abc')
            row=json.loads((root/'.market/supervisor/runtime-events.jsonl').read_text())
            self.assertEqual(row['event'],'validated');self.assertEqual(row['role'],'shop')
            self.assertNotIn('token',json.dumps(row).lower());s.control.close()

    def test_atomic_restart_request_replaces_one_role(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'logs').mkdir();(root/'.market').mkdir()
            command=[sys.executable,'-c','import time;time.sleep(30)']
            s=Supervisor(root,{'shop':command});first=s.start_role('shop').pid
            request=s.requests/'cycle.json';request.write_text(json.dumps({'action':'restart','role':'shop'}))
            s.handle_requests();result=json.loads((s.results/'cycle.json').read_text())
            self.assertTrue(result['healthy']);self.assertNotEqual(first,result['pid'])
            self.assertFalse(_alive(first));s.stop_role('shop',timeout=2);s.control.close()


if __name__=='__main__':unittest.main()
