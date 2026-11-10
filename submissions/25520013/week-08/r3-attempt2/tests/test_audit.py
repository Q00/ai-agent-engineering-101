"""Process audit (kernel/audit.py) on a hand-written trajectory and ledger. Fixed clock values."""

import json
import os

import pytest
from kernel import audit

H = 3600
NOW = 1_794_000_000.0


def _root(tmp_path, rows=(), ledger=None):
    root = str(tmp_path / "root")
    os.makedirs(os.path.join(root, "a2"), exist_ok=True)
    with open(os.path.join(root, "a2", "trajectory.jsonl"), "w", encoding="utf-8") as f:
        for r in rows:
            f.write(
                (r if isinstance(r, str) else json.dumps(r, ensure_ascii=False)) + "\n"
            )
    if ledger is not None:
        with open(
            os.path.join(root, "a2", "experiments.json"), "w", encoding="utf-8"
        ) as f:
            f.write(ledger if isinstance(ledger, str) else json.dumps(ledger))
    return root


def tool(ts, name, args=None, obs=100):
    return {
        "ts": ts,
        "agent": "operator",
        "kind": "tool",
        "name": name,
        "args": json.dumps(args or {}),
        "obs_chars": obs,
    }


def wake(ts, status="done", calls=2, chars=1000, **result):
    return {
        "ts": ts,
        "agent": "operator",
        "kind": "operator_wake",
        "reason": "주기 점검",
        "steps": calls,
        "status": status,
        "result": dict(result, calls=calls, prompt_chars=chars),
    }


def exp(eid, section, key, started, status="running", ended=None):
    e = {
        "id": eid,
        "mode": "sequential",
        "section": section,
        "key": key,
        "old": 1,
        "new": 2,
        "started_ts": started,
        "status": status,
    }
    if ended is not None:
        e["ended_ts"] = ended
    return e


OK = {"ok": True, "experiment": 1, "section": "defaults.goods", "key": "price_mult"}
BAD = {"ok": False, "error": "running experiment blocks this scope: #1 kind:goods"}


def test_group_of():
    assert audit.group_of("buyer.defaults.goods", "stock_target") == "buyer"
    assert audit.group_of("defaults.goods", "stock_target") == "restock"
    assert audit.group_of("shop", "qty_per_buy") == "restock"
    assert audit.group_of("shop", "liquidation_line") == "shop"
    assert audit.group_of("defaults.goods", "price_mult") == "sale"
    assert audit.group_of(None, 3) == "sale"


def test_counts_repeats_tools_and_edits(tmp_path):
    rows = [
        tool(NOW - 5 * H, "policy"),
        tool(NOW - 5 * H, "policy"),  # same name and args in one wake: 1 repeat
        tool(NOW - 5 * H, "policy", {"section": "shop"}),  # other args: no repeat
        tool(NOW - 5 * H, "sales", obs=300),
        wake(NOW - 5 * H, calls=3, chars=3000, policy=[OK, BAD, "x"]),
        tool(NOW - 4 * H, "policy"),  # same call in the next wake: no repeat
        wake(NOW - 4 * H, calls=2, chars=1000),
        {
            "ts": NOW - 3 * H,
            "agent": "operator",
            "kind": "llm",
            "prompt_bytes": 9,
            "status": "ok",
        },
        wake(
            NOW - 2 * H, status="model_error", calls=1, chars=500, error="ModelError: x"
        ),
        wake(NOW - 1 * H, status="no_done", calls=6, chars=500),
    ]
    rep = audit.report(
        _root(tmp_path, rows), NOW, listed=["policy", "sales", "guide", "board"]
    )
    assert rep["wakes"] == 4
    assert rep["calls"] == 12 and rep["prompt_chars"] == 5000
    assert rep["calls_per_wake"] == 3.0 and rep["chars_per_call"] == 417
    assert rep["tools"]["policy"] == {
        "uses": 4,
        "wakes": 2,
        "obs_chars_mean": 100,
        "obs_chars_max": 100,
        "repeats": 1,
    }
    assert rep["tools"]["sales"]["obs_chars_mean"] == 300
    assert rep["repeat_calls"] == 1
    assert rep["unused_tools"] == ["board", "guide"]
    assert (
        rep["edits"]["asked"] == 3
        and rep["edits"]["ok"] == 1
        and rep["edits"]["refused"] == 2
    )
    assert rep["edits"]["reasons"] == {BAD["error"][:40]: 1, "not an object": 1}
    assert rep["model_errors"] == 1 and rep["no_done"] == 1
    assert rep["idle_wakes"] == 1  # only the done wake without policy
    s = rep["score"]
    assert s == {
        "mistakes_per_wake": 1.25,
        "idle_share": 0.25,
        "hit_rate": None,
        "wakes": 4,
    }
    assert audit.score(rep) == s


def test_non_compact_wakes_ignored_and_their_tools_not_counted(tmp_path):
    rows = [
        tool(NOW - 3 * H, "policy"),
        {
            "ts": NOW - 3 * H,
            "agent": "operator",
            "kind": "operator_wake",
            "status": "done",
            "result": {"note": "x"},
        },
        tool(NOW - 2 * H, "sales"),
        wake(NOW - 2 * H),
    ]
    rep = audit.report(_root(tmp_path, rows), NOW)
    assert rep["wakes"] == 1 and list(rep["tools"]) == ["sales"]
    assert rep["unused_tools"] == []


def test_idle_definition(tmp_path):
    rows = [
        wake(NOW - 6 * H, policy=[BAD]),  # idle: only a refused edit
        wake(NOW - 5 * H, policy=[OK]),
        wake(NOW - 4 * H, forum={"queued": 1, "result": []}),
        wake(NOW - 3 * H, forum=[{"post": "x"}]),
        wake(NOW - 2 * H, forum={"queued": 0}),  # idle
        wake(NOW - 1 * H, new_tool={"ok": True, "name": "t"}),
        wake(NOW - 0.5 * H, new_tool={"ok": False}),  # idle
        wake(NOW - 0.2 * H, status="model_error"),  # not done: not idle
    ]
    rep = audit.report(_root(tmp_path, rows), NOW)
    assert rep["wakes"] == 8 and rep["idle_wakes"] == 3


def test_crashes(tmp_path):
    rows = [
        {
            "ts": NOW - H,
            "agent": "operator",
            "kind": "operator_poll",
            "status": "crash",
            "error": "KeyError",
        },
        {
            "ts": NOW - H,
            "agent": "operator",
            "kind": "operator_wake",
            "reason": "r",
            "status": "crash",
            "error": "ValueError",
        },
        {
            "ts": NOW - 30 * H,
            "agent": "operator",
            "kind": "operator_poll",
            "status": "crash",
            "error": "old",
        },
    ]
    rep = audit.report(_root(tmp_path, rows), NOW)
    assert rep["crashes"] == 2 and rep["wakes"] == 0


def test_experiments_hit_and_untouched_groups(tmp_path):
    ledger = [
        exp(1, "defaults.goods", "price_mult", NOW - 10 * H, "kept", NOW - 6 * H),
        exp(2, "defaults.goods", "stock_target", NOW - 9 * H, "reverted", NOW - 5 * H),
        exp(3, "defaults.goods", "min_margin", NOW - 8 * H, "voided", NOW - 4 * H),
        exp(4, "defaults.goods", "price_mult", NOW - 3 * H, "adopted", NOW - 1 * H),
        exp(
            5, "buyer.defaults.goods", "max_price", NOW - 40 * H
        ),  # running, started before the window
        exp(
            6, "shop", "liquidation_line", NOW - 50 * H, "dropped", NOW - 45 * H
        ),  # ended before the window
        "junk",
    ]
    rep = audit.report(_root(tmp_path, [wake(NOW - H)], ledger), NOW)
    x = rep["experiments"]
    assert x["started"] == 4 and x["running"] == 1
    assert (x["kept"], x["reverted"], x["voided"], x["adopted"], x["dropped"]) == (
        1,
        1,
        1,
        1,
        0,
    )
    assert x["by_group"] == {"sale": 3, "restock": 1, "buyer": 0, "shop": 0}
    assert rep["untouched_groups"] == ["shop"]  # buyer has a running experiment
    assert rep["hit"] == {"ended": 3, "good": 2}  # voided is not a verdict
    assert rep["score"]["hit_rate"] == 0.67


def test_stale_verdicts(tmp_path):
    ledger = [
        exp(
            1, "defaults.goods", "price_mult", NOW - 10 * H, "reverted", NOW - 6 * H
        ),  # sale: followed by #4
        exp(
            2, "defaults.goods", "stock_target", NOW - 10 * H, "kept", NOW - 6 * H
        ),  # restock: stale
        exp(
            3, "shop", "liquidation_line", NOW - 10 * H, "dropped", NOW - 1.5 * H
        ),  # 1 wake after: not stale
        exp(4, "defaults.food", "min_margin", NOW - 5 * H, "running"),
        exp(
            5, "buyer.defaults.goods", "max_price", NOW - 10 * H, "kept", NOW - 0.5 * H
        ),  # no wake after
    ]
    rows = [wake(NOW - 5.5 * H), wake(NOW - 3 * H), wake(NOW - H)]
    rep = audit.report(_root(tmp_path, rows, ledger), NOW)
    assert rep["stale_verdicts"] == [2]


def test_hours_and_since_windows(tmp_path):
    rows = [wake(NOW - 30 * H), wake(NOW - 10 * H), wake(NOW - H), wake(NOW + H)]
    root = _root(tmp_path, rows)
    assert audit.report(root, NOW)["wakes"] == 2
    assert audit.report(root, NOW, hours=48)["wakes"] == 3
    rep = audit.report(root, NOW, hours=48, since=NOW - 5 * H)
    assert rep["wakes"] == 1 and rep["from_ts"] == NOW - 5 * H and rep["window_h"] == 5
    assert audit.report(root, NOW)["window_h"] == 24


def test_missing_and_corrupt_files(tmp_path):
    rep = audit.report(str(tmp_path / "nothing"), NOW, listed=["policy"])
    assert rep["wakes"] == 0 and rep["calls"] == 0 and rep["unused_tools"] == ["policy"]
    assert rep["score"] == {
        "mistakes_per_wake": 0.0,
        "idle_share": 0.0,
        "hit_rate": None,
        "wakes": 0,
    }
    rows = [
        "{not json",
        "[1, 2]",
        {
            "ts": "yesterday",
            "agent": "operator",
            "kind": "operator_wake",
            "status": "done",
            "result": {"calls": 1},
        },
        {
            "ts": NOW - H,
            "agent": "operator",
            "kind": "operator_wake",
            "status": "done",
            "result": "oops",
        },
        {
            "ts": NOW - H,
            "agent": "operator",
            "kind": "tool",
            "name": 5,
            "args": {"a": 1},
            "obs_chars": "x",
        },
        {
            "ts": NOW - H,
            "agent": "operator",
            "kind": "operator_wake",
            "status": "done",
            "result": {
                "calls": 1,
                "prompt_chars": "x",
                "policy": "nope",
                "forum": 3,
                "new_tool": [],
            },
        },
    ]
    root = _root(tmp_path, rows, ledger="{broken")
    rep = audit.report(root, NOW)
    assert rep["wakes"] == 1 and rep["prompt_chars"] == 0 and rep["idle_wakes"] == 1
    assert rep["tools"]["?"]["uses"] == 1 and rep["tools"]["?"]["obs_chars_mean"] == 0
    assert rep["experiments"]["started"] == 0
    root2 = _root(
        tmp_path / "b",
        [wake(NOW - H)],
        ledger=[
            {
                "id": 1,
                "status": "kept",
                "ended_ts": "x",
                "started_ts": None,
                "section": [1],
            }
        ],
    )
    assert audit.report(root2, NOW)["hit"] == {"ended": 0, "good": 0}
    assert len(audit.lines(root, NOW)) >= 1 and audit.tool_table(root, NOW)


def test_lines_zero_wakes(tmp_path):
    assert audit.lines(_root(tmp_path, [wake(NOW - 30 * H)]), NOW) == [
        "[감사] 최근 24시간 점검 기록 없음"
    ]


def test_lines_head_and_only_non_zero_findings(tmp_path):
    rows = [tool(NOW - H, "policy"), wake(NOW - H, policy=[OK])]
    ledger = [
        exp(g, s, k, NOW - H)
        for g, (s, k) in enumerate(
            [
                ("defaults.goods", "price_mult"),
                ("defaults.goods", "stock_target"),
                ("buyer.defaults.goods", "max_price"),
                ("shop", "liquidation_line"),
            ]
        )
    ]
    out = audit.lines(_root(tmp_path, rows, ledger), NOW, listed=["policy"])
    assert out == [
        "[감사] 최근 24시간 점검 1회, 호출 2회, 점검당 실수 0.0, 빈 점검 0회"
    ]


@pytest.mark.parametrize("line_chars", [200, 1000])
def test_lines_findings_and_700_char_cap(tmp_path, monkeypatch, line_chars):
    monkeypatch.setattr(
        audit, "LINE_CHARS", line_chars
    )  # 1000: only the block cap holds the size
    listed = [f"tool_with_a_long_name_{i:02d}" for i in range(30)]
    name = "repeated_tool_" + "p" * 400
    rows = []
    for i in range(5):
        rows += [tool(NOW - (6 - i) * H, name)] * 3
        rows.append(wake(NOW - (6 - i) * H, policy=[dict(BAD, error="x" * 300)] * 4))
    rows.append(wake(NOW - 0.5 * H, status="no_done"))
    ledger = [
        exp(i, "defaults.goods", "stock_target", NOW - 10 * H, "kept", NOW - 7 * H)
        for i in range(1, 80)
    ]
    out = audit.lines(_root(tmp_path, rows, ledger), NOW, listed=listed)
    text = "\n".join(out)
    assert len(text) <= 700 and len(out) <= 6
    assert out[0].startswith(
        "[감사] 최근 24시간 점검 6회, 호출 12회, 점검당 실수 5.2, 빈 점검 5회"
    )
    assert any("판매 값" in x and "가게 값" in x for x in out)
    assert any("#1, #2, #3" in x and "외 69건" in x for x in out)
    assert "—" not in text and not any(x.endswith(".") for x in out)


def test_lines_each_finding(tmp_path):
    rows = [
        tool(NOW - 2 * H, "policy"),
        tool(NOW - 2 * H, "policy"),
        wake(NOW - 2 * H, policy=[BAD]),
        wake(NOW - H, status="model_error"),
    ]
    out = audit.lines(_root(tmp_path, rows), NOW, listed=["policy", "guide"])
    assert (
        out[0] == "[감사] 최근 24시간 점검 2회, 호출 4회, 점검당 실수 1.5, 빈 점검 1회"
    )
    body = "\n".join(out[1:])
    assert "판매 값, 재고 값, 구매 값, 가게 값" in body
    assert f"정책 수정 1건 중 1건 거절됨, 최다 사유 '{BAD['error'][:40]}' 1건임" in body
    assert "반복 호출 1회, 최다 policy 1회임" in body
    assert "모델 오류 1회임" in body
    assert "안 쓴 도구 1개: guide임" in body


def test_tool_table(tmp_path):
    rows = [
        tool(NOW - H, "sales", obs=50),
        tool(NOW - H, "sales", obs=150),
        tool(NOW - H, "policy"),
        tool(NOW - H, "secret_tool"),
        wake(NOW - H),
    ]
    t = audit.tool_table(
        _root(tmp_path, rows), NOW, listed=["policy", "sales", "guide"]
    )
    assert [r["name"] for r in t] == ["sales", "policy", "secret_tool", "guide"]
    assert t[0] == {
        "name": "sales",
        "uses": 2,
        "wakes": 1,
        "obs_chars_mean": 100,
        "repeats": 1,
        "listed": True,
    }
    assert t[2]["listed"] is False and t[3] == {
        "name": "guide",
        "uses": 0,
        "wakes": 0,
        "obs_chars_mean": 0,
        "repeats": 0,
        "listed": True,
    }


def test_deterministic(tmp_path):
    rows = [
        tool(NOW - H, "b"),
        tool(NOW - H, "a"),
        tool(NOW - H, "b"),
        tool(NOW - H, "a"),
        wake(NOW - H),
    ]
    root = _root(tmp_path, rows)
    assert audit.report(root, NOW) == audit.report(root, NOW)
    assert "최다 a 1회임" in "\n".join(audit.lines(root, NOW))


@pytest.mark.parametrize("hours", [6, 1.5])
def test_head_shows_hours(tmp_path, hours):
    out = audit.lines(_root(tmp_path, [wake(NOW - 0.5 * H)]), NOW, hours=hours)
    assert out[0].startswith(f"[감사] 최근 {hours}시간 점검 1회")
