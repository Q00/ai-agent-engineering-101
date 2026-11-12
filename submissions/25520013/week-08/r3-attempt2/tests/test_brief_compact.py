"""Section registry, brief.section, brief.build_compact and the brief_section / guide tools on synthetic roots."""

import json
import os

import pytest
from kernel import brief, tools
from kernel import common as c
from tests.test_brief_net_profit import BUYERS, EVENTS
from tests.test_kernel import _board

LOGIN = "secret-login"
NOW = 1_794_000_000.0
H = 3600
COLUMNS = tuple(f"  가게 {k}" for k in ("book", "revenue", "stars_rank")) + tuple(
    f"  구매자 {k}" for k in ("saved", "forum", "deals", "posts"))
OTHERS = ("rival-a", "rival-b", "rival-c", "rival-d", "rival-e", "A Shop", "x.example")


def _legacy_build(root=None, now=None, last_brief_ts=None):
    """brief.build() as it was before the section registry (11-10), kept to pin the output byte for byte."""
    b_ = brief
    root = c.data_root(root)
    now = c.now_ts() if now is None else now
    login = c.owner_login(root)
    snaps = b_.board_snapshots(root)
    out = []
    if not snaps:
        out += b_.section_time(now, None)
        out.append("[순위] 순위표 스냅샷 없음")
        out += b_.section_versions(root, now)
        out += b_.section_trial(root, now)
        out += b_.section_categories(root, now, login)
        out += b_.section_calls(root, now)
    else:
        bts, bpath = snaps[-1]
        b = c.load_json(bpath, {}) or {}
        ts6, p6 = b_._snapshot_near(snaps, bts - 6 * 3600)
        b6 = (c.load_json(p6, {}) or {}) if ts6 < bts else {}
        hours6 = (bts - ts6) / 3600
        out += b_.section_time(now, bts)
        out += b_.section_rank(b, b6, login, hours6)
        out += b_.section_shop_goal(b, b6, login, hours6)
        out += b_.section_buyer_goal(b, b6, login, hours6)
        out += b_.section_profit(root, now, login)
        out += b_.section_net_profit(root, now, login)
        out += b_.section_share24(root, now, login)
        out += b_.section_versions(root, now)
        out += b_.section_rivals(
            b, b6, login, hours6, b_.market_settles(root, ts6), b_._catalog(root)
        )
        out += b_.section_trial(root, now)
        out += b_.section_reflections(root, now)
        out += b_.section_categories(root, now, login)
        out += b_.section_negotiation(root, now, login)
        out += b_.section_stars(root, now, login)
        out += b_.section_calls(root, now)
        out += b_.section_side(
            b.get("shops") or [],
            b6.get("shops") or [],
            b_.SHOP_KEYS,
            "가게",
            login,
            hours6,
        )
        out += b_.section_side(
            b.get("buyers") or [],
            b6.get("buyers") or [],
            b_.BUYER_KEYS,
            "구매자",
            login,
            hours6,
        )
    out += b_.section_inventory(root, now)
    out += b_.section_restock(root, now)
    out += b_.section_seller(root, now)
    out += b_.section_market(root, now, login)
    out += b_.section_forum(root, login, snaps, now)
    out += b_.section_memory(root)
    out += b_.section_harness(
        root, last_brief_ts if last_brief_ts is not None else now - 1800
    )
    return c.mask("\n".join(out), (login,))


def _shops(book, b_book):
    """Shop rows with book / revenue / stars_rank; ours second in book, a faster shop just below."""
    rows = [
        {
            "owner": LOGIN,
            "name": "LSS Shop",
            "book": book,
            "revenue": 1.2e9,
            "stars_rank": 0.6,
        },
        {
            "owner": "rival-a",
            "name": "A Shop",
            "book": 250e6,
            "revenue": 2e9,
            "stars_rank": 0.9,
        },
        {
            "owner": "rival-b",
            "name": "B Shop",
            "book": b_book,
            "revenue": 0.9e9,
            "stars_rank": 0.5,
        },
        {
            "owner": "rival-c",
            "name": "C Shop",
            "book": 120e6,
            "revenue": 0.5e9,
            "stars_rank": 0.7,
        },
    ]
    for s in rows:
        s["all"] = brief.shop_all(rows, s)
    return rows


@pytest.fixture
def root(tmp_path):
    r = tmp_path
    (r / ".market").mkdir()
    (r / ".r3").mkdir()
    (r / "policy.json").write_text(json.dumps({"owner": {"github": LOGIN}}))
    (r / ".market/catalog.json").write_text(
        json.dumps(
            {
                "BED1": {"category": "home>beds", "price": 2_000_000},
                "LAMP1": {"category": "home>lamps", "price": 400_000},
            }
        )
    )
    (r / ".market/inventory.json").write_text(
        json.dumps(
            {
                "balance": 9e5,
                "start": 5e6,
                "injected": 0,
                "bankrupt_line": 5e5,
                "bankruptcies_left": 2,
                "storage_per_day": 1e5,
                "units": [
                    {
                        "sku": "BED1",
                        "grade": "A",
                        "cost": 6e5,
                        "expires_at": NOW + 3600,
                    },
                    {"sku": "LAMP1", "grade": "B", "cost": 1e5},
                ],
            }
        )
    )
    (r / ".market/shops.json").write_text(
        json.dumps([{"name": "A Shop", "owner": "rival-a"}])
    )
    (r / ".r3/events.jsonl").write_text("".join(json.dumps(e) + "\n" for e in EVENTS))
    (r / ".r3/metrics.jsonl").write_text(
        json.dumps(
            {
                "hook": "seller",
                "ts": NOW - 100,
                "decision": "a2_bridge_error",
                "outcome": {"stage": "open"},
            }
        )
        + "\n"
    )
    (r / "a2").mkdir()
    (r / "a2/trajectory.jsonl").write_text(
        "".join(
            json.dumps(x) + "\n"
            for x in (
                {
                    "ts": NOW - 600,
                    "agent": "operator",
                    "kind": "llm",
                    "status": "quota",
                },
                {"ts": NOW - 500, "agent": "operator", "kind": "llm", "status": "ok"},
            )
        )
    )
    bd = r / "a2/board"
    bd.mkdir(parents=True)
    # B Shop grows 5M/h against our 3.3M/h over the 6 h gap: 10M below us, it passes us in 6 h
    for ts, scale, book, b_book in (
        (NOW - 6 * H, 0.9, 170e6, 150e6),
        (NOW - 30 * 60, 0.95, 185e6, 175e6),
        (NOW - 60, 1.0, 190e6, 180e6),
    ):
        b = _board(scale)
        b["shops"] = _shops(book, b_book)
        b["buyers"] = [dict(x, saved=x["saved"] - 0.02 * (1 - scale)) for x in BUYERS]
        (bd / f"{int(ts)}.json").write_text(json.dumps(b))
    return str(r)


def test_build_is_unchanged_by_the_registry(root, tmp_path_factory):
    empty = str(tmp_path_factory.mktemp("empty"))
    for r in (root, empty):
        for last in (None, NOW - 3 * H):
            assert brief.build(r, NOW, last) == _legacy_build(r, NOW, last)


def test_build_matches_legacy_on_the_kernel_fixture_root(tmp_path):
    """The icrl/state/board layout of tests/test_kernel.py (no shops formula columns, saved as an old amount)."""
    bd = tmp_path / "icrl/state/board"
    bd.mkdir(parents=True)
    (bd / f"{int(NOW - 6 * H)}.json").write_text(json.dumps(_board(0.9)))
    (bd / f"{int(NOW - 60)}.json").write_text(json.dumps(_board(1.0)))
    (tmp_path / "policy.json").write_text(json.dumps({"owner": {"github": LOGIN}}))
    assert brief.build(str(tmp_path), NOW) == _legacy_build(str(tmp_path), NOW)


def test_registry_names_are_unique_with_short_descriptions():
    names = [n for n, *_ in brief.SECTIONS]
    assert len(names) == len(set(names)) == 26
    assert all(len(d) <= 40 for _, d, _, _ in brief.SECTIONS)
    assert names[:3] == ["시각", "순위", "가게 목표"] and names[-1] == "하네스 수정"


def test_section_one_and_unknown(root, tmp_path_factory):
    net = brief.section(root, "순이익", now=NOW)
    assert net.startswith("[순이익 12시간, 판매 시각 기준] 우리 판매 6건") and "[가게 목표" not in net
    assert brief.section(root, "가게 목표", now=NOW).startswith("[가게 목표] 우리 가게 all")
    bad = brief.section(root, "없는 절", now=NOW)
    assert bad.startswith("섹션 없음: 없는 절") and "순이익" in bad and "하네스 수정" in bad
    empty = str(tmp_path_factory.mktemp("e"))
    assert brief.section(empty, "가게 목표", now=NOW) == "[가게 목표] 순위표 스냅샷 없음"
    assert tools.call("brief_section", "operator", root, section="별점").startswith("[별점 6시간]")


def test_build_compact_parts_in_order_with_extra(root):
    extra = ["[실험 장부] 진행 중 1건"]
    t = brief.build_compact(root, NOW, NOW - 35 * 60, extra=extra)
    heads = ["[시각]", "[순위] Everyone 4명 중 3위", "[점수 위치]", "[지난 깨어남 이후] 순위표", extra[0],
             "[순이익 12시간", "[가게 상태]", "[알림]", "[더 볼 것] brief_section 도구로 읽음"]
    pos = [t.index(h) for h in heads]
    assert pos == sorted(pos)
    assert t.index(extra[0]) == t.index("\n[시간대별]") - len(extra[0])  # 11-12: the hourly part follows extra
    lines = t.splitlines()
    # (c): one line per formula column, and the shop growing faster below us shows with its hours to pass us
    assert [ln.split(":")[0] for ln in lines if ln.split(":")[0] in COLUMNS] == list(COLUMNS)
    book = next(ln for ln in lines if ln.startswith("  가게 book:"))
    assert "190.0M, 2위" in book and "위 1위 +60.0M" in book and "아래 3위 -10.0M, 지금 속도면 6.0시간 뒤 우리를 넘음" in book
    # (d): only changed columns, against the snapshot 30 min ago
    assert "  가게: book +5.0M" in lines and not any(ln.startswith("  가게: revenue") for ln in lines)
    # (e): best and worst net rows
    assert "  순 상위: beds A 무보증" in t and "  순 하위: beds A 보증" not in t  # three groups: all in 상위
    # (g): alerts that are true here
    for a in ("우리 가게가 지금 가게 목록에 없음", "파산선 0.5M의 2배 이하", "모델 호출 오류: quota 1",
              "판매 처리 오류: a2_bridge_error 1", "최근 1시간 우리 판매 0건"):
        assert a in t
    # (i): every registry section but 시각
    assert "- 순이익: " in t and "- 시각:" not in t and t.endswith("- 하네스 수정: 지난 브리핑 뒤 하네스 수정")
    assert len(t) <= 6_000
    for leak in (LOGIN,) + OTHERS:
        assert leak not in t


def test_build_compact_no_change_and_no_new_snapshot(root):
    t = brief.build_compact(root, NOW, NOW - 50)
    assert "[지난 깨어남 이후] 새 순위표 없음" in t


def test_build_compact_cap_keeps_index(root):
    t = brief.build_compact(root, NOW, extra=["x" * 200] * 60)
    assert len(t) <= brief.COMPACT_CHARS
    assert "[잘림: 묶음 상한" in t and t.endswith("- 하네스 수정: 지난 브리핑 뒤 하네스 수정")


def test_build_compact_empty_root(tmp_path):
    t = brief.build_compact(str(tmp_path), NOW)
    assert "[순위] 순위표 스냅샷 없음" in t and "inventory.json 없음" in t and "만들지 못함" not in t
    assert "[점수 위치]" not in t and "[지난 깨어남 이후]" not in t


S1 = "## 1. 목표와 점수\n\n- 목표 줄\n\n"
S2 = "## 2. 판매\n\n### 2.1 하위 제목\n- 판매 줄\n\n"
GUIDE = ("# 설명서\n\n머리말\n\n" + S1 + S2 + "## 10. 사람이 직접 넣은 변경과 근거\n\n"
         + "".join(f"- 기록 {i}\n" for i in range(3000)))


@pytest.fixture
def guide_file(tmp_path, monkeypatch):
    p = tmp_path / "MARKET_BRIEF.md"
    p.write_text(GUIDE, encoding="utf-8")
    monkeypatch.setenv("R3A2_MARKET_BRIEF", str(p))
    return p


def test_guide_toc_and_sections(guide_file):
    toc = tools.call("guide", "operator", "/unused")
    assert toc.splitlines() == ["시장 설명서 목차", f"- 1. 목표와 점수 ({len(S1)}자)", f"- 2. 판매 ({len(S2)}자)",
                                f"- 10. 사람이 직접 넣은 변경과 근거 ({len(GUIDE) - GUIDE.index('## 10.')}자)"]
    assert tools.guide(None, "1") == S1
    assert tools.guide(None, 2).startswith("## 2. 판매\n\n### 2.1") and "판매 줄" in tools.guide(None, "판매")
    assert tools.guide(None, "없음").startswith("절 없음: 없음\n시장 설명서 목차")


def test_guide_long_section_returns_its_tail(guide_file, monkeypatch):
    monkeypatch.setattr(tools, "MAX_CHARS", 500)
    t = tools.guide(None, "10")
    assert len(t) <= 500 and t.startswith("[앞부분 생략:") and t.endswith("- 기록 2999\n")
    assert "- 기록 0\n" not in t


def test_guide_missing_file(tmp_path, monkeypatch):
    monkeypatch.setenv("R3A2_MARKET_BRIEF", str(tmp_path / "none.md"))
    assert tools.guide(None) == "시장 설명서 없음: none.md"
    assert tools.guide_always() == "시장 설명서 없음: none.md"


def test_guide_always(guide_file):
    t = tools.guide_always(("1", "판매"))
    assert t.startswith(f"## 시장 설명서 목차 (나머지 절은 guide 도구로 읽음)\n- 1. 목표와 점수 ({len(S1)}자)")
    assert "\n\n## 1. 목표와 점수\n\n- 목표 줄\n\n## 2. 판매" in t and "기록 1" not in t
    assert "절 없음: 9" in tools.guide_always(("9",))
