"""Role operators (kernel.operator --role shop|buyer): per-role job, notes, state, wake rule and permissions over one
shared toolbox, ledger, auditor and trajectory; routing of verdicts; the busy lock; tool_search; the per-role audit and
auditor actions. No role must stay the single operator (the other test files)."""

import json
import os
import shutil

import pytest
from kernel import audit, made_tools, memfeed, tools
from kernel import auditor as au
from kernel import code_edit as ce
from kernel import experiments as ex
from kernel import operator as op
from kernel.trajectory import Trajectory
from tests import test_policy_edit_wide as W

NOW = 1_794_000_000.0
H = 3600


class Script:
    """Model stand-in: answers the queued actions in order and records every prompt."""

    def __init__(self, *acts):
        self.acts, self.prompts = list(acts), []

    def complete(self, prompt, *, agent, timeout_s, effort="medium"):
        self.prompts.append(prompt)
        a = self.acts.pop(0)
        return a if isinstance(a, str) else json.dumps(a, ensure_ascii=False)


def _root(tmp_path):
    root = str(tmp_path / "root")
    os.makedirs(os.path.join(root, "a2"), exist_ok=True)
    json.dump(
        W.POLICY,
        open(os.path.join(root, "policy.json"), "w", encoding="utf-8"),
        ensure_ascii=False,
    )
    shutil.copy(W.A1_POLICY, os.path.join(root, "policy.py"))
    return root


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("A2_OPERATOR_COMPACT", "1")
    monkeypatch.delenv("A2_OPERATOR_INTERVAL_S", raising=False)
    monkeypatch.delenv("A2_SHOP_LEAD_S", raising=False)
    monkeypatch.delenv("A2_SPLIT", raising=False)
    root = _root(tmp_path)

    def make(role):
        traj = Trajectory(os.path.join(root, "a2/trajectory.jsonl"), role=role)
        return op.Operator(
            root, None, traj, harness_root=str(tmp_path / "noharness"), role=role
        )

    return root, make


def _rows(root):
    p = os.path.join(root, "a2/trajectory.jsonl")
    return (
        [json.loads(x) for x in open(p, encoding="utf-8")] if os.path.exists(p) else []
    )


def _item(key="price_mult", value=0.85, section="defaults.goods"):
    return {
        "section": section,
        "key": key,
        "value": value,
        "reason": "근거 1",
        "expect": "순이익 +0.1M/h",
    }


# ------------------------------------------------------------ trajectory, job, notes, state
def test_trajectory_rows_carry_the_role(tmp_path):
    p = str(tmp_path / "t.jsonl")
    Trajectory(p, role="buyer").append("operator", "tool", name="board")
    Trajectory(p).append("operator", "tool", name="board")
    Trajectory(p, role="shop").append(
        "operator", "experiment_end", role="buyer"
    )  # a row's own role wins
    rows = [json.loads(x) for x in open(p, encoding="utf-8")]
    assert (
        rows[0]["role"] == "buyer"
        and "role" not in rows[1]
        and rows[2]["role"] == "buyer"
    )


def test_role_job_files_are_human_written_per_role():
    # 11-11: the role job texts are written by a human per role: goal, means and hard lines only, no work order
    other = {"shop": "구매 운영자가 같은 방식으로 맡음", "buyer": "가게 운영자가 같은 방식으로 맡음"}
    for r in op.ROLES:
        text = open(os.path.join(op.HERE, f"OPERATOR_JOB_{r.upper()}.md"), encoding="utf-8").read()
        assert text.startswith(f"<!-- role: {r} -->\n")
        assert "## 목표와 권한" in text and "## 수단" in text and "## 넘을 수 없는 선" in text and other[r] in text
        assert "일하는 순서" not in text and "개까지" not in text and "번까지" not in text


def test_job_path_falls_back_to_the_single_job(env, monkeypatch, tmp_path):
    root, make = env
    assert make("shop")._job_path().endswith("OPERATOR_JOB_SHOP.md")
    assert make(None)._job_path() == op.JOB
    monkeypatch.setattr(op, "HERE", str(tmp_path))  # no role file there
    assert make("buyer")._job_path() == op.JOB


def test_notes_are_seeded_once_from_the_single_operator(env):
    root, make = env
    with open(os.path.join(root, "a2/operator_notes.md"), "w", encoding="utf-8") as f:
        f.write("단일 메모")
    shop = make("shop")
    assert shop._notes() == "단일 메모"
    with open(
        os.path.join(root, "a2/operator_notes_shop.md"), "w", encoding="utf-8"
    ) as f:
        f.write("가게 메모")
    make("shop")  # a second start does not seed again
    assert (
        make("shop")._notes() == "가게 메모" and make("buyer")._notes() == "단일 메모"
    )


def test_role_state_and_interval_are_separate(env, monkeypatch):
    root, make = env
    shop, single = make("shop"), make(None)
    assert shop.state_path.endswith(
        "operator_state_shop.json"
    ) and single.state_path.endswith("operator_state.json")
    assert (
        shop.settings()["interval_s"] == op.ROLE_INTERVAL_S
        and single.settings()["interval_s"] == 1800
    )
    monkeypatch.setenv("A2_OPERATOR_INTERVAL_S", "900")
    assert shop.settings()["interval_s"] == 900


def test_role_operators_do_not_wake_outside_compact_mode(env, monkeypatch):
    root, make = env
    monkeypatch.delenv("A2_OPERATOR_COMPACT")
    assert make("buyer").wake_reason(NOW) is None


# ------------------------------------------------------------ prompt: schema, tools, state card
def test_role_schema_lists_only_the_roles_keys_and_files():
    shop, buyer = op.role_schema("shop"), op.role_schema("buyer")
    assert '"section": "defaults.food|defaults.goods|<상품군 전체 경로>|shop"' in shop
    assert (
        "buyer.defaults" not in shop
        and "max_pay_frac" not in shop
        and "market_buyer" not in shop
    )
    assert (
        "<policy.py|hooks/restock.py|hooks/seller.py>" in shop
        and '"as": "shop"' in shop
    )
    assert (
        '"section": "buyer.defaults.food|buyer.defaults.goods"' in buyer
        and "max_pay_frac" in buyer
    )
    assert (
        "price_mult" not in buyer
        and "grade_floor_list" not in buyer
        and "hooks/restock.py" not in buyer
    )
    assert "<policy.py|hooks/market_buyer.py>" in buyer and '"as": "buyer"' in buyer
    assert "구매 운영자(buyer) 몫" in shop and "가게 운영자(shop) 몫" in buyer
    assert op.COMPACT_SCHEMA.split("\n")[0] == shop.split("\n")[0]


def test_wake_prompt_of_a_role(env):
    root, make = env
    o = make("buyer")
    o.terra = Script({"done": {"note": "n"}})
    res = o.wake("주기 점검", NOW)
    p = o.terra.prompts[0]
    assert res["status"] == "done"
    assert p.startswith("<!-- role: buyer -->")
    tool_part = p.split("## 도구\n\n")[1].split("## 답 형식")[0]
    assert "- catalog: " in tool_part and "- tool_search: " in tool_part
    assert "- stockouts: " not in tool_part
    rest = [ln for ln in tool_part.splitlines() if ln.startswith("- 그 밖의 도구")]
    assert (
        len(rest) == 1
        and "stockouts" in rest[0]
        and "tool_search" in rest[0]
        and "catalog" not in rest[0]
    )
    assert '"as": "buyer"' in p and "판매 구역은" not in p
    wake = [r for r in _rows(root) if r["kind"] == "operator_wake"][-1]
    assert wake["role"] == "buyer"


def test_single_operator_does_not_list_tool_search(env):
    root, make = env
    names = [n for n, _ in make(None).listed()]
    assert "tool_search" not in names and "stockouts" in names
    assert "tool_search" in [n for n, _ in make("shop").listed()]
    assert "tool_search" not in make(None)._tool_list()


def test_tool_rows_record_recommended_and_search_hits(env):
    root, make = env
    o = make("buyer")
    o._run_tool("stockouts", {"hours": 1})
    o._run_tool("board", {})
    o._run_tool("tool_search", {"query": "재고 없음"})
    o._run_tool("tool_search", {"query": "zzzqqq"})
    rows = [r for r in _rows(root) if r["kind"] == "tool"]
    assert [r["recommended"] for r in rows] == [False, True, True, True]
    assert all(r["role"] == "buyer" for r in rows)
    assert (
        rows[2]["query"] == "재고 없음"
        and rows[2]["hits"] >= 1
        and rows[3]["hits"] == 0
    )


def test_state_card_marks_the_other_roles_experiments_and_code(env):
    root, make = env
    rows = [
        {
            "id": 1,
            "mode": "sequential",
            "section": "defaults.goods",
            "key": "price_mult",
            "old": 0.9,
            "new": 0.85,
            "status": "kept",
            "ended_ts": NOW - 10,
            "side": "shop",
            "verdict": {},
            "role": "shop",
        },
        {
            "id": 2,
            "mode": "sequential",
            "section": "buyer.defaults.goods",
            "key": "max_pay_frac",
            "old": 0.9,
            "new": 0.8,
            "status": "reverted",
            "ended_ts": NOW - 5,
            "side": "buyer",
            "verdict": {},
        },
    ]
    ex.save(root, rows)
    shop = ex.lines(root, NOW, role="shop")
    assert [ln.endswith(ex.OTHER) for ln in shop[1:]] == [
        True,
        False,
    ]  # newest first: #2 is the buyer's
    assert not any(ln.endswith(ex.OTHER) for ln in ex.lines(root, NOW))
    ce._save(
        root,
        [
            {
                "id": 1,
                "files": ["policy.py"],
                "status": "kept",
                "ended_ts": NOW - 9,
                "ts": NOW - 9 * H,
                "why": "x",
            },
            {
                "id": 2,
                "files": ["hooks/market_buyer.py"],
                "status": "reverted",
                "ended_ts": NOW - 8,
                "ts": NOW - 8 * H,
                "why": "y",
            },
        ],
    )
    out = ce.lines(root, NOW, role="shop")
    assert (
        out[0].startswith("[코드] 수정 #2")
        and out[0].endswith(ex.OTHER)
        and not out[1].endswith(ex.OTHER)
    )
    assert not any(ln.endswith(ex.OTHER) for ln in ce.lines(root, NOW, role="buyer"))


# ------------------------------------------------------------ permissions
def test_policy_items_of_the_other_side_are_refused(env):
    root, make = env
    shop, buyer = make("shop"), make("buyer")
    r = shop._finish_compact(
        {"policy": [_item("max_pay_frac", 0.8, "buyer.defaults.goods"), _item()]}, NOW
    )
    assert not r["policy"][0]["ok"] and "구매 운영자(buyer)" in r["policy"][0]["error"]
    assert r["policy"][1]["ok"]
    r = buyer._finish_compact(
        {
            "policy": [
                _item("min_margin", 0.1, "defaults.food"),
                _item("max_pay_frac", 0.8, "buyer.defaults.goods"),
            ]
        },
        NOW + 1,
    )
    assert not r["policy"][0]["ok"] and "가게 운영자(shop)" in r["policy"][0]["error"]
    assert r["policy"][1]["ok"]
    # test none (the default): applied directly, so the role is on the trajectory row, not on a ledger entry
    roles = {x["key"]: x.get("role") for x in _rows(root) if x.get("kind") == "policy_edit"}
    assert roles == {"price_mult": "shop", "max_pay_frac": "buyer"} and ex.load(root) == []


def test_code_files_and_forum_of_the_other_side_are_refused(env):
    root, make = env
    shop = make("shop")
    code = {
        "edits": [{"file": "hooks/market_buyer.py", "old": "a", "new": "b"}],
        "reason": "r" * 30,
        "expect": "e" * 30,
    }
    r = shop._finish_compact(
        {
            "code": code,
            "forum": [
                {"post": "가게 글", "as": "shop"},
                {"post": "구매 글", "as": "buyer"},
            ],
        },
        NOW,
    )
    assert r["code"]["ok"] is False and "구매 운영자(buyer) 몫" in r["code"]["error"]
    assert r["forum"]["queued"] == 1 and "구매 운영자(buyer)" in r["forum"]["refused"]
    refused = [x for x in _rows(root) if x["kind"] == "code_edit_refused"]
    assert refused[-1]["cls"] == "role" and refused[-1]["role"] == "shop"
    assert ce.role_error(["policy.py", "hooks/market_buyer.py"], "buyer") is None
    assert "hooks/seller.py" in ce.role_error(["hooks/seller.py"], "buyer")


def test_made_tools_are_tagged_and_notes_are_per_role(env, monkeypatch):
    root, make = env
    seen = {}
    monkeypatch.setattr(
        made_tools,
        "add",
        lambda r, spec, now, **kw: seen.update(kw=kw) or {"ok": True, "name": "q_x"},
    )
    r = make("buyer")._finish_compact(
        {"new_tool": {"name": "q_x"}, "notes": "구매 메모"}, NOW
    )
    assert r["new_tool"]["ok"] and seen["kw"]["role"] == "buyer"
    assert (
        open(os.path.join(root, "a2/operator_notes_buyer.md"), encoding="utf-8").read()
        == "구매 메모"
    )
    assert not os.path.exists(os.path.join(root, "a2/operator_notes.md"))


def test_made_tool_store_keeps_the_role(tmp_path):
    root = str(tmp_path)
    os.makedirs(os.path.join(root, "a2"))
    spec = {
        "name": "q_buys",
        "description": "구매 시도를 센 수 (2시간)",
        "source": "trajectory",
        "hours": 2,
        "agg": [{"fn": "count", "as": "n"}],
    }
    assert made_tools.add(root, spec, NOW, role="buyer")["ok"]
    assert made_tools.load(root)[0]["role"] == "buyer"
    assert au.recommended(root, "buyer")[
        -1
    ] == "q_buys" and "q_buys" not in au.recommended(root, "shop")


# ------------------------------------------------------------ routing
def test_role_of_experiments_and_code_changes():
    assert (
        ex.role_of({"section": "buyer.defaults.food"}) == "buyer"
        and ex.role_of({"section": "shop"}) == "shop"
    )
    assert ex.role_of({"section": "buyer.defaults.food", "role": "shop"}) == "shop"
    assert ce.role_of({"files": ["policy.py"]}) == {"shop", "buyer"}
    assert ce.role_of({"files": ["policy.py"], "role": "buyer"}) == {"buyer"}
    assert ce.role_of({"files": ["hooks/market_buyer.py"]}) == {"buyer"}
    assert ce.role_of({"files": ["hooks/restock.py"]}) == {"shop"}


def test_verdicts_wake_the_role_that_proposed_them(env):
    root, make = env
    shop, buyer = make("shop"), make("buyer")
    for o in (shop, buyer):  # first poll marks the start; then both have just woken
        o.wake_reason(NOW)
        o._save_state(dict(o._state(), last_wake_ts=NOW))
    ex.save(
        root,
        [
            {
                "id": 1,
                "section": "buyer.defaults.goods",
                "key": "max_pay_frac",
                "status": "kept",
                "ended_ts": NOW + 10,
            },
            {
                "id": 2,
                "section": "defaults.goods",
                "key": "price_mult",
                "status": "reverted",
                "ended_ts": NOW + 11,
                "role": "shop",
            },
        ],
    )
    ce._save(
        root,
        [
            {
                "id": 1,
                "files": ["policy.py"],
                "status": "kept",
                "ended_ts": NOW + 12,
                "ts": NOW - H,
            }
        ],
    )
    rb, rs = buyer.wake_reason(NOW + 60), shop.wake_reason(NOW + 60)
    assert "실험 판정 1건" in rb and "코드 수정 판정 1건" in rb
    assert "실험 판정 1건" in rs and "코드 수정 판정 1건" in rs
    buyer.terra = Script({"done": {}})
    buyer.wake(rb, NOW + 60)
    assert (
        buyer.wake_reason(NOW + 120) is None
    )  # seen: no second wake for the same verdicts
    assert "실험 판정" in shop.wake_reason(NOW + 120)  # the shop has not woken yet
    ce._save(
        root,
        ce.load(root)
        + [
            {
                "id": 2,
                "files": ["policy.py"],
                "status": "kept",
                "ended_ts": NOW + 130,
                "ts": NOW - H,
                "role": "shop",
            }
        ],
    )
    assert buyer.wake_reason(
        NOW + 180
    ) is None and "코드 수정 판정" in shop.wake_reason(NOW + 180)


def test_an_answer_to_its_own_question_wakes_the_role_that_asked_once(env):
    from kernel import questions as qs

    root, make = env
    shop, buyer = make("shop"), make("buyer")
    for o in (shop, buyer):
        o.wake_reason(NOW)
        o._save_state(dict(o._state(), last_wake_ts=NOW, next_wake_ts=NOW + 30 * 60))
    a = qs.add(root, "shop", "사과는 어디에서 빠지나", NOW + 5)["id"]
    qs.add(root, "buyer", "구매 오류는 어디서 나나", NOW + 6)
    assert shop.wake_reason(NOW + 60) is None  # asked, not answered yet: the operator's own next wake stands
    qs.close(root, a, "done", NOW + 90)
    r = shop.wake_reason(NOW + 120)
    assert r == f"네 조사 질문의 답 도착 #{a} (findings 도구로 전문을 봄)"
    assert buyer.wake_reason(NOW + 120) is None  # the other role's answer does not wake it
    shop.terra = Script({"done": {}})
    shop.wake(r, NOW + 120)
    assert shop.wake_reason(NOW + 180) is None  # seen: no second wake for the same answer
    # an answer that came before the operator's last wake (read on that wake's page) does not wake it again, even
    # when no verdict has moved its seen mark since
    c = qs.add(root, "shop", "깍두기는 왜 품절인가", NOW + 200)["id"]
    qs.close(root, c, "done", NOW + 210)
    shop._save_state(dict(shop._state(), last_wake_ts=NOW + 220))
    assert shop.wake_reason(NOW + 240) is None


def test_a_failed_answer_wakes_and_a_bad_stamp_neither_wakes_nor_moves_the_mark(env):
    from kernel import questions as qs

    root, make = env
    shop = make("shop")
    shop.wake_reason(NOW)
    shop._save_state(dict(shop._state(), last_wake_ts=NOW, next_wake_ts=NOW + 30 * 60))
    a = qs.add(root, "shop", "왜 거절됐나", NOW + 5)["id"]
    qs.close(root, a, "failed", NOW + 90)
    assert shop.wake_reason(NOW + 120) == f"네 조사 질문의 답 도착 #{a} (findings 도구로 전문을 봄)"
    shop.terra = Script({"done": {}})
    shop.wake(shop.wake_reason(NOW + 120), NOW + 120)
    # closed rows with no stamp, a stamp ahead of the clock, or an infinite one do not wake it now
    for i, ts in enumerate([None, NOW + 10**6, float("inf")]):
        q = qs.add(root, "shop", f"질문 {i}", NOW + 130)["id"]
        qs.close(root, q, "done", NOW + 140)
        rows = qs.load(root)
        for r in rows:
            if r["id"] == q:
                r["answered_ts"] = ts
        qs._save(root, rows)
    assert shop.wake_reason(NOW + 200) is None
    assert shop._seen_next is None or shop._seen_next <= NOW + 200
    # once the clock reaches the early stamp, that answer wakes it
    late = [r["id"] for r in qs.load(root) if r.get("answered_ts") == NOW + 10**6]
    assert f"네 조사 질문의 답 도착 #{late[0]} " in (shop.wake_reason(NOW + 10**6 + 1) or "")


# ------------------------------------------------------------ restock window
def _inv(root, **restock):
    os.makedirs(os.path.join(root, ".market"), exist_ok=True)
    json.dump(
        {"restock": restock}, open(os.path.join(root, ".market/inventory.json"), "w")
    )


def _metrics(root, rows):
    os.makedirs(os.path.join(root, ".r3"), exist_ok=True)
    with open(os.path.join(root, ".r3/metrics.jsonl"), "w") as f:
        f.write("".join(json.dumps(r) + "\n" for r in rows))


def _closed(ts, wait):
    return {
        "ts": ts,
        "hook": "restock",
        "decision": "restock_plan",
        "outcome": {"lines": 0, "qty": 0, "why": "window_closed", "opens_in_s": wait},
    }


def test_next_window_ts_sources(tmp_path):
    root = _root(tmp_path)
    assert op.next_window_ts(root, NOW) is None
    _inv(root, open=False, opens_at=NOW + 400, closes_at=NOW + 580)
    assert op.next_window_ts(root, NOW) == NOW + 400
    _inv(
        root, open=True, opens_at=NOW - 30, closes_at=NOW + 150
    )  # open now: the inventory says nothing ahead
    _metrics(
        root,
        [
            _closed(NOW - 600, 300),
            _closed(NOW - 540, 241),
            {"ts": NOW - 500, "hook": "seller", "outcome": {"opens_in_s": 5}},
        ],
    )
    assert (
        op.next_window_ts(root, NOW) is None
    )  # one opening in the past, no gap to carry it forward
    _metrics(
        root,
        [_closed(NOW - 3300, 300), _closed(NOW - 2100, 300), _closed(NOW - 2040, 240)],
    )
    assert (
        op.next_window_ts(root, NOW) == NOW - 1800 + 2 * 1200
    )  # openings NOW-3000, NOW-1800, gap 1200
    _metrics(root, [_closed(NOW - 100, 700)])
    assert op.next_window_ts(root, NOW) == NOW + 600
    Trajectory(os.path.join(root, "a2/trajectory.jsonl")).append(
        "kernel", "restock", why="window_closed", opens_in_s=200
    )
    t = op.next_window_ts(root, _rows(root)[-1]["ts"])
    assert t == pytest.approx(_rows(root)[-1]["ts"] + 200)
    _metrics(
        root, [_closed(NOW - 7 * H, 300), _closed(NOW - 7 * H + 1200, 300)]
    )  # too old
    _inv(root)
    assert op.next_window_ts(root, NOW) is None


def test_shop_wakes_before_the_window(env, monkeypatch):
    root, make = env
    shop = make("shop")
    shop.wake_reason(NOW - 1000)  # marks the verdict start
    _inv(root, open=False, opens_at=NOW + 200)
    assert "재입고 창" in shop.wake_reason(NOW) and "주기 점검" not in shop.wake_reason(
        NOW
    )
    shop._save_state(dict(shop._state(), last_wake_ts=NOW))
    assert shop.wake_reason(NOW + 60) is None  # woke for this window already
    _inv(root, open=False, opens_at=NOW + 1000)
    shop._save_state(dict(shop._state(), last_wake_ts=NOW - 5000))
    assert (
        shop.wake_reason(NOW) is None
    )  # an estimate within 30 min: wait for the lead, not the interval
    _inv(root, open=False, opens_at=NOW + 250)
    shop._save_state(dict(shop._state(), last_wake_ts=NOW - 300))
    assert shop.wake_reason(NOW) is None  # never more often than every 600 s
    monkeypatch.setenv("A2_SHOP_LEAD_S", "900")
    _inv(root, open=False, opens_at=NOW + 800)
    shop._save_state(dict(shop._state(), last_wake_ts=NOW - 700))
    assert "재입고 창" in shop.wake_reason(NOW)
    _inv(root, open=False, opens_at=NOW + 3 * H)  # too far: the interval decides
    shop._save_state(dict(shop._state(), last_wake_ts=NOW - op.ROLE_INTERVAL_S))
    assert "주기 점검" in shop.wake_reason(NOW)
    _inv(root)  # no estimate at all: the interval
    assert "주기 점검" in shop.wake_reason(NOW)
    buyer = make("buyer")
    _inv(root, open=False, opens_at=NOW + 200)
    assert "주기 점검" in buyer.wake_reason(NOW) and "재입고" not in buyer.wake_reason(
        NOW
    )


# ------------------------------------------------------------ busy lock
def test_busy_lock(tmp_path):
    root = _root(tmp_path)
    assert op.claim_busy(root, "shop", NOW)
    assert op.busy_other(root, "buyer", NOW + 10)["role"] == "shop"
    assert not op.claim_busy(root, "buyer", NOW + 10)
    assert op.claim_busy(root, "shop", NOW + 20)  # the same role may take it again
    assert (
        op.busy_other(root, "buyer", NOW + 20 + op.BUSY_STALE_S + 1) is None
    )  # stale by age
    op.release_busy(root, "buyer")  # not the holder: nothing happens
    assert os.path.exists(os.path.join(root, op.BUSY))
    op.release_busy(root, "shop")
    assert not os.path.exists(os.path.join(root, op.BUSY))
    json.dump(
        {"role": "shop", "pid": 2**22 + 12345, "ts": NOW},
        open(os.path.join(root, op.BUSY), "w"),
    )
    assert op.busy_other(root, "buyer", NOW) is None and op.claim_busy(
        root, "buyer", NOW
    )  # dead pid: stale


def test_main_role_argument(env, monkeypatch, tmp_path):
    root, _ = env
    with pytest.raises(SystemExit):
        op.main(["--root", root, "--role", "seller", "--dry"])
    monkeypatch.setenv("A2_OPERATOR_ROLE", "buyer")
    monkeypatch.setattr(op.versions, "check_breakage", lambda *a, **k: {})  # no harness library here
    monkeypatch.setattr(op.Operator, "wake_reason", lambda self, now=None: "x")
    seen = {}
    monkeypatch.setattr(
        op.Operator,
        "wake",
        lambda self, reason, now=None: seen.update(role=self.role) or {},
    )
    assert (
        op.main(
            [
                "--root",
                root,
                "--dry",
                "--once",
                "--harness",
                str(tmp_path / "noharness"),
            ]
        )
        == 0
    )
    assert seen["role"] == "buyer" and not os.path.exists(os.path.join(root, op.BUSY))
    seen.clear()
    with open(os.path.join(root, op.BUSY), "w") as f:
        json.dump(
            {"role": "shop", "pid": os.getpid(), "ts": __import__("time").time()}, f
        )
    assert (
        op.main(
            [
                "--root",
                root,
                "--dry",
                "--once",
                "--harness",
                str(tmp_path / "noharness"),
            ]
        )
        == 0
    )
    assert seen == {}  # the shop's wake runs: the buyer waits


# ------------------------------------------------------------ tool_search
def test_tool_search(tmp_path):
    root = str(tmp_path)
    os.makedirs(os.path.join(root, "a2"))
    json.dump(
        {"hidden": ["shops"], "desc": {}, "recommend": {"buyer": ["stockouts"]}},
        open(os.path.join(root, au.TOOL_STATE), "w"),
    )
    spec = {
        "name": "q_attempts",
        "description": "구매 시도 결과 수",
        "source": "trajectory",
        "hours": 2,
        "agg": [{"fn": "count", "as": "n"}],
    }
    assert made_tools.add(root, spec, NOW, role="buyer")["ok"]
    out = tools.call("tool_search", "operator", root, query="재고 없음")
    assert (
        out.startswith("도구 찾기 '재고 없음': ")
        and "- stockouts(hours) 추천: shop, buyer" in out
    )
    out = tools.call("tool_search", "operator", root, query="구매 시도")
    assert "q_attempts(hours) 추천: buyer, 만든 도구(만든 쪽 buyer)" in out
    assert "shops(" not in tools.call(
        "tool_search", "operator", root, query="shops"
    )  # hidden
    assert "harness_read" not in tools.call(
        "tool_search", "operator", root, query="harness"
    )
    assert tools.call("tool_search", "operator", root, query="zzqq").startswith(
        "도구 찾기 'zzqq': 0개"
    )
    assert tools.call("tool_search", "operator", root, query=" ").startswith(
        "인자 오류"
    )
    out = tools.call("tool_search", "operator", root, query="code")
    assert (
        len([ln for ln in out.splitlines() if ln.startswith("- ")]) <= tools.SEARCH_MAX
    )


def test_every_registry_tool_is_assigned():
    named = set(tools.RECOMMENDED["shop"]) | set(tools.RECOMMENDED["buyer"])
    assert named <= set(tools.TOOLS)
    assert set(tools.TOOLS) - named == {
        "trajectory",
        "versions",
        "harness_read",
        "replay_probe",
        "analyze",  # the investigator sees every tool; the role operators find it by name or tool_search
        "agents",  # named in the operators' agents section when they have agents
    }
    for core in au.CORE_TOOLS:
        assert core in tools.RECOMMENDED["shop"] and core in tools.RECOMMENDED["buyer"]


# ------------------------------------------------------------ audit per role
def _tool(ts, name, role, recommended=True, **kw):
    return {
        "ts": ts,
        "agent": "operator",
        "kind": "tool",
        "name": name,
        "args": "{}",
        "obs_chars": 10,
        "role": role,
        "recommended": recommended,
        **kw,
    }


def _wake(ts, role, status="done", **res):
    return {
        "ts": ts,
        "agent": "operator",
        "kind": "operator_wake",
        "status": status,
        "role": role,
        "result": dict(res, calls=1, prompt_chars=100),
    }


def _traj(root, rows):
    with open(os.path.join(root, "a2/trajectory.jsonl"), "w", encoding="utf-8") as f:
        f.write("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))


def test_audit_report_per_role(tmp_path):
    root = str(tmp_path)
    os.makedirs(os.path.join(root, "a2"))
    _traj(
        root,
        [
            _tool(NOW - 3 * H, "stockouts", "buyer", recommended=False),
            _tool(NOW - 3 * H, "tool_search", "buyer", query="재고", hits=2),
            _tool(NOW - 3 * H, "tool_search", "buyer", query="없는것", hits=0),
            _wake(NOW - 3 * H, "buyer"),
            _tool(NOW - 2 * H, "board", "shop"),
            _wake(NOW - 2 * H, "shop", status="model_error"),
        ],
    )
    ex.save(
        root,
        [
            {
                "id": 1,
                "section": "buyer.defaults.food",
                "key": "max_pay_frac",
                "started_ts": NOW - H,
                "status": "running",
            }
        ],
    )
    b = audit.report(root, NOW, role="buyer", listed=["board", "stockouts"])
    s = audit.report(root, NOW, role="shop")
    every = audit.report(root, NOW)
    assert b["wakes"] == 1 and s["wakes"] == 1 and every["wakes"] == 2
    assert b["tools"]["stockouts"]["outside"] == 1 and b["outside_calls"] == 1
    assert (
        b["searches"] == 2
        and b["search_zero"] == 1
        and b["search_zero_queries"] == ["없는것"]
    )
    assert b["unused_tools"] == ["board"] and "outside" not in every["tools"]["board"]
    assert s["model_errors"] == 1 and b["model_errors"] == 0
    assert b["experiments"]["running"] == 1 and s["experiments"]["running"] == 0
    assert (
        set(b["experiments"]["by_group"]) == {"buyer"}
        and "buyer" not in s["experiments"]["by_group"]
    )
    assert any(
        "tool_search 결과 0개 1회" in ln for ln in audit.lines(root, NOW, role="buyer")
    )
    rows, srch = audit.role_tool_table(root, NOW, "buyer", ["board", "tool_search"])
    assert rows[0] == {
        "name": "tool_search",
        "calls": 2,
        "outside": 0,
        "recommended": True,
        "errors": 0,
        "cut": 0,
        "uncited": 0,
    }
    assert {r["name"]: r["outside"] for r in rows}["stockouts"] == 1
    assert srch == {"searches": 2, "search_zero": 1, "queries": ["없는것"]}


# ------------------------------------------------------------ auditor per role
def test_auditor_recommend_and_unrecommend(tmp_path):
    root = str(tmp_path)
    os.makedirs(os.path.join(root, "a2"))
    json.dump(
        {"hidden": ["shops"], "desc": {"board": "새 설명"}},
        open(os.path.join(root, au.TOOL_STATE), "w"),
    )
    listed = list(tools.TOOLS)
    ask = {
        "recommend": {"buyer": ["stockouts", "board", "nope"], "seller": ["board"]},
        "unrecommend": {"shop": ["inventory", "policy", "catalog"]},
    }
    r = au._apply_tools(
        root,
        NOW,
        ask,
        listed,
        (),
        Trajectory(os.path.join(root, "a2/t.jsonl")),
        roles=au.ROLES,
    )
    assert [(x["op"], x["role"], x["name"]) for x in r["applied"]] == [
        ("recommend", "buyer", "stockouts"),
        ("unrecommend", "shop", "inventory"),
    ]
    why = {(x["role"], x["name"]): x["why"] for x in r["refused"]}
    assert (
        why[("buyer", "board")] == "이미 추천 목록에 있음"
        and why[("buyer", "nope")] == "목록에 없는 도구"
    )
    assert (
        why[("shop", "policy")].startswith("핵심")
        and why[("shop", "catalog")] == "추천 목록에 없는 도구"
    )
    assert why[("seller", "board")] == "역할은 shop 또는 buyer"
    assert "stockouts" in au.recommended(
        root, "buyer"
    ) and "inventory" not in au.recommended(root, "shop")
    assert au.tool_state(root) == {"hidden": ["shops"], "desc": {"board": "새 설명"}}
    r = au._apply_tools(
        root,
        NOW,
        {
            "recommend": {"shop": ["inventory"]},
            "unrecommend": {"buyer": ["stockouts"]},
            "hide": ["tool_search"],
        },
        listed,
        (),
        None,
        roles=au.ROLES,
    )
    assert len(r["applied"]) == 2 and r["refused"][0]["name"] == "tool_search"
    assert au.recommended(root, "shop") == list(tools.RECOMMENDED["shop"])
    assert au.recommended(root, "buyer") == list(tools.RECOMMENDED["buyer"])
    r = au._apply_tools(
        root, NOW, {"recommend": {"buyer": ["stockouts"]}}, listed, (), None
    )  # single: ignored
    assert r == {"applied": [], "refused": []}


def test_auditor_guidance_is_written_and_judged_per_role(tmp_path):
    root = str(tmp_path)
    os.makedirs(os.path.join(root, "a2"))
    traj = Trajectory(os.path.join(root, "a2/audit.jsonl"))
    st = au._state(root)
    g = au._apply_guidance(root, NOW, "가게: 재고 절을 먼저 읽음", (), st, role="shop")
    assert g["changed"] and au.guidance(root, "shop") == "가게: 재고 절을 먼저 읽음"
    assert au.guidance(root) == "" and au.guidance(root, "buyer") == ""
    assert st["history_shop"][0]["status"] == "active" and st["history"] == []
    # judged on that role's wakes only: the shop got worse, the buyer did not
    st["history_shop"] = [
        {
            "id": 1,
            "ts": NOW - 10 * H,
            "text": "새",
            "prev_text": "옛 가게",
            "status": "active",
            "before": {"mistakes_per_wake": 0, "idle_share": 0, "wakes": 5},
        }
    ]
    st["history_buyer"] = [
        {
            "id": 1,
            "ts": NOW - 10 * H,
            "text": "구매 새",
            "prev_text": "옛 구매",
            "status": "active",
            "before": {"mistakes_per_wake": 0, "idle_share": 1, "wakes": 5},
        }
    ]
    au._atomic_json(os.path.join(root, au.STATE), st)
    for r in ("shop", "buyer"):
        with open(os.path.join(root, au.guidance_rel(r)), "w", encoding="utf-8") as f:
            f.write("새" if r == "shop" else "구매 새")
    _traj(
        root,
        [_wake(NOW - (9 - i) * H, "shop", status="model_error") for i in range(4)]
        + [_wake(NOW - (9 - i) * H, "buyer") for i in range(4)],
    )
    assert au.judge_guidance(root, NOW, traj, role="shop")["status"] == "reverted"
    assert au.judge_guidance(root, NOW, traj, role="buyer")["status"] == "kept"
    assert (
        au.guidance(root, "shop") == "옛 가게"
        and au.guidance(root, "buyer") == "구매 새"
    )
    rows = [
        json.loads(x)
        for x in open(os.path.join(root, "a2/audit.jsonl"), encoding="utf-8")
    ]
    assert [(r["role"], r["action"]) for r in rows] == [
        ("shop", "reverted"),
        ("buyer", "kept"),
    ]


def test_auditor_run_with_roles(tmp_path):
    root = str(tmp_path)
    os.makedirs(os.path.join(root, "a2"))
    _traj(
        root,
        [
            _tool(NOW - H, "stockouts", "buyer", recommended=False),
            _wake(NOW - H, "buyer"),
            _tool(NOW - H, "tool_search", "shop", query="없음", hits=0),
            _wake(NOW - H, "shop"),
        ],
    )
    ans = {
        "findings": ["[buyer] 목록 밖 stockouts 1회"],
        "guidance": {"buyer": "구매: 재고 없음 도구를 씀", "shop": None},
        "tools": {"recommend": {"buyer": ["stockouts"]}},
        "note": "n",
    }
    terra = Script(ans)
    traj = Trajectory(os.path.join(root, "a2/trajectory.jsonl"))
    out = au.run(root, terra, traj, NOW, listed=list(tools.TOOLS), roles=au.ROLES)
    p = terra.prompts[0]
    assert "구매 운영자(buyer) 도구 표" in p and "stockouts | 1 | 1 | 아니오" in p
    assert (
        "가게 운영자(shop) 도구 표" in p
        and "결과 0개 1회: 없음" in p
        and '"recommend"' in p
    )
    assert (
        out["status"] == "done"
        and out["guidance_changed"]
        and out["guidance"]["buyer"]["changed"]
    )
    assert (
        au.guidance(root, "buyer") == "구매: 재고 없음 도구를 씀"
        and au.guidance(root) == ""
    )
    assert out["tools"]["applied"] == [
        {"op": "recommend", "role": "buyer", "name": "stockouts"}
    ]
    nums = au._state(root)["runs"][-1]["nums"]["roles"]
    assert nums["buyer"]["outside_calls"] == 1 and nums["shop"]["search_zero"] == 1
    with open(os.path.join(root, "a2/trajectory.jsonl"), "a", encoding="utf-8") as f:  # a wake since that audit
        f.write(json.dumps(_wake(NOW + 6 * H, "shop")) + "\n")
    terra = Script({"guidance": "역할 없는 글"})
    out = au.run(
        root, terra, traj, NOW + 7 * H, listed=list(tools.TOOLS), roles=au.ROLES
    )
    assert out["guidance"]["refused"] and not out["guidance_changed"]


# ------------------------------------------------------------ memory
def test_memfeed_tags_memories_with_the_role(tmp_path):
    root = str(tmp_path)
    os.makedirs(os.path.join(root, "a2"))
    _traj(
        root,
        [
            {
                "ts": NOW - 60,
                "agent": "operator",
                "kind": "experiment_end",
                "id": 1,
                "section": "buyer.defaults.food",
                "key": "max_pay_frac",
                "old": 0.9,
                "new": 0.8,
                "status": "kept",
                "verdict": {},
                "role": "buyer",
            },
            {
                "ts": NOW - 50,
                "agent": "operator",
                "kind": "experiment_end",
                "id": 2,
                "section": "defaults.food",
                "key": "price_mult",
                "old": 1,
                "new": 0.9,
                "status": "kept",
                "verdict": {},
            },
        ],
    )
    sent = []
    r = memfeed.run_once(
        root,
        "sock",
        harness_dir=str(tmp_path / "noh"),
        now=NOW,
        add=lambda sock, agent, text, infer, metadata: (
            sent.append((agent, metadata)) or True
        ),
    )
    assert r["ok"] and sent == [
        ("operator", {"source": "memfeed", "n": 1, "role": "buyer"}),
        ("operator", {"source": "memfeed", "n": 1}),
    ]
