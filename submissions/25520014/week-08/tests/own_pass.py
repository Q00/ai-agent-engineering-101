"""Checks restock.OWN_PASS (11-16): a goods SKU we sold at a margin passes the A check, unless too few sold, the
margin is thin, or its A held pass our recent A sales of it. No network, no model.

    python tests/own_pass.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import restock as R

own_m = {"G24": (124, 0.071), "FEW": (5, 0.10), "THIN": (40, 0.01), "PILE": (30, 0.05)}
own_a = {"G24": 9, "PILE": 2}
rows = (
    ("G24", 0, own_a, True, "sold 124 at 7.1% of list, no A held"),
    ("FEW", 0, own_a, False, "only 5 sold"),
    ("THIN", 0, own_a, False, "margin 1% of list"),
    ("PILE", 12, own_a, False, "12 A held against 2 A sold in 6 h (min 3)"),
    ("PILE", 3, own_a, True, "3 A held, within B_SEEK_A_MIN"),
    ("G24", 40, None, True, "no A sales ledger: the held-A bound is not applied"),
    ("NONE", 0, own_a, False, "never sold"),
)
good = True
for s, held, oa, want, why in rows:
    got = R.own_pass(s, own_m, held, oa)
    ok = got == want
    good &= ok
    print("ok " if ok else "BAD", f"{s}: {got} (want {want}) - {why}")
print("all ok" if good else "FAILED")
sys.exit(0 if good else 1)
