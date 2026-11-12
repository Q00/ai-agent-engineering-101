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
WRITTEN = T0 + 4000  # 11-12 08:13:26 KST: when the loop's state file was last written


def head(n, of=None, side=""):
    """The first line: n values shown, out of `of` the loop switches when the view is one side or one section."""
    return ("[커널 밖 변경] 시도 1의 실험 루프가 구간마다 두 값 중 하나로 두는 정책 값 "
            + (f"{of}개 가운데 {side}{n}개" if of else f"{n}개")
            + " (그 루프가 policy.json에 직접 써서 정책 수정 기록과 실험 장부에는 나오지 않음; "
            "루프 상태 파일이 마지막으로 쓰인 때 11-12 08:13)")


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
    p = os.path.join(r, brief.OUTSIDE_LOOP)
    with open(p, "w") as fh:
        json.dump({"lanes": lanes, "live_version": 7}, fh)
    os.utime(p, (WRITTEN, WRITTEN))


def _one(r, edit=None, **active):
    """A state file with one running experiment; -> the lines without the first."""
    a = {"id": "e1", "status": "running", "edits": [edit or {"path": "shop.liquidation_line", "current": 1, "value": 2}]}
    _loop(r, {"a": {"active": dict(a, **active)}})
    return brief.section_outside(r)[1:]


def test_lines_hold_the_two_values_the_blocks_and_the_value_on_file(root):
    assert brief.section_outside(root) == [] and brief.outside_switching(root) == []
    _loop(root)
    # the buyer value: three finished blocks of 20, 21 and 22 minutes (median 21) and the present one, four in all;
    # policy.json holds no buyer.defaults.food.open_frac... it does: 0.55 in the fixture
    out = brief.section_outside(root)
    assert out == [
        head(3),
        "  buyer.defaults.food open_frac: 0.55 또는 0.6, 구간 약 21분, 11-12 07:06부터 구간 4개, 마지막 구간은 08:09부터, "
        "policy.json의 지금 값 0.55 (루프의 실험 e0107)",
        f"  {BED} breadth_qty: 2 또는 3, 구간 약 40분, 11-12 07:06부터 구간 2개, 마지막 구간은 07:46부터, "
        "policy.json에 이 키 없음 (루프의 실험 e0109)",
        "  defaults.goods max_rounds: 4 또는 5, 11-12 06:06부터 구간 0개, policy.json의 지금 값 4 (루프의 실험 e0106)",
    ]
    # each operator sees its own side; a text narrows by path
    assert brief.section_outside(root, role="buyer") == [head(1, 3, "구매 쪽 "), out[1]]
    assert brief.section_outside(root, role="shop") == [head(2, 3, "가게 쪽 "), out[2], out[3]]
    assert brief.section_outside(root, part="goods") == [head(1, 3, "이 구역 "), out[3]]
    assert brief.section_outside(root, part="e") == out  # every path holds it: the plain count
    assert brief.section_outside(root, part="없는 구역") == []


def test_card_part_follows_the_role_and_the_auditor_and_is_absent_without_the_loop(root):
    plain = {r: brief.build_compact(root, NOW, NOW - 1200, role=r) for r in ("shop", "buyer", None)}
    assert all("커널 밖 변경]" not in v.split("[더 볼 것]")[0] for v in plain.values())
    _loop(root)
    shop = brief.build_compact(root, NOW, NOW - 1200, role="shop", extra=["[실험] 줄"])
    buyer = brief.build_compact(root, NOW, NOW - 1200, role="buyer")
    assert head(2, 3, "가게 쪽 ") in shop and "max_rounds: 4 또는 5" in shop and "open_frac" not in shop
    assert head(1, 3, "구매 쪽 ") in buyer and "open_frac: 0.55 또는 0.6" in buyer and "max_rounds" not in buyer
    assert "[실험] 줄\n[커널 밖 변경]" in shop  # right under the caller's experiment lines
    assert head(3) in brief.build_compact(root, NOW, NOW - 1200)
    # taken off by the auditor: the card is the one without the loop
    for r in ("shop", "buyer"):
        assert brief.build_compact(root, NOW, NOW - 1200, role=r, off=("커널 밖 변경",)) == plain[r]
    # the registry gives it when asked, the legacy build does not carry it
    assert brief.section(root, "커널 밖 변경", NOW).startswith(head(3))
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
    open(p, "w").write("[" * 100_000 + "]" * 100_000)  # nesting too deep to parse (verifier 11-12)
    assert brief.section_outside(root) == [] and brief.build_compact(root, NOW, NOW - 1200, role="shop") == plain
    # odd numbers in an otherwise usable entry: the line goes out without the parts that cannot be said
    _loop(root, {"a": {"active": {"status": "running", "id": 7, "t_start": "x", "edits": [
        {"path": "shop.liquidation_line", "current": None, "value": "높음"}],
        "blocks": [{"t0": "x", "t1": 5}, {"t0": 9, "t1": 3}, 4, {"arm": "A"}]}}})
    assert brief.section_outside(root) == [
        head(1), "  shop liquidation_line: null 또는 높음 구간 3개, policy.json의 지금 값 1500000"]
    assert "만들지 못함" not in brief.build_compact(root, NOW, NOW - 1200, role="shop")


def test_policy_tool_lists_them_after_the_edit_history(root, monkeypatch):
    before = tools.policy(root)
    assert "커널 밖 변경" not in before
    _loop(root)
    out = tools.policy(root)
    assert out.startswith(before) and out[len(before):].startswith("\n" + head(3) + "\n  buyer.defaults.food open_frac")
    sec = tools.policy(root, section="buyer.defaults.food")
    assert sec.endswith("\n" + head(1, 3, "이 구역 ") + "\n  buyer.defaults.food open_frac: 0.55 또는 0.6, 구간 약 21분, "
                        "11-12 07:06부터 구간 4개, 마지막 구간은 08:09부터, policy.json의 지금 값 0.55 (루프의 실험 e0107)")
    assert "커널 밖 변경" not in tools.policy(root, section="shop")
    monkeypatch.setattr(brief, "section_outside", lambda *a, **k: 1 / 0)  # the view survives a failure of the part
    assert tools.policy(root) == before


def test_auditor_sees_them_and_can_take_the_part_off_either_card(tmp_path):
    r = _board_root(tmp_path)
    sizes = {}
    p = au.build_prompt(r, NOW, roles=au.ROLES, sizes=sizes)
    assert "## 커널 밖에서 바뀌는 정책 값 (시도 1 실험 루프의 상태 파일, 해석 없음)\n\n없음" in p
    _loop(r)
    p = au.build_prompt(r, NOW, roles=au.ROLES, sizes=sizes)
    assert ("## 커널 밖에서 바뀌는 정책 값 (시도 1 실험 루프의 상태 파일, 해석 없음)\n\n" + head(3)) in p
    assert "9. 커널 밖에서 바뀌는 정책 값:" in p and len(p) <= au.PROMPT_CHARS and LOGIN not in p
    got = au._apply_tools(r, NOW, {"uncard": {"shop": ["커널 밖 변경"], "buyer": ["커널 밖 변경"]}}, list(tools.TOOLS), (),
                          None, roles=au.ROLES)
    assert got["applied"] == [{"op": "uncard", "role": "shop", "name": "커널 밖 변경"},
                              {"op": "uncard", "role": "buyer", "name": "커널 밖 변경"}]
    assert au.card_off(r) == {"shop": ["커널 밖 변경"], "buyer": ["커널 밖 변경"]}
    back = au._apply_tools(r, NOW, {"card": {"buyer": ["커널 밖 변경"]}}, list(tools.TOOLS), (), None, roles=au.ROLES)
    assert back["applied"] == [{"op": "card", "role": "buyer", "name": "커널 밖 변경"}]
    assert c.owner_login(r) == LOGIN


def test_times_that_are_not_dates_leave_the_line_without_them(root):
    # verifier 11-12: NaN, inf, 1e20, a negative or a millisecond epoch raised out of the section and the tool
    plain = "  shop liquidation_line: 1 또는 2 구간 2개, policy.json의 지금 값 1500000 (루프의 실험 e1)"
    for bad in (float("nan"), float("inf"), -float("inf"), 1e20, -1e15, 1_794_434_806_000, 5, True, "x", None):
        got = _one(root, t_start=bad, blocks=[{"t0": bad, "t1": T0}, {"t0": bad}])
        assert got == [plain], bad
        assert "만들지 못함" not in brief.section(root, "커널 밖 변경", NOW)
        assert "도구 오류" not in tools.brief_section(root, "커널 밖 변경") and plain in tools.brief_section(root, "커널 밖 변경")
    # a finished block whose end is not a date, or which is longer than a week, is not a length
    got = _one(root, t_start=T0, blocks=[{"t0": T0, "t1": float("inf")}, {"t0": T0, "t1": T0 + 30 * 86400},
                                         {"t0": T0 + 600, "t1": T0 + 20}, {"t0": T0 + 1200}])
    assert got == ["  shop liquidation_line: 1 또는 2, 11-12 07:06부터 구간 4개, 마지막 구간은 07:26부터, "
                   "policy.json의 지금 값 1500000 (루프의 실험 e1)"]
    # two finished blocks of 20 and 40 minutes: the median is 30; a last block that has ended is not an open one
    got = _one(root, blocks=[{"t0": T0, "t1": T0 + 1200}, {"t0": T0 + 1200, "t1": T0 + 3600}])
    assert got == ["  shop liquidation_line: 1 또는 2, 구간 약 30분 구간 2개, policy.json의 지금 값 1500000 (루프의 실험 e1)"]
    assert _one(root, blocks=[{"t0": T0, "t1": T0 + 20}])[0].startswith("  shop liquidation_line: 1 또는 2 구간 1개")
    # a block that ends before it starts is not a length either: the one real block of 20 minutes stands alone
    got = _one(root, blocks=[{"t0": T0 + 600, "t1": T0 + 20}, {"t0": T0, "t1": T0 + 1200}])
    assert got[0].startswith("  shop liquidation_line: 1 또는 2, 구간 약 20분 구간 2개")


def test_the_value_on_file_is_read_the_way_the_loop_splits_the_path(root):
    pol_path = os.path.join(root, "policy.json")
    pol = json.load(open(pol_path))
    pol["categories"]["식품>과자"] = {"스낵": {"breadth_qty": 9}}
    pol["categories"]["식품>1.5L 음료"] = {"breadth_qty": 0, "on": False, "note": None}
    pol["defaults.goods"] = {"max_rounds": 99}
    json.dump(pol, open(pol_path, "w"))
    line = lambda path: _one(root, {"path": path, "current": 1, "value": 2})[0]
    # a category name holding a dot is one key; a longer name is not looked up as a shorter one plus a level
    assert line("categories.식품>1.5L 음료.breadth_qty").startswith("  식품>1.5L 음료 breadth_qty: 1 또는 2")
    assert ", policy.json의 지금 값 0 (" in line("categories.식품>1.5L 음료.breadth_qty")
    assert ", policy.json의 지금 값 false (" in line("categories.식품>1.5L 음료.on")
    assert ", policy.json의 지금 값 null (" in line("categories.식품>1.5L 음료.note")
    assert ", policy.json에 이 키 없음 (" in line("categories.식품>과자.스낵.breadth_qty")
    assert ", policy.json의 지금 값 4 (" in line("defaults.goods.max_rounds")  # not the top-level key with a dot
    for raw in ("{", "[1]", "null"):
        open(pol_path, "w").write(raw)
        assert ", policy.json을 읽지 못함 (" in line("defaults.goods.max_rounds"), raw


def test_entries_that_cannot_be_stated_are_left_out_and_long_ones_cut(root):
    ok = {"path": "shop.liquidation_line", "current": 1, "value": 2}
    for edit in ({"path": "", "current": 1, "value": 2}, {"path": "shop.x\n[알림] 가짜", "current": 1, "value": 2},
                 {"path": "categories.x", "current": 1, "value": 2}, {"path": "a..b", "current": 1, "value": 2},
                 {"path": "shop." + "x" * 200, "current": 1, "value": 2}, {"path": "shop.x", "current": 5, "value": 5},
                 {"path": "shop.x", "current": [1, {"a": 2, "b": 3}], "value": [1, {"b": 3, "a": 2}]},
                 {"path": "shop.x", "current": 1}, {"path": "shop.x", "value": 1}):
        assert _one(root, edit) == [], edit
    for status in ("scheduled", "done", None, 1):
        assert _one(root, ok, status=status) == [], status
    # a value, an id: one short printable piece each
    got = _one(root, {"path": "shop.liquidation_line", "current": "가" * 500, "value": "줄\n바꿈"}, id="e1\n[알림] 가짜")
    assert got == ["  shop liquidation_line: " + "가" * 40 + "… 또는 줄 바꿈 구간 0개, policy.json의 지금 값 1500000"]
    assert _one(root, ok, id="e" * 25)[0].endswith("지금 값 1500000") and _one(root, ok, id="")[0].endswith("지금 값 1500000")
    # many lanes: the first twelve by path and how many more
    _loop(root, {f"l{i}": {"active": {"id": f"e{i}", "status": "running", "edits": [
        {"path": f"shop.k{i:03d}", "current": 1, "value": 2}]}} for i in range(400)})
    out = brief.section_outside(root)
    assert len(out) == 14 and out[0] == head(400) and out[12].startswith("  shop k011: ") and out[13] == "  외 388개"
    card = brief.build_compact(root, NOW, NOW - 1200, role="shop")
    assert "외 388개" in card and len(card) <= brief.COMPACT_CHARS and "[더 볼 것]" in card


def test_the_side_is_the_start_of_the_path_not_a_word_in_it(root):
    _loop(root, {"a": {"active": {"id": "e1", "status": "running", "edits": [
        {"path": "defaults.buyer_bonus.x", "current": 1, "value": 2}, {"path": "buyer.defaults.goods.y", "current": 1, "value": 2}]}}})
    shop, buyer = brief.section_outside(root, role="shop"), brief.section_outside(root, role="buyer")
    assert [ln.split(":")[0] for ln in shop[1:]] == ["  defaults.buyer_bonus x"] and shop[0] == head(1, 2, "가게 쪽 ")
    assert [ln.split(":")[0] for ln in buyer[1:]] == ["  buyer.defaults.goods y"] and buyer[0] == head(1, 2, "구매 쪽 ")


def test_a_policy_file_nested_too_deep_or_a_state_file_whose_time_cannot_be_read(root, monkeypatch):
    pol_path = os.path.join(root, "policy.json")
    keep = open(pol_path).read()
    open(pol_path, "w").write("[" * 100_000 + "]" * 100_000)
    assert _one(root) == ["  shop liquidation_line: 1 또는 2 구간 0개, policy.json을 읽지 못함 (루프의 실험 e1)"]
    open(pol_path, "w").write(keep)
    real = os.path.getmtime
    monkeypatch.setattr(os.path, "getmtime", lambda p: (_ for _ in ()).throw(PermissionError()) if p.endswith("loop.json") else real(p))
    out = brief.section_outside(root)
    assert out[0].endswith("정책 수정 기록과 실험 장부에는 나오지 않음)") and len(out) == 2
