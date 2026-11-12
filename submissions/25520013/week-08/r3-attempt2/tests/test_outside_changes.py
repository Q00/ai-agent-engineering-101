"""11-12: policy values a loop outside the kernel switches (attempt 1's experiment loop writes policy.json itself).
The operators' edit history and the ledger did not show them; the card, the policy tool and the auditor prompt do."""

import json
import os

from kernel import auditor as au
from kernel import brief, tools
from kernel import common as c
from tests.test_card_facts import LOGIN, NOW, _board_root
from tests.test_experiments import BED, root  # noqa: F401  (root is a fixture)

T0 = 1_794_434_806  # 11-12 07:06:46 KST
HEAD = ("[커널 밖 변경] 시도 1의 실험 루프가 구간마다 두 값 중 하나로 두는 정책 값 {}개 "
        "(그 루프가 policy.json에 직접 써서 정책 수정 기록과 실험 장부에는 나오지 않음)")


def _blocks(t0, spans):
    out, t = [], t0
    for i, s in enumerate(spans):
        out.append({"i": i, "arm": "AB"[i % 2], "t0": t, "t1": t + s})
        t += s
    out.append({"i": len(spans), "arm": "B", "t0": t})  # the present block has no end yet
    return out


def _loop(r, lanes=None):
    lanes = lanes if lanes is not None else {
        "buyer": {"active": {"id": "e0107", "status": "running", "t_start": T0,
                             "edits": [{"path": "buyer.defaults.food.open_frac", "current": 0.55, "value": 0.6}],
                             "blocks": _blocks(T0, [1200, 1260, 1320])}},
        "price": {"active": {"id": "e0106", "status": "running", "t_start": T0 - 3600,
                             "edits": [{"path": "defaults.goods.max_rounds", "current": 4, "value": 5}], "blocks": []}},
        "stock": {"active": {"id": "e0109", "status": "running", "t_start": T0,
                             "edits": [{"path": f"categories.{BED}.breadth_qty", "current": 2, "value": 3}],
                             "blocks": _blocks(T0, [2400])}},
        "done": {"active": {"id": "e0001", "status": "done", "edits": [{"path": "shop.x", "current": 1, "value": 2}]}},
        "idle": {"active": None},
    }
    os.makedirs(os.path.join(r, "icrl/state"), exist_ok=True)
    json.dump({"lanes": lanes, "live_version": 7}, open(os.path.join(r, brief.OUTSIDE_LOOP), "w"))


def test_lines_hold_the_two_values_the_blocks_and_the_value_on_file(root):
    assert brief.section_outside(root) == [] and brief.outside_switching(root) == []
    _loop(root)
    # the buyer value: three finished blocks of 20, 21 and 22 minutes (median 21) and the present one, four in all;
    # policy.json holds no buyer.defaults.food.open_frac... it does: 0.55 in the fixture
    out = brief.section_outside(root)
    assert out == [
        HEAD.format(3),
        "  buyer.defaults.food open_frac: 0.55 또는 0.6, 구간 약 21분, 11-12 07:06부터 구간 4개, 지금 구간은 08:09부터, "
        "policy.json의 지금 값 0.55 (루프의 실험 e0107)",
        f"  {BED} breadth_qty: 2 또는 3, 구간 약 40분, 11-12 07:06부터 구간 2개, 지금 구간은 07:46부터, "
        "policy.json에 지금 값 없음 (루프의 실험 e0109)",
        "  defaults.goods max_rounds: 4 또는 5, 11-12 06:06부터 구간 0개, policy.json의 지금 값 4 (루프의 실험 e0106)",
    ]
    # each operator sees its own side; a text narrows by path
    assert brief.section_outside(root, role="buyer") == [HEAD.format(1), out[1]]
    assert brief.section_outside(root, role="shop") == [HEAD.format(2), out[2], out[3]]
    assert brief.section_outside(root, part="goods") == [HEAD.format(1), out[3]]
    assert brief.section_outside(root, part="없는 구역") == []


def test_card_part_follows_the_role_and_the_auditor_and_is_absent_without_the_loop(root):
    plain = {r: brief.build_compact(root, NOW, NOW - 1200, role=r) for r in ("shop", "buyer", None)}
    assert all("커널 밖 변경]" not in v.split("[더 볼 것]")[0] for v in plain.values())
    _loop(root)
    shop = brief.build_compact(root, NOW, NOW - 1200, role="shop", extra=["[실험] 줄"])
    buyer = brief.build_compact(root, NOW, NOW - 1200, role="buyer")
    assert HEAD.format(2) in shop and "max_rounds: 4 또는 5" in shop and "open_frac" not in shop
    assert HEAD.format(1) in buyer and "open_frac: 0.55 또는 0.6" in buyer and "max_rounds" not in buyer
    assert shop.index("[실험] 줄") < shop.index("[커널 밖 변경]")  # right under the caller's experiment lines
    assert HEAD.format(3) in brief.build_compact(root, NOW, NOW - 1200)
    # taken off by the auditor: the card is the one without the loop
    for r in ("shop", "buyer"):
        assert brief.build_compact(root, NOW, NOW - 1200, role=r, off=("커널 밖 변경",)) == plain[r]
    # the registry gives it when asked, the legacy build does not carry it
    assert brief.section(root, "커널 밖 변경", NOW).startswith(HEAD.format(3))
    assert "커널 밖 변경" not in brief.build(root, NOW)
    os.remove(os.path.join(root, brief.OUTSIDE_LOOP))
    assert brief.section(root, "커널 밖 변경", NOW) == "[커널 밖 변경] 지금 없음"


def test_a_broken_loop_file_reads_as_nothing(root):
    p = os.path.join(root, brief.OUTSIDE_LOOP)
    os.makedirs(os.path.dirname(p))
    plain = brief.build_compact(root, NOW, NOW - 1200, role="shop")
    for raw in ("{", "[1, 2]", "null", '{"lanes": 3}', '{"lanes": {"a": 1, "b": {"active": 2}, "c": {"active": {"status": "running"}}}}',
                '{"lanes": {"a": {"active": {"status": "running", "edits": [1, {"path": 3}, {"path": "shop.x"}], "blocks": "x"}}}}'):
        open(p, "w").write(raw)
        assert brief.outside_switching(root) == [] and brief.section_outside(root) == [], raw
        assert brief.build_compact(root, NOW, NOW - 1200, role="shop") == plain
    # odd numbers in an otherwise usable entry: the line goes out without the parts that cannot be said
    _loop(root, {"a": {"active": {"status": "running", "id": 7, "t_start": "x", "edits": [
        {"path": "shop.liquidation_line", "current": None, "value": "높음"}],
        "blocks": [{"t0": "x", "t1": 5}, {"t0": 9, "t1": 3}, 4, {"arm": "A"}]}}})
    assert brief.section_outside(root) == [
        HEAD.format(1), "  shop liquidation_line: null 또는 높음 구간 3개, policy.json의 지금 값 1500000 (루프의 실험 7)"]
    assert "만들지 못함" not in brief.build_compact(root, NOW, NOW - 1200, role="shop")


def test_policy_tool_lists_them_after_the_edit_history(root):
    before = tools.policy(root)
    assert "커널 밖 변경" not in before
    _loop(root)
    out = tools.policy(root)
    assert out.startswith(before) and out[len(before):].startswith("\n" + HEAD.format(3) + "\n  buyer.defaults.food open_frac")
    sec = tools.policy(root, section="buyer.defaults.food")
    assert sec.endswith("\n" + HEAD.format(1) + "\n  buyer.defaults.food open_frac: 0.55 또는 0.6, 구간 약 21분, "
                        "11-12 07:06부터 구간 4개, 지금 구간은 08:09부터, policy.json의 지금 값 0.55 (루프의 실험 e0107)")
    assert "커널 밖 변경" not in tools.policy(root, section="shop")


def test_auditor_sees_them_and_can_take_the_part_off_either_card(tmp_path):
    r = _board_root(tmp_path)
    sizes = {}
    p = au.build_prompt(r, NOW, roles=au.ROLES, sizes=sizes)
    assert "## 커널 밖에서 바뀌는 정책 값 (시도 1 실험 루프의 상태 파일, 해석 없음)\n\n없음" in p
    _loop(r)
    p = au.build_prompt(r, NOW, roles=au.ROLES, sizes=sizes)
    assert ("## 커널 밖에서 바뀌는 정책 값 (시도 1 실험 루프의 상태 파일, 해석 없음)\n\n" + HEAD.format(3)) in p
    assert "9. 커널 밖에서 바뀌는 정책 값:" in p and len(p) <= au.PROMPT_CHARS and LOGIN not in p
    got = au._apply_tools(r, NOW, {"uncard": {"shop": ["커널 밖 변경"], "buyer": ["커널 밖 변경"]}}, list(tools.TOOLS), (),
                          None, roles=au.ROLES)
    assert got["applied"] == [{"op": "uncard", "role": "shop", "name": "커널 밖 변경"},
                              {"op": "uncard", "role": "buyer", "name": "커널 밖 변경"}]
    assert au.card_off(r) == {"shop": ["커널 밖 변경"], "buyer": ["커널 밖 변경"]}
    back = au._apply_tools(r, NOW, {"card": {"buyer": ["커널 밖 변경"]}}, list(tools.TOOLS), (), None, roles=au.ROLES)
    assert back["applied"] == [{"op": "card", "role": "buyer", "name": "커널 밖 변경"}]
    assert c.owner_login(r) == LOGIN
