"""core.index: new lines only, kinds and days filtered, repeats dropped, rebuilt when deleted.

    py -m unittest discover -s tests      # from submissions/26512070/week-08
"""
import shutil
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

from core import index, paths  # noqa: E402
from core.events import EventWriter  # noqa: E402
from tests.test_calc_check import Base  # noqa: E402


class IndexTest(Base):
    def emit(self, n, kind="seller.settled"):
        w = EventWriter("t", source="seller")
        for i in range(n):
            w.emit(kind, {"price": i}, task=f"t{i}")
        w.close()

    def test_incremental_filter_rebuild(self):
        self.emit(3)
        self.emit(2, "seller.decision")
        self.assertEqual(len(index.events(["seller.settled"])), 3)
        self.assertEqual(index.update(), 0)                       # nothing new
        self.emit(1)
        self.assertEqual(len(index.events(["seller.settled"])), 4)
        self.assertEqual(index.events(["seller.settled"], since=4e9), [])
        f = next((index.root() / "seller.settled").glob("*.jsonl"))
        f.write_text(f.read_text(encoding="utf-8") * 2, encoding="utf-8")   # a crash filed them twice
        self.assertEqual(len(index.events(["seller.settled"])), 4)
        shutil.rmtree(index.root())
        (paths.bus() / "cursors" / "index.json").unlink()
        self.assertEqual(len(index.events(["seller.settled", "seller.decision"])), 6)


if __name__ == "__main__":
    unittest.main()
