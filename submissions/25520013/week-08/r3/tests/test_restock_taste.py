"""v1.19 restock_plan(taste=...): an adopted taste forecast reorders categories only; the hook reads the gate file
only (no taste/memory import) and ignores a stale, shadow or switched-off gate."""
import ast
import copy
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

R3 = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(R3))
import policy as P  # noqa: E402

NOW = 1793708000.0
CATS = [f"테스트>분류{i:02d}" for i in range(14)]


class Stats:
    """aggregates stand-in: category velocity rises with the index, no SKU rows."""

    def sku(self, k):
        return None

    def category(self, k):
        return {"velocity_per_h": 0.01 * (CATS.index(k) + 1)}


def _plan(taste=None, lines=None, stats=None, **kw):
    catalog, demand = {}, []
    for i, cat in enumerate(CATS):
        catalog[f"S{i:02d}"] = {"sku": f"S{i:02d}", "name": f"상품{i}", "category": cat, "kind": "goods",
                                "list_price": 10000}
        demand.append({"category": cat, "kind": "goods", "median_price": 10000})
    inv = {"balance": 4_500_000, "units": [], "storage_per_day": 0.01}
    pol = P.load()
    if lines:
        pol["shop"]["max_lines_per_run"] = lines
    if taste is not None:
        kw["taste"] = taste
    return P.restock_plan(inv, demand, catalog, pol, stats, activity={}, now=NOW, **kw)


def _cats(buy):
    return [CATS[int(b["sku"][1:])] for b in buy]


def test_taste_none_gives_the_same_plan():
    for stats in (None, Stats()):
        assert _plan(stats=stats) == _plan(None, stats=stats) == _plan({}, stats=stats)


def test_adopted_taste_reorders_categories_only():
    taste = {c: (i + 1) / 105 for i, c in enumerate(reversed(CATS))}     # first category most likely
    base, why0 = _plan(lines=20, stats=Stats())
    got, why1 = _plan(taste, lines=20, stats=Stats())
    assert _cats(base) == CATS[::-1] and _cats(got) == CATS                  # velocity order vs taste order
    assert sorted(map(json.dumps, base)) == sorted(map(json.dumps, got))     # same lines, same quantities
    assert {k: why0[k] for k in ("budget", "worst_spend", "inventory_cost")} == \
           {k: why1[k] for k in ("budget", "worst_spend", "inventory_cost")}


def test_caps_are_unchanged_and_line_limit_takes_the_top_of_the_taste_order():
    taste = {c: (i + 1) / 105 for i, c in enumerate(reversed(CATS))}
    base, _ = _plan(stats=Stats())
    got, _ = _plan(taste, stats=Stats())
    assert len(base) == len(got) == 10 and _cats(got) == CATS[:10]
    q0, q1 = {b["sku"]: b["qty"] for b in _plan(lines=20, stats=Stats())[0]}, {b["sku"]: b["qty"] for b in got}
    assert all(q1[s] == q0[s] for s in q1)                                  # goods storage cap per category kept
    assert {q0[s] for s in q0} == {1, 2, 3}                                  # the cap binds at different depths


def test_taste_covers_some_categories_the_rest_keep_velocity_order():
    got, _ = _plan({CATS[0]: 0.3, CATS[5]: 0.6}, lines=20, stats=Stats())
    assert _cats(got)[:2] == [CATS[5], CATS[0]]
    assert _cats(got)[2:] == [c for c in CATS[::-1] if c not in (CATS[0], CATS[5])]


def _restock_hook():
    sys.path.insert(0, str(R3 / "hooks"))
    spec = importlib.util.spec_from_file_location("restock_hook_v119", R3 / "hooks" / "restock.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_hook_reads_only_a_fresh_adopted_enabled_gate(tmp_path):
    R = _restock_hook()
    pol = P.load()
    f = {CATS[0]: 0.9, CATS[1]: 0.1}

    def gate(**kw):
        g = dict({"state": "adopted", "asof": NOW - 600, "forecast": f}, **kw)
        (tmp_path / "taste_gate.json").write_text(json.dumps(g, ensure_ascii=False), encoding="utf-8")

    assert R.taste_ranking(pol, tmp_path, NOW) is None                          # no gate file
    gate()
    assert R.taste_ranking(pol, tmp_path, NOW) == f
    gate(state="shadow")
    assert R.taste_ranking(pol, tmp_path, NOW) is None
    gate(state="rolled_back")
    assert R.taste_ranking(pol, tmp_path, NOW) is None
    gate(asof=NOW - pol["taste"]["max_age_s"] - 1)                              # stale
    assert R.taste_ranking(pol, tmp_path, NOW) is None
    gate(asof=NOW + 600)                                                        # from the future
    assert R.taste_ranking(pol, tmp_path, NOW) is None
    gate(asof=None)
    assert R.taste_ranking(pol, tmp_path, NOW) is None
    gate()
    off = copy.deepcopy(pol)
    off["taste"]["enabled"] = False
    assert R.taste_ranking(off, tmp_path, NOW) is None
    (tmp_path / "taste_gate.json").write_text("{not json", encoding="utf-8")
    assert R.taste_ranking(pol, tmp_path, NOW) is None


def test_policy_taste_block_and_format():
    raw = (R3 / "policy.json").read_text(encoding="utf-8")
    d = json.loads(raw)
    assert d["version"] == 10 and d["taste"]["enabled"] is True and d["taste"]["max_age_s"] == 7200
    assert json.dumps(d, indent=1, ensure_ascii=False) + "\n" == raw
    assert P.load()["_hash"]


DECISION_PATH = ["hooks/seller.py", "hooks/market_buyer.py", "hooks/restock.py", "hooks/review.py",
                 "hooks/common.py", "policy.py", "aggregates.py", "cards.py", "ledger.py", "reputation.py",
                 "volume.py", "metrics.py", "owner.py"]
FORBIDDEN = {"memory", "harness", "experiments", "evals", "taste", "memo_proposer", "llm"}


def test_decision_path_imports_no_shadow_module():
    for rel in DECISION_PATH:
        tree = ast.parse((R3 / rel).read_text(encoding="utf-8"))
        mods = set()
        for n in ast.walk(tree):
            if isinstance(n, ast.Import):
                mods |= {a.name.split(".")[0] for a in n.names}
            elif isinstance(n, ast.ImportFrom) and n.module and not n.level:
                mods.add(n.module.split(".")[0])
        assert not mods & FORBIDDEN, (rel, mods & FORBIDDEN)


def test_restock_hook_loads_without_taste_or_memory():
    code = ("import sys; sys.path[:0] = [%r, %r]; import restock; "
            "print(sorted(m for m in sys.modules if m.split('.')[0] in %r))") % (str(R3 / "hooks"), str(R3),
                                                                                  sorted(FORBIDDEN))
    r = subprocess.run([sys.executable, "-c", code], cwd=R3, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "[]"
