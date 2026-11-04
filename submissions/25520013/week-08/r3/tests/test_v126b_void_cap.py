"""v1.26b (2026-11-05): an experiment stopped by a loss cap that the current cap would not trip (the 2 % cap before
v1.25c stopped e0010-e0015 at noise level) is shown to the proposer as void_old_cap in EXPERIMENTS and MEMORY, while
recall itself (memcheck reads it) is unchanged; a hold keeps the proposer's reason in the cycle log."""

import json

import pytest
from icrl import loop

from tests.test_icrl_lanes import PREF, env, lane_of, rows  # noqa: F401
from tests.test_icrl_loop import NOW0

OLD = "loss_cap shortfall 553356 > 0.02 x 19931312"  # e0011 at 01:26 KST: 2.8 % of the balance
NEW = "loss_cap shortfall 4000000 > 0.15 x 20000000"  # 20 %: the current cap trips as well
BUYER_ARM = "buyer.max_shops|down"


def _ends(ctx, specs):
    with open(ctx.f("experiments.jsonl"), "a", encoding="utf-8") as fh:
        for i, lane, arm, verdict, reason in specs:
            path, _, d = arm.partition("|")
            row = {
                "event": "end",
                "id": i,
                "lane": lane,
                "arm": arm,
                "protocol": "switchback",
                "edits": [{"path": path, "current": 1, "value": 2, "dir": d}],
                "verdict": verdict,
                "reason": reason,
                "stats": None,
            }
            fh.write(json.dumps(row) + "\n")


@pytest.mark.parametrize(
    "reason,void",
    [
        (OLD, True),
        (
            "loss_cap shortfall 3000000 > 0.02 x 20000000",
            True,
        ),  # exactly 15 %: not over the current cap
        (NEW, False),
        ("harm t=-5.1 over 3 pairs", False),
        ("loss_cap shortfall x > 0.02 x 1", False),
        ("loss_cap shortfall 5", False),
        (None, False),
    ],
)
def test_superseded_cap(reason, void):
    assert loop.superseded_cap({"reason": reason}) is void


def test_prompt_memory_marks_void_arms_and_leaves_recall_alone(env):
    ctx, arm = env["ctx"], PREF["price"]
    _ends(
        ctx,
        [
            ("e0011", "price", arm, "reject", OLD),
            ("e0012", "buyer", BUYER_ARM, "reject", NEW),
        ],
    )
    rec = {
        "edges": [
            {"exp": arm, "verdict": "reject"},
            {"exp": BUYER_ARM, "verdict": "reject"},
            {"buyer_cat": "x", "count": 3},
        ],
        "lessons": ["t11", "t12"],
        "lesson_rows": [{"exp": "e0011"}, {"exp": "e0012"}],
        "exp": {arm: "reject", BUYER_ARM: "reject"},
        "policy": {},
    }
    before = json.dumps(rec)
    out = loop.prompt_memory(ctx, rec)
    assert json.dumps(rec) == before  # memcheck reads rec as recalled
    assert [e.get("verdict") for e in out["edges"]] == [loop.VOID_CAP, "reject", None]
    assert out["lessons"][0].startswith("t11 [void:") and out["lessons"][1] == "t12"
    assert out["exp"] == rec["exp"]
    _ends(
        ctx, [("e0020", "price", "retest|" + arm, "keep_a", None)]
    )  # a real verdict on the arm since
    assert [e.get("verdict") for e in loop.prompt_memory(ctx, rec)["edges"]][:2] == [
        "reject",
        "reject",
    ]


def test_the_price_prompt_shows_the_void_row(env):
    ctx, arm = env["ctx"], PREF["price"]
    _ends(ctx, [("e0011", "price", arm, "reject", OLD)])
    assert [r["verdict"] for r in loop.exp_table(ctx, "price")] == [loop.VOID_CAP]
    assert loop.exp_table(ctx, "buyer") == []
    with open(ctx.f("lessons.jsonl"), "a", encoding="utf-8") as fh:
        fh.write(
            json.dumps(
                {
                    "ts": NOW0 - 3600,
                    "exp": "e0011",
                    "lane": "price",
                    "arm": arm,
                    "path": arm.split("|")[0],
                    "dir": "up",
                    "verdict": "reject",
                    "text": f"{arm} 1->2: reject (switchback, stats null, {OLD})",
                }
            )
            + "\n"
        )
    loop.step(NOW0, ctx=ctx)
    p = [p for p in env["prop"].prompts if lane_of(p) == "price"][0]
    exps = json.loads(p.split("\nEXPERIMENTS ", 1)[1].split("\n", 1)[0])
    mem = json.loads(p.split("\nMEMORY ", 1)[1].split("\n", 1)[0])
    assert exps[-1]["verdict"] == loop.VOID_CAP
    assert any("[void:" in t for t in mem["lessons"]), mem


def test_a_hold_keeps_its_reason_in_the_cycle_log(env):
    env["prop"].replies = [
        json.dumps({"action": "hold", "edits": [], "why": "no arm looks better"})
    ]
    r = loop.step(NOW0, ctx=env["ctx"])
    assert {c.get("why") for c in r["cycles"].values()} == {"no arm looks better"}
    cyc = [x for x in rows(env["sdir"] / "log.jsonl") if x["ev"] == "cycle"]
    assert len(cyc) == 3 and all(
        x["proposer"] == "hold" and x["why"] == "no arm looks better" for x in cyc
    )
