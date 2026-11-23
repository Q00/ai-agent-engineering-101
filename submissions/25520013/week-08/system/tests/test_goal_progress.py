"""[목표 진척]: brief.goal_progress on a 3-player board with hand-computed numbers, and its place on the card."""

import json

import pytest
from kernel import brief

LOGIN = "secret-login"
H = 3600
NOW = brief.ROUND_END_TS - 30 * H  # 30 h to round end


def _board(totals, shops, buyers):
    """totals: {player: total}; shops / buyers: {name: columns}. Row name S-x / B-x belongs to player x."""
    who = {"me": LOGIN, "a": "rival-a", "b": "rival-b"}
    return {
        "players": [{"player": who[p], "total": t, "shop": {"name": f"S-{p}"}, "buyer": {"name": f"B-{p}"}}
                    for p, t in totals.items()],
        "shops": [dict(cols, name=n, owner=who[n[2:]], all=0.0) for n, cols in shops.items()],
        "buyers": [dict(cols, name=n, owner=who[n[2:]], all=0.0) for n, cols in buyers.items()],
    }


def _shop(book, stars):
    return {"book": book, "revenue": book, "stars_rank": stars}


def _buyer(saved, forum, deals):
    return {"saved": saved, "forum": forum, "deals": deals, "posts": deals}


# now: shop all me 0.5, a 1.0, b 0.0; buyer all me 0.88, a 0.5, b 0.27 -> totals 1.38, 1.5, 0.27
CUR = _board({"me": 1.38, "a": 1.5, "b": 0.27},
             {"S-me": _shop(300, 4.0), "S-a": _shop(500, 4.5), "S-b": _shop(200, 3.0)},
             {"B-me": _buyer(0.5, 0.6, 10), "B-a": _buyer(0.4, 0.5, 5), "B-b": _buyer(0.3, 0.9, 1)})
# 6 h before: b's book and revenue +20/h, a's stars_rank +0.05/h and saved +0.01/h, our forum +0.005/h
OLD6 = _board({"me": 1.37, "a": 1.45, "b": 0.2},
              {"S-me": _shop(300, 4.0), "S-a": _shop(500, 4.2), "S-b": _shop(80, 3.0)},
              {"B-me": _buyer(0.5, 0.57, 10), "B-a": _buyer(0.34, 0.5, 5), "B-b": _buyer(0.3, 0.9, 1)})
OLD24 = _board({"me": 1.40, "a": 1.30, "b": 0.1}, {}, {})


def test_goal_progress_hand_computed():
    g = brief.goal_progress((NOW, CUR), (NOW - 6 * H, OLD6), (NOW - 24 * H, OLD24), LOGIN, NOW)
    assert (g["left_h"], g["n"], g["rank"], g["total"], g["top_total"], g["top_is_me"]) == (30, 3, 2, 1.38, 1.5, False)
    ch = g["change"]
    assert ch["me"][6]["d"] == pytest.approx(0.01) and ch["me"][24]["d"] == pytest.approx(-0.02)
    assert ch["top"][6]["d"] == pytest.approx(0.05) and ch["top"][24]["d"] == pytest.approx(0.2)
    assert ch["me"][6]["h"] == 6 and ch["me"][24]["h"] == 24
    # at round end (30 h): b's book 200 + 20 x 30 = 800 passes a 500 and us 300 -> shop all b 0.8, a 0.6, us 0.1;
    # a's saved 0.4 + 0.3 = 0.7 passes our 0.5, our forum 0.6 + 0.15 = 0.75 -> buyer all a 0.75, us 0.675, b 0.27
    p = g["proj"]
    assert p["total"] == pytest.approx(0.775) and p["rank"] == 3  # b (1.07) overtakes us
    assert p["top_total"] == pytest.approx(1.35) and not p["top_is_me"] and p["top_rank_now"] == 1
    cols = {x["key"]: x for x in g["cols"]}
    assert cols["book"]["need"] == pytest.approx((800 - 300) / 30) and cols["book"]["rate"] == 0
    assert cols["stars_rank"]["top"] == 5 and cols["stars_rank"]["need"] == pytest.approx(1 / 30)  # a: 6.0 clamped
    assert cols["saved"]["need"] == pytest.approx(0.2 / 30)
    assert cols["forum"]["need"] == pytest.approx(0.01) and cols["forum"]["rate"] == pytest.approx(0.005)
    assert cols["deals"]["lead"] and cols["posts"]["lead"] and not cols["book"]["lead"]
    text = "\n".join(brief.section_goal_progress(g))
    assert "우리 3명 중 2위 total 1.380, 1위 1.500, 차이 0.120" in text
    assert "total 변화: 우리 6시간 +0.010, 24시간 -0.020 | 1위 6시간 +0.050, 24시간 +0.200" in text
    assert "최근 6시간 속도로 직선 연장" in text and "우리 0.775 3위, 예상 1위 1.350 (지금 1위)" in text
    assert "book +16.7/h / +0/h" in text and "forum +0.01/h / +0.005/h" in text and "deals 예상 1위, 지금 +0/h" in text
    shop = "\n".join(brief.section_goal_progress(g, "shop"))
    assert "가게 항목" in shop and "구매자 항목" not in shop and len(text) < 600


def test_goal_progress_missing_snapshots():
    assert brief.section_goal_progress(None) == ["[목표 진척] 자료 없음"]
    g = brief.goal_progress((NOW, CUR), None, None, LOGIN, NOW)
    text = "\n".join(brief.section_goal_progress(g))
    assert "우리 6시간 자료 없음, 24시간 자료 없음" in text and "종료 예상: 6시간 전 순위표 없음" in text
    assert brief.goal_progress((NOW, CUR), None, None, "someone-else", NOW) is None


def test_goal_progress_block_first_on_the_card(tmp_path):
    bd = tmp_path / "icrl/state/board"
    bd.mkdir(parents=True)
    for ts, b in ((NOW, CUR), (NOW - 6 * H, OLD6), (NOW - 24 * H, OLD24)):
        (bd / f"{int(ts)}.json").write_text(json.dumps(b))
    (tmp_path / "policy.json").write_text(json.dumps({"owner": {"github": LOGIN}}))
    for role, mine, other in (("shop", "가게 항목", "구매자 항목"), ("buyer", "구매자 항목", "가게 항목"), (None, "가게 항목", "")):
        card = brief.build_compact(str(tmp_path), NOW, NOW - H, role=role)
        assert card.startswith("[목표 진척] 회차 종료까지 30시간"), role
        head = card.split("\n[시각]")[0]
        assert mine in head and (not other or other not in head)
    assert brief.section(str(tmp_path), "목표 진척", NOW).startswith("[목표 진척]")
    assert "- 목표 진척:" not in card  # printed whole, so not in the index
    empty = brief.build_compact(str(tmp_path / "none"), NOW, NOW - H, role="shop")
    assert empty.startswith("[목표 진척] 자료 없음\n")
