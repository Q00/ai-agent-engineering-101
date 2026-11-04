"""v1.26 (2026-11-05): the proposer sees our standing as the board computes it (rows with a non-null 'all', house
shops included) plus an OBJECTIVE and a SCORE line: the official formula, our player rank and gaps, and for the terms
the lane's verdict moves, pct, rank of n, the gap to the next value above and the score of one rank step."""

import json

from icrl import board as B
from icrl import loop

from tests.test_icrl_lanes import PREF, env, lane_of, picker  # noqa: F401
from tests.test_icrl_loop import NOW0

LOGIN = "me-login"


def shop(name, profit, all_=0.5, house=False, owner=None):
    return {
        "name": name,
        "profit": profit,
        "revenue": profit,
        "stars_rank": 1.0,
        "all": all_,
        "house": house,
        "deals": 1,
        "owner": {"login": owner} if owner else {},
    }


def buyer(name, surplus, deals, owner=None):
    return {
        "name": name,
        "surplus": surplus,
        "forum": 0.0,
        "deals": deals,
        "posts": 0,
        "house": False,
        "all": 0.5 if deals > 0 else None,
        "owner": {"login": owner} if owner else {},
    }


BOARD = {
    "shops": [
        shop("me", 50, owner=LOGIN),
        shop("a", 100),
        shop("b", 10),
        shop("h", 70, house=True),  # house shops are ranked on the board
        shop("dead", 999, all_=None),  # no 'all': not ranked
    ],
    "buyers": [
        buyer("me", 5.0, 3, owner=LOGIN),
        buyer("x", 9.0, 2),
        buyer("y", 1.0, 4),
        buyer("z", 100.0, 0),  # no deals: not ranked
    ],
    "players": [
        {
            "player": "p1",
            "shop": {"name": "a", "all": 0.9},
            "buyer": {"name": "x", "all": 0.9},
            "total": 1.8,
        },
        {
            "player": "p2",
            "shop": {"name": "me", "all": 0.7},
            "buyer": {"name": "me", "all": 0.6},
            "total": 1.3,
        },
        {
            "player": "p3",
            "shop": {"name": "b", "all": 0.6},
            "buyer": {"name": "y", "all": 0.8},
            "total": 1.4,
        },
        {"player": "p4", "shop": None, "buyer": None, "total": None},
    ],
}


def test_summary_uses_the_board_population():
    s = B.summary(BOARD, LOGIN)
    p = s["profit"]
    assert (p["n"], p["rank"]) == (4, 3)  # me, a, b, house h; dead (no 'all') left out
    assert p["pct"] == round(1 / 3, 3)  # below: b -> 1 / (4 - 1)
    assert (
        p["gap_up"] == 20.0 and p["gap_down"] == 40.0
    )  # next above is the house shop at 70
    assert p["per_rank"] == round(0.5 / 3, 4)
    sp = s["surplus"]
    assert (sp["n"], sp["rank"], sp["pct"]) == (3, 2, 0.5)  # z has no deals
    assert sp["gap_up"] == 4.0


def test_summary_gaps_keep_three_significant_digits():
    b = {"shops": [shop("me", 1000.0, owner=LOGIN), shop("a", 1000.003)], "buyers": []}
    assert B.summary(b, LOGIN)["profit"]["gap_up"] == 0.003


def test_standing_ranks_our_player_row():
    st = B.standing(BOARD, LOGIN)
    assert st == {
        "total": 1.3,
        "rank": 3,
        "n": 3,
        "gap_next": 0.1,
        "gap_top": 0.5,
        "shop_all": 0.7,
        "buyer_all": 0.6,
    }
    assert B.standing({"shops": [], "players": BOARD["players"]}, LOGIN) is None


def test_objective_lists_the_lane_terms():
    L = {
        "board_summary": B.summary(BOARD, LOGIN),
        "board_standing": B.standing(BOARD, LOGIN),
    }
    o = loop.objective("buyer", L)
    assert o.startswith("OBJECTIVE ") and "\nSCORE " in o and o.endswith("\n")
    score = json.loads(o.split("\nSCORE ", 1)[1])
    assert (
        set(score["lane_terms"]) == {"surplus", "deals"}
        and score["standing"]["rank"] == 3
    )
    assert set(
        json.loads(loop.objective("price", L).split("\nSCORE ", 1)[1])["lane_terms"]
    ) == {"profit", "revenue"}
    assert json.loads(loop.objective("stock", {}).split("\nSCORE ", 1)[1]) == {
        "standing": None,
        "lane_terms": {},
    }


def test_the_prompt_carries_the_objective_before_the_lane(env):
    loop.step(NOW0, ctx=env["ctx"])
    prompts = env["prop"].prompts
    assert {lane_of(p) for p in prompts} == {"price", "stock", "buyer"}
    for p in prompts:
        assert "\nOBJECTIVE " in p and "\nSCORE " in p
        assert p.index("\nSCORE ") < p.index("\nLANE ") < p.index("\nARMS ")
