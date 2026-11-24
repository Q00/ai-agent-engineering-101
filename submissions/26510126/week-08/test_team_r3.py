"""Offline check of team_r3.py: draws by rank, the seller's style over a strategy, promotion and replacement.

    python test_team_r3.py      # prints PASS/FAIL per case; save with | tee logs/team-r3-test-<time>.txt
"""
import collections
import json
import os
import tempfile
from pathlib import Path

os.environ["TEAM_ROSTER"] = str(Path(tempfile.mkdtemp(prefix="team-")) / "team_r3.json")
import team_r3                                      # noqa: E402

ok = []


def check(name, cond, detail):
    ok.append(bool(cond))
    print(f"{'PASS' if cond else 'FAIL'}  {name}: {detail}", flush=True)


draws = collections.Counter(team_r3.choose("buyer")[0] for _ in range(3000))
check("talks go to members by rank: the lead most, the junior least", draws["b-balanced"] > draws["b-patient"]
      > draws["b-closer"] > 200, dict(draws))
s = {"ask_frac": 0.8, "ask_frac_food": 0.9, "floor_frac": 0.72, "floor_frac_food": 0.7, "step": 0.2}
up = team_r3.apply_seller(s, {"ask_delta": 0.06, "floor_delta": 0.1, "step": 0.5})
check("a seller style moves ask and floor but keeps the floor under the ask and the ask at most list",
      up["ask_frac"] == 0.86 and up["floor_frac"] == 0.82 and up["ask_frac_food"] == 0.96 and up["step"] == 0.5
      and team_r3.apply_seller(dict(s, ask_frac=0.98), {"ask_delta": 0.1})["ask_frac"] == 1.0, up)
recs = {"b-balanced": [(1e12, 1000)] * 30, "b-patient": [(1e12, 3000)] * 30, "b-closer": [(1e12, 200)] * 45}
ch = team_r3.review("buyer", recs, now=1e12 + 1)
r = {m["id"]: m["rank"] for m in team_r3.load()["buyer"]}
check("the best earner is promoted to lead and a junior far behind after its trial is replaced by a recruit",
      r.get("b-patient") == "lead" and r.get("b-balanced") == "senior" and "b-closer" not in r
      and any("replaced" in c for c in ch) and len(team_r3.load()["buyer"]) == len(team_r3.DEFAULT["buyer"]), ch)
recs2 = {m["id"]: [(1e12 + 2, 500)] * 5 for m in team_r3.load()["seller"]}
ch2 = team_r3.review("seller", recs2, recruit={"ask_delta": 0.5}, now=1e12 + 3)
check("members with too few talks are not ranked", ch2 == [], ch2)
check("a proposed recruit is clamped to the bounds", team_r3.clamp("seller", {"ask_delta": 0.5, "bogus": 1}) == {"ask_delta": 0.1},
      team_r3.clamp("seller", {"ask_delta": 0.5, "bogus": 1}))
print(f"\n{sum(ok)}/{len(ok)} passed")
