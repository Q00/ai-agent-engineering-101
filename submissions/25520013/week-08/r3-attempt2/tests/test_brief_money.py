"""State card: where the money sits (stock cost per leaf, pace, restock runs) and the new entries of guide section
10. Live 11-12: stock was on the card in units only, and an entry a person appended to section 10 reached nobody."""

import json
import pathlib
from datetime import datetime

from kernel import brief
from kernel import common as c
from tests.test_brief_compact import NOW, root  # noqa: F401  (root is a fixture)


def _card(root, now=NOW):
    return brief.build_compact(str(root), now, now - 1200)


def test_money_lines_sum_the_stock_cost_per_leaf(root):
    root = pathlib.Path(root)
    inv = json.loads((root / ".market/inventory.json").read_text())
    cat = json.loads((root / ".market/catalog.json").read_text())
    want = {}
    for u in inv["units"]:
        leaf = brief._leaf(cat[u["sku"]]["category"])
        n, cost = want.get(leaf, (0, 0.0))
        want[leaf] = (n + 1, cost + u["cost"])
    lines = _card(root).split("\n")
    i = next(k for k, ln in enumerate(lines) if ln.startswith("[돈이 있는 곳]"))
    assert f"현금 {c.fmt(inv['balance'])}" in lines[i] and f"재고 원가 합 {c.fmt(sum(v[1] for v in want.values()))}" in lines[i]
    for leaf, (n, cost) in want.items():
        assert any(ln.startswith(f"  {leaf}: {n}개 원가 합 {c.fmt(cost)}, 판매 ") for ln in lines[i + 1:])


def test_money_lines_absent_without_inventory(root):
    root = pathlib.Path(root)
    (root / ".market/inventory.json").unlink()
    t = _card(root)
    assert "[돈이 있는 곳]" not in t and "만들지 못함" not in t


def test_new_section_10_entries_are_named_and_old_ones_are_not(root, tmp_path, monkeypatch):
    stamp = lambda ts: datetime.fromtimestamp(ts, c.KST).strftime("%m-%d %H:%M")
    new, old = stamp(NOW - 2 * 3600), stamp(NOW - 7 * 3600)
    p = tmp_path / "G.md"
    p.write_text("## 1. 목표\n\n- a\n\n## 10. 사람이 직접 넣은 변경과 근거\n\n"
                 f"- {old} 옛 항목\n  - 근거 줄\n- {new} 새 항목 " + "가" * 400 + "\n  - 근거 줄\n- 날짜 없는 줄\n", encoding="utf-8")
    monkeypatch.setenv("R3A2_MARKET_BRIEF", str(p))
    lines = _card(root).split("\n")
    i = next(k for k, ln in enumerate(lines) if ln.startswith("[사람이 적은 것]"))
    assert "항목 1개" in lines[i] and lines[i + 1].startswith(f"  - {new} 새 항목") and lines[i + 1].endswith(" …")
    assert len(lines[i + 1]) <= brief.HUMAN_NOTE_CHARS + 4 and not any("옛 항목" in ln or "근거 줄" in ln for ln in lines)


def test_no_section_10_or_no_new_entry_adds_nothing(root, tmp_path, monkeypatch):
    p = tmp_path / "G.md"
    p.write_text("## 1. 목표\n\n- a\n", encoding="utf-8")
    monkeypatch.setenv("R3A2_MARKET_BRIEF", str(p))
    assert "[사람이 적은 것]" not in _card(root)
    monkeypatch.setenv("R3A2_MARKET_BRIEF", str(tmp_path / "none.md"))
    t = _card(root)
    assert "[사람이 적은 것]" not in t and "만들지 못함" not in t


def test_money_lines_carry_storage_cost_and_stock_age(root):
    root = pathlib.Path(root)
    inv = json.loads((root / ".market/inventory.json").read_text())
    inv["storage_per_day"] = 0.01
    for k, u in enumerate(inv["units"]):
        u["supplied_at"] = NOW - (k + 1) * 3600
    (root / ".market/inventory.json").write_text(json.dumps(inv))
    total = sum(u["cost"] for u in inv["units"])
    lines = [ln for ln in _card(root).split("\n")]
    head = next(ln for ln in lines if ln.startswith("[돈이 있는 곳]"))
    assert f"보관비 하루 {c.fmt(total * 0.01)} (원가의 1.0%)" in head
    assert any("보관비 하루" in ln and "들어온 지 중앙" in ln and "가장 오래된 것" in ln for ln in lines if ln.startswith("  "))


def test_money_lines_without_a_rate_or_supply_time_say_nothing_of_them(root):
    t = _card(root)
    assert "[돈이 있는 곳]" in t and "원가의" not in t and "들어온 지" not in t
