"""The auditor's [목표 진척] facts: goal progress, hours since our total rose, ended experiments and code changes by
verdict; and its [shop] / [buyer] findings reaching the operators."""

import json
import os

from kernel import auditor as au
from kernel import questions
from kernel.trajectory import Trajectory
from tests.test_auditor import Script
from tests.test_goal_progress import CUR, H, LOGIN, NOW, OLD6, OLD24


def _root(tmp_path):
    root = str(tmp_path)
    bd = tmp_path / "icrl/state/board"
    bd.mkdir(parents=True)
    at3 = json.loads(json.dumps(OLD6))
    next(p for p in at3["players"] if p["player"] == LOGIN)["total"] = 1.39
    # our total: 24 h 1.40, 6 h 1.37, 3 h 1.39, now 1.38 -> last rise at the 3 h snapshot
    for ts, b in ((NOW, CUR), (NOW - 3 * H, at3), (NOW - 6 * H, OLD6), (NOW - 24 * H, OLD24)):
        (bd / f"{int(ts)}.json").write_text(json.dumps(b))
    (tmp_path / "policy.json").write_text(json.dumps({"owner": {"github": LOGIN}}))
    os.makedirs(os.path.join(root, "a2"))
    v = lambda d, r: {"decision": d, "reason": r}
    exps = [
        {"id": 1, "status": "kept", "ended_ts": NOW - H, "verdict": v("better", "clear")},
        {"id": 2, "status": "measured", "ended_ts": NOW - 2 * H, "verdict": v("worse", "stars")},
        {"id": 3, "status": "kept", "ended_ts": NOW - 3 * H, "verdict": v("unclear", "noise")},
        {"id": 4, "status": "measured", "ended_ts": NOW - 4 * H, "verdict": v("thin", "thin")},
        {"id": 5, "status": "voided", "ended_ts": NOW - 5 * H, "verdict": v("voided", "value_changed")},
        {"id": 6, "status": "kept", "ended_ts": NOW - 30 * H, "verdict": v("better", "clear")},  # older than 24 h
        {"id": 7, "status": "running"},
    ]
    json.dump(exps, open(os.path.join(root, "a2/experiments.json"), "w"))
    codes = [
        {"id": 1, "status": "kept", "ended_ts": NOW - H,
         "verdict": {"metrics": ["shop"], "shop": v("better", "clear"), "decision": "ok"}},
        {"id": 2, "status": "reverted", "ended_ts": NOW - 2 * H,
         "verdict": {"metrics": ["shop", "buyer"], "shop": v("unclear", "noise"), "buyer": v("thin", "thin")}},
        {"id": 3, "status": "reverted", "ended_ts": NOW - 3 * H},  # canary revert, no verdict
        {"id": 4, "status": "watching"},
        {"id": 5, "status": "kept", "ended_ts": NOW - 48 * H, "verdict": {"metrics": ["shop"], "shop": v("better", "clear")}},
    ]
    json.dump(codes, open(os.path.join(root, "a2/code_changes.json"), "w"))
    return root


def test_auditor_progress_facts_in_the_prompt(tmp_path):
    root = _root(tmp_path)
    p = au.build_prompt(root, NOW, roles=au.ROLES)
    block = p.split("## 목표 진척 (순위표와 실험·코드 판정, 커널이 셈. 해석 없음)\n\n")[1].split("\n\n## ")[0]
    assert block.startswith("[목표 진척] 회차 종료까지 30시간 | 우리 3명 중 2위 total 1.380, 1위 1.500, 차이 0.120")
    assert "우리 0.775 3위, 예상 1위 1.350 (지금 1위)" in block and "가게 항목" in block and "구매자 항목" in block
    assert "우리 total이 마지막으로 오른 순위표: 3.0시간 전" in block
    assert "최근 24시간 끝난 실험 5건: 판정 뚜렷(잡음 넘음·별점) 2, 불분명(잡음 안·표본 적음·원가 모름) 2, 그 밖(무효·기준 없음 등) 1" in block
    assert "최근 24시간 끝난 코드 수정 3건: 판정 뚜렷 1, 불분명 1, 그 밖 0, 판정 없이 끝남(되돌림·무효 등) 1" in block
    assert "10. 목표 진척:" in p and LOGIN not in p and len(p) <= au.PROMPT_CHARS


def test_auditor_progress_without_a_rise_or_snapshots(tmp_path):
    root = str(tmp_path)
    os.makedirs(os.path.join(root, "a2"))
    block = au._goal_block(root, NOW)
    assert block.startswith("[목표 진척] 자료 없음\n우리 total이 오른 순위표: 최근 72시간 스냅샷 0개 안에 없음")
    assert "끝난 실험 0건" in block


def test_auditor_progress_findings_reach_the_tagged_operator(tmp_path):
    root = _root(tmp_path)
    ans = {"findings": ["[shop] 반복 호출 3건", "[buyer] 오류 답 2건", "[shop] 근거 없는 숫자 1건"], "note": "n"}
    traj = Trajectory(os.path.join(root, "a2/trajectory.jsonl"))
    out = au.run(root, Script(ans), traj, NOW, listed=["policy"], roles=au.ROLES)
    assert out["status"] == "done"
    shop = "\n".join(questions.prompt_lines(root, "shop", NOW))
    buyer = "\n".join(questions.prompt_lines(root, "buyer", NOW))
    assert "감사자 질문: 감사자가 짚은 것" in shop and "결과: [shop] 반복 호출 3건 / [shop] 근거 없는 숫자 1건" in shop
    assert "결과: [buyer] 오류 답 2건" in buyer and "[shop]" not in buyer
