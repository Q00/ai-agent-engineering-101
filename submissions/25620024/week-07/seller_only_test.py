"""Run only the seller half of scripts/market_test_week07.py (unchanged), before market_buyer exists.

    python seller_only.py <repo>/scripts/market_test_week07.py <submission dir> <log file>
"""
import importlib.util
import json
import sys
from pathlib import Path

test_path, sub, out = Path(sys.argv[1]), Path(sys.argv[2]).resolve(), Path(sys.argv[3])
spec = importlib.util.spec_from_file_location("market_test_week07", test_path)
t = importlib.util.module_from_spec(spec)
spec.loader.exec_module(t)

cfg = json.loads((sub / "agent.json").read_text(encoding="utf-8"))
(sub / "logs").mkdir(exist_ok=True)
t.log(f"# seller tests only (market_test_week07.py, kit v{t.KIT_VERSION}); market_buyer not written yet")
t.log(f"harness: {cfg.get('harness')}")
t.seller_tests(sub, cfg)
passed = sum(r["pass"] for r in t.RESULTS)
t.log(f"\n{passed}/{len(t.RESULTS)} seller checks passed")
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text("\n".join(t.LOG) + "\n", encoding="utf-8")
