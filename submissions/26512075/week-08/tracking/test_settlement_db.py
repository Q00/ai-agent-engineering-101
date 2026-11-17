import tempfile, unittest
from pathlib import Path
from settlement_db import connect, ingest, parse

class Tests(unittest.TestCase):
    def test_parse(self):
        self.assertIsNone(parse('[trade] TASK_STATE_COMPLETED'))
        self.assertEqual(parse('[acp] paid 110 through https://test, settled at ledger #55')[:3],('buyer',55,110))
        self.assertEqual(parse('[acp] settled apple at 120, ledger #56, unit u1 grade B')[:3],('seller',56,120))
    def test_replay_and_conflict(self):
        with tempfile.TemporaryDirectory() as d:
            log=Path(d)/'log'; log.write_text('[acp] paid 110 through https://a, settled at ledger #55\n[acp] settled apple at 120, ledger #56, unit u1 grade B\n')
            with connect(Path(d)/'test.db') as db:
                self.assertEqual(ingest(db,log,'r1')['added'],2)
                self.assertEqual(ingest(db,log,'r1')['duplicates'],2)
                log.write_text('[acp] paid 111 through https://a, settled at ledger #55\n')
                self.assertEqual(ingest(db,log,'r1')['conflicts'],1)
                self.assertEqual(db.execute('SELECT price,round FROM observations WHERE ledger=55').fetchone(),(110,None))
                self.assertEqual(ingest(db,log,'r2')['added'],1)
                
if __name__=='__main__': unittest.main()

