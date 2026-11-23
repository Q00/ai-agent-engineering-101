"""11-12 fixes after the second independent verification: sibling experiments in separate wakes end with the category
inheriting again; the memory feeder starts a new source at the cut."""
import json
import os

from kernel import experiments as ex
from tests.test_revert_key import *  # noqa: F401,F403 - fixtures and helpers


def test_inherited_origin_reads_running_rows_only():
    rows = [
        {"status": "running", "section": "S", "raw": {"base": "fresh_discount", "present": False, "inherited": {"B": 0.1}}},
        {"status": "kept", "section": "T", "raw": {"base": "fresh_discount", "present": False, "inherited": {"B": 0.2}}},
        {"status": "running", "items": [{"section": "G", "raw": {"base": "accept_c", "present": False, "inherited": {"x": 1}}}]},
        "junk",
    ]
    assert ex._inherited_origin(rows, "S", "fresh_discount") == {"B": 0.1}
    assert ex._inherited_origin(rows, "T", "fresh_discount") is None
    assert ex._inherited_origin(rows, "G", "accept_c") == {"x": 1}
    assert ex._inherited_origin(rows, "S", "accept_c") is None
