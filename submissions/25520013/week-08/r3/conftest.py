"""v1.17 LLM candidate test root. No bytecode is written (the r3 tree is imported read-only), the r3 suite's
network guard (r3_netguard.py at the r3 root) is installed: any non-loopback DNS / connect fails the test."""
import json
import os
import sys
from pathlib import Path

import pytest

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
R3 = HERE
for p in (str(HERE), str(R3)):
    if p not in sys.path:
        sys.path.insert(0, p)
sys.path.insert(0, str(R3))  # r3_netguard.py lives at the r3 root
# v1.20 syspath fix: llm/editor.py (and editor_v4, labpc drivers) do sys.path.insert(0, "/workspace/course/r3")
# when that path is not yet on sys.path. On the box that dir holds an OLDER experiments/harness/memory package,
# so once tests import llm.editor, later imports of experiments.* resolve there (18 failures + 2 collection errors
# in a full run). Putting it LAST here makes their guard a no-op, so the tree-local packages always win. The
# pinned modules (test_old_modules_are_unchanged_by_this_bundle) stay byte-identical. No-op where the dir is absent.
_BOX_R3 = "/workspace/course/r3"
if _BOX_R3 not in sys.path:
    sys.path.append(_BOX_R3)
import r3_netguard  # noqa: E402

os.environ["R3_NETGUARD"] = "1"
os.environ["R3_NETGUARD_PARENT"] = str(os.getpid())
r3_netguard.install()


@pytest.fixture(autouse=True)
def _no_network():
    n0 = len(r3_netguard.ATTEMPTS)
    yield
    new = r3_netguard.ATTEMPTS[n0:]
    if new:
        pytest.fail(f"network attempt blocked: {new[:3]}", pytrace=False)
