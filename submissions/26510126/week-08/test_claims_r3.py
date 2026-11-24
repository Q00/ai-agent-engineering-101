"""python test_claims_r3.py: the claim checker flags a changed number, retires a removed claim, catches loose ranks."""
import json
import tempfile
from pathlib import Path

import claims_r3

board = {"players": [{"player": f"p{i}", "student": True, "student_total": 2 - i / 10, "buyer": {"name": f"p{i}"}}
                     for i in range(12)] + [{"player": "thisjonghoon", "student": True, "student_total": 0.5,
                                             "buyer": {"name": "thisjonghoon"}}],
         "buyers": [{"name": f"p{i}", "forum": 0.95 - i / 100, "saved": 0.3} for i in range(12)] +
                   [{"name": "thisjonghoon", "forum": 0.909, "saved": 0.29}]}
d = Path(tempfile.mkdtemp())
f = d / "orders.md"
f.write_text("We are #13 of 13 students (0.909). Also #2 of 9 somewhere.", encoding="utf-8")
claims = [{"id": "r", "file": str(f), "says": "#13 of 13 students", "metric": "forum_rank_students", "value": 6, "tol": 0},
          {"id": "s", "file": str(f), "says": "#13 of 13 students", "metric": "student_rank", "value": 10, "tol": 1},
          {"id": "g", "file": str(f), "says": "gone text", "metric": "forum_rating", "value": 0.5, "tol": 0}]
res = {c["id"]: (now, st) for c, now, st in claims_r3.check(board, claims)}
ok = [("a true claim is ok", res["r"] == (6, "ok")),
      ("a changed number is STALE", res["s"] == (13, "STALE")),
      ("a claim whose text left the file is retired", res["g"][1] == "retired"),
      ("an unregistered rank is caught", claims_r3.loose(f.read_text(encoding="utf-8"), claims) == ["#2 of 9"]),
      ("the live claims parse", isinstance(claims_r3.load(), list) and all("metric" in c for c in claims_r3.load()))]
ok.append(("reconcile flags the receipts-counted margin of 11/4-11/7 (18M vs Book 5M) and passes 11/10 (8.9M vs 7.1M)",
           claims_r3.gap(18e6, 5e6) and not claims_r3.gap(8.9e6, 7.1e6)))
for name, good in ok:
    print("PASS" if good else "FAIL", name)
print(f"{sum(g for _, g in ok)}/{len(ok)} passed")
raise SystemExit(0 if all(g for _, g in ok) else 1)
