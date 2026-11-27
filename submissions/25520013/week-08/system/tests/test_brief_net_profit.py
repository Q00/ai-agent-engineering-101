"""[순이익] (net of warranty refunds), the net_profit tool and [구매자 목표] on a synthetic data root."""

import json

import pytest
from kernel import brief, tools

LOGIN = "secret-login"
NOW = 1_794_000_000.0
H = 3600


def _ev(ts, type_, data, src="market", mine=False):
    return {
        "ts": ts,
        "type": type_,
        "src": src,
        "role": "market",
        "mine": mine,
        "data": data,
    }


def _sale(ts, sku, grade, buyer, unit, amount, wh):
    d = {
        "sku": sku,
        "grade": grade,
        "buyer": buyer,
        "unit": unit,
        "amount": amount,
        "own_owner": True,
        "shop": "our shop",
        "order": f"o-{unit}",
    }
    if wh:
        d["warranty_hours"] = wh
    return _ev(ts, "settle", d)


def _gone(unit, cost, category):
    return _ev(
        NOW - 60,
        "unit_gone",
        {"unit": unit, "cost": cost, "category": category, "why": "sold_or_seized"},
        src="inventory",
        mine=True,
    )


def _broke(at, sku, buyer, mine=True, src="market"):
    return _ev(
        at, "broken", {"sku": sku, "buyer": buyer, "broke_at": at}, src=src, mine=mine
    )


EVENTS = (
    [
        # u2 has no unit_gone row: its cost comes from the restock row
        _ev(
            NOW - 20 * H,
            "restock",
            {"unit": "u2", "sku": "BED1", "cost": 600_000, "category": "home>beds"},
            src="inventory",
            mine=True,
        ),
        _sale(
            NOW - 13 * H, "BED1", "A", "b1", "u6", 5_000_000, 2
        ),  # outside the 12 h window
        _sale(
            NOW - 8 * H, "LAMP1", "B", "b5", "u7", 260_000, 1
        ),  # breaks exactly at 1 h + 60 s: refunded
        _sale(
            NOW - 6 * H, "LAMP1", "B", "b4", "u4", 200_000, 3
        ),  # earlier sale of (b4, LAMP1): not the one that broke
        _sale(
            NOW - 5 * H, "BED1", "A", "b1", "u1", 1_100_000, 2
        ),  # breaks inside warranty: refunded
        _sale(
            NOW - 5 * H, "BED1", "A", "b2", "u2", 1_000_000, 1
        ),  # breaks after its 1 h warranty: no refund
        _sale(
            NOW - 5 * H, "BED1", "A", "b3", "u3", 1_000_000, 0
        ),  # no warranty: break, no refund
        _sale(
            NOW - 2 * H, "LAMP1", "B", "b4", "u5", 300_000, 3
        ),  # latest (b4, LAMP1) before the break: refunded
        _ev(
            NOW - 2 * H,
            "settle",
            {
                "sku": "BED1",
                "grade": "A",
                "buyer": "x",
                "amount": 9e6,
                "own_owner": False,
            },
        ),
        _broke(NOW - 8 * H + 3660, "LAMP1", "b5"),
        _broke(NOW - 4.5 * H, "BED1", "b3"),
        _broke(NOW - 4 * H, "BED1", "b1"),
        _broke(NOW - 3 * H, "BED1", "b2"),
        _broke(NOW - 1 * H, "LAMP1", "b4"),
        _broke(
            NOW - 1 * H, "BED1", "zz"
        ),  # no matching sale: counted as unmatched only
        _broke(NOW - 1 * H, "BED1", "b1", mine=False),  # another shop's break
        _ev(
            NOW - 1 * H,
            "broken",
            {"name": "x", "broke_at": NOW - H, "attributed": True},
            src="broken",
            mine=True,
        ),
    ]
    + [_gone(u, 600_000, "home>beds") for u in ("u1", "u3", "u6")]
    + [_gone(u, 100_000, "home>lamps") for u in ("u4", "u5", "u7")]
)


def _buyer(name, owner, saved, forum, deals, posts, all_, alive=True):
    return {
        "name": name,
        "owner": owner,
        "saved": saved,
        "forum": forum,
        "deals": deals,
        "posts": posts,
        "all": all_,
        "alive": alive,
    }


# Alive percentiles over 5 rows (denominator 4); all = 0.5 saved + 0.3 forum + 0.1 deals + 0.1 posts exactly.
BUYERS = [
    _buyer("us", LOGIN, 0.30, 0.5, 100, 10, 0.5125),
    _buyer("ra", "rival-a", 0.40, 0.7, 200, 5, 0.835),
    _buyer("rb", "rival-b", 0.20, 0.6, 50, 20, 0.405),
    _buyer("rc", "rival-c", 0.35, 0.4, 100, 1, 0.5575),
    _buyer("rd", "rival-d", 0.10, 0.2, 10, 30, 0.16),
    _buyer("re", "rival-e", 0.99, 0.9, 999, 999, 0.99, alive=False),
]


@pytest.fixture
def root(tmp_path):
    (tmp_path / ".market").mkdir()
    (tmp_path / ".r3").mkdir()
    (tmp_path / "policy.json").write_text(json.dumps({"owner": {"github": LOGIN}}))
    (tmp_path / ".market/catalog.json").write_text(
        json.dumps(
            {
                "BED1": {"category": "home>beds", "price": 2_000_000},
                "LAMP1": {"category": "home>lamps", "price": 400_000},
            }
        )
    )
    (tmp_path / ".r3/events.jsonl").write_text(
        "".join(json.dumps(e) + "\n" for e in EVENTS)
    )
    bd = tmp_path / "a2/board"
    bd.mkdir(parents=True)
    old = [dict(r, saved=r["saved"] - 0.02) for r in BUYERS]
    (bd / f"{int(NOW - 6 * H)}.json").write_text(json.dumps({"buyers": old}))
    (bd / f"{int(NOW - 60)}.json").write_text(json.dumps({"buyers": BUYERS}))
    return str(tmp_path)


def _groups(sales):
    out = {}
    for s in sales:
        out.setdefault((s["leaf"], s["grade"], bool(s["wh"])), []).append(s)
    return {k: brief._net_sum(v) for k, v in out.items()}


def test_net_profit_joins_breaks_and_refunds(root):
    sales, unmatched, delays = brief.net_profit_data(root, NOW, 12, LOGIN)
    assert len(sales) == 6 and unmatched == 1 and len(delays) == 5
    by_unit = {s["ts"]: s for s in sales if s["sku"] == "LAMP1"}
    assert (
        not by_unit[NOW - 6 * H]["broke"] and by_unit[NOW - 2 * H]["refund"] == 300_000
    )
    g = _groups(sales)
    # [n, revenue, gross, n with cost, broken, refunds n, refund amount]
    assert g[("beds", "A", True)] == [2, 2_100_000, 900_000, 2, 2, 1, 1_100_000]
    assert g[("beds", "A", False)] == [1, 1_000_000, 400_000, 1, 1, 0, 0]
    assert g[("lamps", "B", True)] == [3, 760_000, 460_000, 3, 2, 2, 560_000]
    t = brief._net_sum(sales)
    assert t[2] - t[6] == pytest.approx(100_000)


def test_net_profit_section_text(root):
    out = brief.section_net_profit(root, NOW, LOGIN)
    assert out[0].startswith(
        "[순이익 12시간, 판매 시각 기준] 우리 판매 6건 (원가 이어진 6건)"
    )
    assert (
        "보증 환불 3건 1.7M, 순이익 0.1M; 고장 5건 (83.3%), 판매와 못 이은 고장 1건"
        in out[0]
    )
    assert "중앙 1.0시간 (고장 5건)" in out[1]
    assert out[2].startswith(
        "  beds: A 보증 2건, 총 0.9M, 고장 2, 환불 1건 1.1M, 순 -0.2M, 고장률 100%; A 무보증 1건"
    )
    assert out[3].startswith("  lamps: B 보증 3건")
    assert brief.net_profit_lines([], 0, [], 12) == [
        "[순이익 12시간, 판매 시각 기준] 우리 판매 기록 없음"
    ]


def test_net_profit_tool(root):
    assert (
        "net_profit" in tools.TOOLS
        and len(" ".join(tools.net_profit.__doc__.split())) <= 300
    )
    out = tools.net_profit(root, hours=12, category="home>beds", now=NOW)
    assert "  lamps:" not in out and "  beds: A 보증" in out
    assert (
        "beds SKU별 1묶음" in out
        and "BED1 A: 3건, 총 1.3M, 환불 1.1M, 순 0.2M, 고장률 100%" in out
    )
    assert tools.net_profit(root, hours=100, now=NOW).startswith("[순이익 48시간")
    assert "우리 판매 7건" in tools.net_profit(root, hours=100, now=NOW)


def test_buyer_goal_counts_every_board_row(root):
    """Live 11-10: percentiles over all 64 board rows reproduce the board's all within 0.0005; over the 49 alive rows
    only they miss by up to 0.079. So a dead row still counts."""
    rows = [dict(r) for r in BUYERS]
    for r in rows:
        r["all"] = round(brief.buyer_all(rows, r), 4)
    b6 = {"buyers": [dict(r, saved=r["saved"] - 0.02) for r in rows]}
    out = brief.section_buyer_goal({"buyers": rows}, b6, LOGIN, 6.0)
    text = "\n".join(out)
    me = next(r for r in rows if r["owner"] == LOGIN)
    rank = 1 + sum(1 for r in rows if r["all"] > me["all"])
    assert out[0] == f"[구매자 목표] 우리 구매자 all {me['all']:.3f} (6곳 중 {rank}위)"
    assert "순위표 6곳 백분위" in out[1] and "순위표 all과 최대 오차 0.0000" in out[1]
    pct = brief._pct([r["saved"] for r in rows], me["saved"])
    assert f"  구매자 saved: 0.3, 4위, 백분위 {pct:.3f}" in text  # the dead row's 0.99 is above us
    assert "  구매자 forum: 0.5, 4위, 값 그대로" in text
    assert "값 그대로라 순위가 바뀌어도 all 변화 없음" in text
    gone = [r for r in rows if r["owner"] != LOGIN]
    assert brief.section_buyer_goal({"buyers": gone}, {}, LOGIN, 6.0) == ["[구매자 목표] 우리 구매자 행 없음"]



def test_build_has_both_sections(root):
    text = brief.build(root, NOW)
    assert "[구매자 목표]" in text and "[순이익 12시간" in text
    assert text.index("[구매자 목표]") < text.index("[이익 ") < text.index("[순이익 ")
    assert "rival-" not in text
