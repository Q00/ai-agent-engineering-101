"""Side-by-side trial (동시 비교, 2026-11-06): the operator runs a seller candidate next to the current version."""

import json
import os
import shutil

import pytest

from kernel import brief, versions
from kernel import operator as op
from kernel import seller_bridge as sb
from tests.test_operator import LOGIN, NOW, FakeTerra, _rows
from tests.test_seller_bridge import RecAgent, _w, make_bridge, talk
from kernel.trajectory import Trajectory

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NEW_PROMPT = {"systemprompt.md": "# 판매자 후보\n- 후보 판 지시\n"}


@pytest.fixture(autouse=True)
def trials_on(monkeypatch):
    # 동시 비교는 운영에서 꺼져 있지만(versions.TRIAL_ENABLED) 장치 자체의 동작은 계속 검사함
    monkeypatch.setattr(versions, "TRIAL_ENABLED", True)


@pytest.fixture
def hroot(tmp_path):
    proj = tmp_path / "proj"
    shutil.copytree(os.path.join(HERE, "harness"), proj / "harness")
    return str(proj / "harness")


def _current(hroot, agent="seller"):
    return int(open(os.path.join(hroot, agent, "VERSION")).read())


def test_trial_start_stores_a_candidate_without_installing_it(hroot):
    base = _current(hroot)
    ok, info = versions.start_trial(hroot, "seller", NEW_PROMPT, share=0.99, reason="r", now=NOW)
    assert ok and info["base"] == base and info["version"] > base and info["share"] == 0.9  # share is bounded
    assert _current(hroot) == base  # the current version is untouched
    d = versions.trial_dir(hroot, "seller", info["version"])
    assert "후보 판 지시" in open(os.path.join(d, "systemprompt.md")).read()
    assert versions.trial_state(hroot)["seller"]["version"] == info["version"]
    ok2, info2 = versions.start_trial(hroot, "seller", NEW_PROMPT, now=NOW)
    assert not ok2 and "이미 진행 중" in info2["errors"][0]
    assert not versions.start_trial(hroot, "buyer", NEW_PROMPT, now=NOW)[0]


def test_trial_bad_files_start_nothing(hroot):
    ok, info = versions.start_trial(hroot, "seller", {"../evil.md": "x"}, now=NOW)
    assert not ok and info["errors"] and versions.trial_state(hroot) == {}


def test_trial_arm_is_deterministic_and_near_the_share(hroot):
    assert versions.trial_arm(hroot, "seller", "t1") == ("base", None)
    ok, info = versions.start_trial(hroot, "seller", NEW_PROMPT, share=0.3, now=NOW)
    arms = [versions.trial_arm(hroot, "seller", f"task-{i}")[0] for i in range(4000)]
    assert 0.27 < arms.count("trial") / 4000 < 0.33
    assert versions.trial_arm(hroot, "seller", "task-7") == versions.trial_arm(hroot, "seller", "task-7")
    assert versions.trial_arm(hroot, "seller", "task-7")[1] == info["version"]


def test_trial_end_adopt_installs_candidate_and_drop_does_not(hroot):
    base = _current(hroot)
    ok, info = versions.start_trial(hroot, "seller", NEW_PROMPT, now=NOW)
    ok, end = versions.end_trial(hroot, "seller", adopt=False, now=NOW + 1)
    assert ok and not end["adopted"] and _current(hroot) == base and versions.trial_state(hroot) == {}
    ok, info = versions.start_trial(hroot, "seller", NEW_PROMPT, now=NOW + 2)
    ok, end = versions.end_trial(hroot, "seller", adopt=True, reason="좋음", now=NOW + 3)
    assert ok and end["adopted"] and _current(hroot) == end["version"] > info["version"]
    assert "후보 판 지시" in open(os.path.join(hroot, "seller/systemprompt.md")).read()
    assert not versions.end_trial(hroot, "seller", adopt=True, now=NOW + 4)[0]  # nothing running


def test_trial_bridge_routes_arms_and_tags_metrics(tmp_path, hroot, monkeypatch):
    monkeypatch.setattr(sb, "HARNESS", hroot)
    ok, info = versions.start_trial(hroot, "seller", NEW_PROMPT, share=0.5, now=NOW)
    w = _w(tmp_path)
    tasks = [f"task-{i}" for i in range(40)]
    want = {t: versions.trial_arm(hroot, "seller", t)[0] for t in tasks}
    t_trial = next(t for t in tasks if want[t] == "trial")
    t_base = next(t for t in tasks if want[t] == "base")
    ag = RecAgent(
        w,
        [
            {"action": "offer", "unit": "u1", "ask": 280},
            {"action": "accept", "price": 260},
            {"action": "offer", "unit": "u2", "ask": 290},
        ],
        os.path.join(hroot, "seller"),
    )
    metrics = []
    b = make_bridge(w, ag, metrics=metrics)
    talk(b, t_trial, "I want Oak Bed (G00001)")
    talk(b, t_trial, "I offer 260")
    talk(b, t_base, "I want Oak Bed (G00001)")
    assert "후보 판 지시" in ag.terra.prompts[0] and "후보 판 지시" in ag.terra.prompts[1]
    assert "후보 판 지시" not in ag.terra.prompts[2]
    tagged = [(d, o.get("arm"), o.get("trial_v")) for d, o in metrics if d in ("open", "accept")]
    assert tagged == [("open", "trial", info["version"]), ("accept", "trial", info["version"]), ("open", "base", info["version"])]
    rows = [r for r in _rows(str(w), "decision")]
    assert [(r["arm"], r["harness_version"]) for r in rows] == [
        ("trial", info["version"]), ("trial", info["version"]), ("base", _current(hroot))
    ]


def test_trial_broken_candidate_falls_back_to_current(tmp_path, hroot, monkeypatch):
    monkeypatch.setattr(sb, "HARNESS", hroot)
    ok, info = versions.start_trial(hroot, "seller", NEW_PROMPT, share=0.9, now=NOW)
    os.remove(os.path.join(versions.trial_dir(hroot, "seller", info["version"]), "routing.json"))
    w = _w(tmp_path)
    t = next(f"task-{i}" for i in range(50) if versions.trial_arm(hroot, "seller", f"task-{i}")[0] == "trial")
    ag = RecAgent(w, [{"action": "offer", "unit": "u1", "ask": 280}], os.path.join(hroot, "seller"))
    ok, log = talk(make_bridge(w, ag), t, "I want Oak Bed (G00001)")
    assert ok and log[0][0] == "input"  # the sale went on with the current version
    (row,) = _rows(str(w), "decision")
    assert row["harness_version"] == _current(hroot) and any("trial harness" in e for e in row["errors"])


def _metric_rows(root, rows):
    os.makedirs(os.path.join(root, ".r3"), exist_ok=True)
    with open(os.path.join(root, ".r3/metrics.jsonl"), "a", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(dict(r, hook="seller"), ensure_ascii=False) + "\n")


def test_trial_brief_compares_both_arms(tmp_path, hroot):
    root = str(tmp_path / "data")
    os.makedirs(os.path.join(root, ".market"))
    with open(os.path.join(root, ".market/catalog.json"), "w") as f:
        json.dump({"BED1": {"category": "가구>침대", "list_price": 1000}}, f)
    assert brief.section_trial(root, NOW, hroot)[0].startswith("[동시 비교] 진행 중 없음")
    ok, info = versions.start_trial(hroot, "seller", NEW_PROMPT, share=0.5, reason="가격 낮춤", now=NOW)
    v = info["version"]
    rows = []
    for arm, n_open, n_decl, sales in (("base", 10, 10, 2), ("trial", 10, 10, 5)):
        tag = {"arm": arm, "trial_v": v}
        rows += [{"ts": NOW + 10, "decision": "open", "outcome": dict(tag, sku="BED1")} for _ in range(n_open)]
        rows += [{"ts": NOW + 10, "decision": "declined", "outcome": dict(tag)} for _ in range(n_decl)]
        rows += [{"ts": NOW + 20, "decision": "accept", "outcome": dict(tag, sku="BED1", price=700, cost=500)} for _ in range(sales)]
    rows.append({"ts": NOW + 20, "decision": "accept", "outcome": {"arm": "trial", "trial_v": v - 1, "sku": "BED1", "price": 1}})  # another trial
    rows.append({"ts": NOW - 20, "decision": "open", "outcome": {"arm": "trial", "trial_v": v, "sku": "BED1"}})  # before start
    _metric_rows(root, rows)
    r = brief.trial_results(root, NOW + 3600, hroot)
    b, t = r["arms"]["base"], r["arms"]["trial"]
    assert (b["requests"], b["offers"], b["sales"]) == (20, 10, 2) and (t["requests"], t["sales"]) == (20, 5)
    assert t["conversion"] == 0.25 and t["revenue_per_request"] == pytest.approx(175.0)
    assert t["margin_per_request"] == pytest.approx(50.0) and t["price_to_list_median"] == 0.7
    text = "\n".join(brief.section_trial(root, NOW + 3600, hroot))
    assert f"후보 판 {v} 대 지금 판 {info['base']}" in text and "무작위" in text and "가격 낮춤" in text
    assert "차이(후보 - 지금): 판매/요청 +0.150" in text and "침대 2/10, 5/10" in text


def test_trial_operator_start_blocks_base_edits_and_adopts(tmp_path, hroot):
    root = str(tmp_path / "data")
    for d in (".market", ".r3", "a2/board"):
        os.makedirs(os.path.join(root, d))
    with open(os.path.join(root, "policy.json"), "w") as f:
        json.dump({"owner": {"github": LOGIN}}, f)
    traj = Trajectory(os.path.join(root, "a2/trajectory.jsonl"), secrets=(LOGIN,))
    o = op.Operator(root, FakeTerra([{"done": {"edits": []}}]), traj, harness_root=hroot, dry=True, spawn=lambda *a, **k: None)
    base = _current(hroot)
    res = o.finish({"trial": {"target": "seller", "files": NEW_PROMPT, "share": 0.5, "reason": "시험"}}, "", {}, NOW)
    assert res["trial"]["ok"] and _current(hroot) == base
    res = o.finish({"edits": [{"target": "seller", "files": {"guardrails.md": "x\n"}, "reason": "r"}],
                    "revert": [{"target": "seller", "version": base}]}, "", {}, NOW + 1)
    assert "동시 비교 중" in res["edit_errors"][0] and not res["revert"][0]["ok"] and _current(hroot) == base
    res = o.finish({"trial_end": {"target": "seller", "adopt": True, "reason": "후보가 나음"}}, "", {}, NOW + 2)
    assert res["trial_end"]["ok"] and res["trial_end"]["adopted"] and _current(hroot) == res["trial_end"]["version"]
    kinds = [r["kind"] for r in _rows(root)]
    assert kinds.count("trial_start") == 1 and kinds.count("trial_end") == 1 and "harness_edit" in kinds
    edit = [r for r in _rows(root, "harness_edit")][-1]
    assert edit["adopted_from"] == res["trial_end"]["trial_version"] and "동시 비교 채택" in edit["reason"]


def test_trial_timeout_ends_without_adopting_and_brief_shows_the_cap(tmp_path, hroot):
    root = str(tmp_path / "data")
    for d in (".market", ".r3", "a2/board"):
        os.makedirs(os.path.join(root, d))
    with open(os.path.join(root, "policy.json"), "w") as f:
        json.dump({"owner": {"github": LOGIN}}, f)
    traj = Trajectory(os.path.join(root, "a2/trajectory.jsonl"), secrets=(LOGIN,))
    o = op.Operator(root, FakeTerra([{"done": {"edits": []}}]), traj, harness_root=hroot, dry=True, spawn=lambda *a, **k: None)
    base = _current(hroot)
    ok, info = versions.start_trial(hroot, "seller", NEW_PROMPT, share=0.5, reason="시험", now=NOW)
    assert "시간 상한 3시간 (남은 2.0시간)" in brief.section_trial(root, NOW + 3600, hroot)[0]
    assert o.trial_timeouts(NOW + 2.9 * 3600) == [] and "seller" in versions.trial_state(hroot)
    res = o.trial_timeouts(NOW + versions.TRIAL_MAX_H * 3600)
    assert res[0]["ok"] and not res[0]["adopted"] and not versions.trial_state(hroot) and _current(hroot) == base
    end = [r for r in _rows(root, "trial_end")][-1]
    assert end["adopt"] is False and "커널 시간 상한" in end["reason"]
    assert brief.section_trial(root, NOW + 7 * 3600, hroot)[0].endswith("(시간 상한 3시간)")


def test_trial_off_refuses_start_and_brief_says_edits_apply_directly(tmp_path, hroot, monkeypatch):
    monkeypatch.setattr(versions, "TRIAL_ENABLED", False)
    root = str(tmp_path / "w")
    os.makedirs(root)
    traj = Trajectory(os.path.join(root, "trajectory.jsonl"))
    o = op.Operator(root, FakeTerra([]), traj, harness_root=hroot, dry=True, spawn=lambda *a, **k: None)
    res = o._trial_start({"target": "seller", "files": NEW_PROMPT, "share": 0.5, "reason": "시험"}, NOW)
    assert res["ok"] is False and "꺼져 있음" in res["errors"][0]
    assert versions.trial_state(hroot) == {}
    assert brief.section_trial(root, NOW, hroot)[0].startswith("[동시 비교] 꺼짐")
