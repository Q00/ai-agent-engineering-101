"""Test root: no bytecode is written, and the network guard (r3_netguard.py) is installed, so any non-loopback DNS
lookup or connect fails the test. The whole suite runs offline."""

import os
import sys
from pathlib import Path

import pytest

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
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
