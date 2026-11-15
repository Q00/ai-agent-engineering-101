"""Checks seller.NOT_GRADE (11-15): a grade named in a negation ("no grade C") is not a grade the buyer named, so a
buyer asking for A or B gets B or A, not the C we hold; other wordings as before.

    python tests/not_grade.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import seller

U = {"kind": "goods"}
ABC = {g: {"unit": U, "ask": 1} for g in "ABC"}
AC = {g: {"unit": U, "ask": 1} for g in "AC"}
NOC = ("I want to buy 시몬스 D2178A (Gbec2b68f). Grade A or B with a warranty of at least 6 hours on the receipt, "
       "please (no grade C). Please quote your cheapest such unit and its price.")
rows = (
    ("'no grade C', A B C held: B", NOC, ABC, "B"),
    ("'no grade C', A C held: A", NOC, AC, "A"),
    ("'grade C is not acceptable', A C held: A", "I want to buy X (G00000001). Grade C is not acceptable.", AC, "A"),
    ("'grade C is fine' still C", "I want to buy X (G00000001). Grade C is fine.", ABC, "C"),
    ("'Only grade A or B is acceptable': best held A", "I want to buy X (G00000001). Only grade A or B is acceptable.", ABC, "B"),
    ("no grade named: best held", "I want to buy X (G00000001).", ABC, "A"),
)
good = True
for name, text, opts, want in rows:
    got = seller.offer_grade(text, opts)
    print("ok " if got == want else "BAD", f"{name}: {got} (want {want})")
    good &= got == want
seller.NOT_GRADE = False
old = seller.offer_grade(NOC, ABC)
print("ok " if old == "C" else "BAD", f"NOT_GRADE off: the old reading gives {old} (want C)")
print("ALL OK" if good and old == "C" else "FAILED")
ok2 = seller.wants_lower(NOC, "C") is False
seller.NOT_GRADE = True
ok2 = seller.wants_lower(NOC, "C") is False and seller.wants_lower(NOC, "B") is True
print("ok " if ok2 else "BAD", "wants_lower: C not wanted after 'no grade C', B wanted")
print("ALL OK" if ok2 else "FAILED")
