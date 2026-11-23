"""Model trade switch off: the operator improves the rule hooks through policy.json, one bounded key at a time."""

import json
import os
import shutil
import time

import pytest
from kernel import operator as op
from kernel import policy_edit as pe
from kernel import tools
from kernel.trajectory import Trajectory

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOGIN = "secret-login"
BED = "가구/침구>가구>침대/매트리스"
SOFA = "가구/침구>가구>소파/의자/스툴"

POLICY = {
    "owner": {"github": LOGIN},
    "defaults": {"food": {"price_mult": 0.92, "max_rounds": 4}, "goods": {"price_mult": 0.9, "max_rounds": 4}},
    "categories": {BED: {"stock_target": 36, "price_mult": 0.66}},
    "bounds": {"price_mult": [0.5, 1.5], "max_rounds": [1, 10]},
}

# a stand-in for the rule code's policy.py: load(path) raises on a value outside bounds
FAKE_A1 = '''
import json
class PolicyError(ValueError):
    pass
def load(path=None):
    pol = json.load(open(path))
    b = pol.get("bounds") or {}
    for blk in list(pol["defaults"].values()) + list(pol["categories"].values()):
        for k, v in blk.items():
            if k in b and not b[k][0] <= v <= b[k][1]:
                raise PolicyError(k)
        if blk.get("stock_target") == 30:
            raise PolicyError("30 is refused by the rule code")
    return pol
'''


class FakeTerra:
    def __init__(self, answers):
        self.answers, self.prompts = list(answers), []

    def complete(self, prompt, *, agent, timeout_s, effort="medium"):
        self.prompts.append(prompt)
        a = self.answers.pop(0)
        return a if isinstance(a, str) else json.dumps(a, ensure_ascii=False)


@pytest.fixture
def root(tmp_path):
    r = tmp_path / "data"
    for d in (".market", ".r3", "a2/board"):
        (r / d).mkdir(parents=True)
    (r / "policy.json").write_text(json.dumps(POLICY, ensure_ascii=False))
    (r / "policy.py").write_text(FAKE_A1)
    (r / ".market/catalog.json").write_text(json.dumps({"S1": {"category": SOFA, "list_price": 1000, "kind": "goods"}}, ensure_ascii=False))
    (r / ".market/inventory.json").write_text(json.dumps({"balance": 1e7, "bankrupt_line": 5e5, "units": []}))
    return str(r)


@pytest.fixture
def hroot(tmp_path):
    shutil.copytree(os.path.join(HERE, "harness"), tmp_path / "proj" / "harness")
    return str(tmp_path / "proj" / "harness")


def _traj(root):
    return Trajectory(os.path.join(root, "a2/trajectory.jsonl"), secrets=(LOGIN,))


def _pol(root):
    return json.load(open(os.path.join(root, "policy.json"), encoding="utf-8"))


def test_one_key_changes_with_backup_and_trajectory_row(root):
    traj, now = _traj(root), time.time()
    res = pe.apply(root, [{"section": BED, "key": "price_mult", "value": 0.7, "reason": "침대 정산 3/120"}], traj, now)
    assert res == [{"ok": True, "section": BED, "key": "price_mult", "old": 0.66, "new": 0.7}]
    pol = _pol(root)
    assert pol["categories"][BED] == {"stock_target": 36, "price_mult": 0.7}
    assert pol["owner"] == POLICY["owner"] and pol["defaults"] == POLICY["defaults"]  # nothing else moved
    baks = os.listdir(os.path.join(root, pe.BAK_DIR))
    assert len(baks) == 1 and json.load(open(os.path.join(root, pe.BAK_DIR, baks[0])))["categories"][BED]["price_mult"] == 0.66
    row = [r for r in traj.since(0) if r["kind"] == "policy_edit"][0]
    assert (row["section"], row["key"], row["old"], row["new"]) == (BED, "price_mult", 0.66, 0.7)


def test_refusals_leave_the_file_untouched(root):
    traj, now = _traj(root), time.time()
    before = open(os.path.join(root, "policy.json"), "rb").read()
    bad = [
        {"section": BED, "key": "owner", "value": 1, "reason": "r"},  # key not editable
        {"section": BED, "key": "price_mult", "value": "0.7", "reason": "r"},  # not a number
        {"section": "없는>상품군", "key": "price_mult", "value": 0.9, "reason": "r"},  # unknown section
        {"section": BED, "key": "max_rounds", "value": 2.5, "reason": "r"},  # whole numbers only
        {"section": BED, "key": "stock_target", "value": 30, "reason": "r"},  # the rule code's own check refuses
        {"section": "defaults.goods", "key": "price_mult", "value": 1.3, "reason": "r"},  # above 1.2
    ]
    res = pe.apply(root, bad, traj, now)
    assert [r["ok"] for r in res] == [False] * len(bad)
    assert "check failed" in res[4]["error"]
    assert open(os.path.join(root, "policy.json"), "rb").read() == before
    assert not [f for f in os.listdir(root) if f.startswith(".policy.tmp-")]


def test_new_category_from_catalog_starts_from_defaults_and_no_cooldown(root):
    traj, now = _traj(root), time.time()
    res = pe.apply(root, [{"section": SOFA, "key": "price_mult", "value": 0.8, "reason": "소파 몫 4%"}], traj, now,
                   categories={SOFA})
    assert res[0]["ok"] and res[0]["old"] == 0.9 and _pol(root)["categories"][SOFA] == {"price_mult": 0.8}
    again = pe.apply(root, [{"section": SOFA, "key": "price_mult", "value": 0.75, "reason": "r"}], traj, now + 60,
                     categories={SOFA})
    assert again[0]["ok"] and again[0]["old"] == 0.8  # 11-11: no cooldown, no step limit


def test_one_refused_item_among_several(root):
    traj, now = _traj(root), time.time()
    items = [{"section": s, "key": "max_rounds", "value": v, "reason": "r"} for s, v in (("defaults.food", 3), ("defaults.goods", 3), (BED, 2))]
    items.append({"section": BED, "key": "stock_target", "value": 30, "reason": "r"})
    res = pe.apply(root, items, traj, now)
    assert [r["ok"] for r in res] == [True, True, True, False]


def test_operator_live_off_applies_policy_and_drops_seller_edits(root, hroot):
    traj = _traj(root)
    o = op.Operator(root, FakeTerra([]), traj, harness_root=hroot, dry=True, spawn=lambda *a, **k: None)
    assert not o.live() and "규칙 훅이 정책 파일을 읽어" in o._mode_text()
    done = {
        "edits": [{"target": "seller", "files": {"guardrails.md": "x\n"}, "reason": "r"}],
        "restock_plan": {"buy": [{"sku": "S1", "qty": 3}], "valid_until": time.time() + 600},
        "policy": [{"section": BED, "key": "max_rounds", "value": 2, "reason": "가구 재제안 1회"}],
    }
    res = o.finish(done, "", {}, time.time())
    assert res["policy"][0]["ok"] and _pol(root)["categories"][BED]["max_rounds"] == 2
    assert set(res["not_applied_live_off"]) == {"edits seller/buyer 1개", "restock_plan"}
    assert res["edits"] == [] and not os.path.exists(os.path.join(root, "a2/restock_plan.json"))
    assert "재입고 계획 없음" not in (o.wake_reason(time.time()) or "")


def test_operator_live_on_refuses_policy(root, hroot):
    open(os.path.join(root, "a2/LIVE"), "w").close()
    o = op.Operator(root, FakeTerra([]), _traj(root), harness_root=hroot, dry=True, spawn=lambda *a, **k: None)
    res = o.finish({"policy": [{"section": BED, "key": "max_rounds", "value": 2, "reason": "r"}]}, "", {}, time.time())
    assert not res["policy"][0]["ok"] and _pol(root)["categories"][BED].get("max_rounds") is None


def test_policy_tool_shows_values_and_history(root):
    pe.apply(root, [{"section": BED, "key": "price_mult", "value": 0.7, "reason": "침대 근거"}], _traj(root), time.time())
    out = tools.call("policy", "operator", root)
    assert BED in out and '"price_mult": 0.7' in out and "침대 근거" in out and SOFA in out
    assert "seller는 policy" in tools.call("policy", "seller", root)


def test_attempt1_validate_uses_its_load(root):
    v = pe.attempt1_validate(root)
    assert v(os.path.join(root, "policy.json"))["categories"][BED]["stock_target"] == 36
