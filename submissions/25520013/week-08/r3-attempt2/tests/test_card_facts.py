"""11-12: three facts the operators could not see, and the auditor's reach over them.
1. [가게 회전]: the shops above us and ours over a day (book, revenue, deals gain, stock cost).
2. [돈이 있는 곳]: per leaf, the net profit of the tied-up money scaled to a day over its stock cost.
3. [실험]: what a running experiment holds so far and the difference it could tell from noise at the verdict time.
The auditor sees the board comparison and may take these parts off a card (uncard) and put them back (card)."""

import json
import math
import os

from kernel import auditor as au
from kernel import brief, tools
from kernel import common as c
from kernel import experiments as ex
from tests.test_experiments import (  # noqa: F401  (root is a fixture)
    BED,
    CATALOG,
    S,
    H,
    _buy,
    _exp,
    _hourly,
    _put_ledger,
    CATS,
    _sale,
    _set_pol,
    _traj,
    _write_events,
    a1,
    root,
)

LOGIN = "secret-login"
NOW = 1_794_400_000.0


# ------------------------------------------------------------ 1. turnover
def _shop(name, all_, book, revenue, deals, cost, owner="other"):
    return {"owner": owner, "name": name, "all": all_, "book": book, "revenue": revenue, "deals": deals,
            "stock_cost": cost}


def _boards():
    now = {"shops": [_shop("Alpha Shop", 0.95, 500e6, 3000e6, 9000, 100e6),
                     _shop("Beta Shop", 0.90, 400e6, 2000e6, 5000, 0),
                     _shop("LSS Shop", 0.80, 300e6, 1000e6, 2000, 200e6, owner=LOGIN),
                     _shop("Gamma Shop", 0.70, 100e6, 500e6, 900, 50e6),
                     _shop("New Shop", 0.85, 50e6, 60e6, 10, 5e6)]}
    old = {"shops": [_shop("Alpha Shop", 0.9, 450e6, 2400e6, 7000, 80e6),
                     _shop("Beta Shop", 0.9, 410e6, 1900e6, 4800, 10e6),
                     _shop("LSS Shop", 0.8, 290e6, 900e6, 1800, 150e6, owner=LOGIN),
                     _shop("Gamma Shop", 0.7, 90e6, 400e6, 800, 50e6)]}
    return now, old


def test_turnover_lists_the_shops_above_us_and_ours_with_hashed_names():
    now, old = _boards()
    out = brief.section_turnover(now, old, 24.0, LOGIN)
    assert out[0].startswith("[가게 회전 24시간] 가게 all 순서로 우리 위 가게와 우리 (우리 4/5위).")
    assert out[1] == (f"  1위 {c.hid('Alpha Shop')}: book +50.0M, 매출 +600.0M, 거래 +2,000, 재고 원가 100.0M (80.0M), "
                      "매출/재고 원가 6.0배")
    # a shop with no stock cost has no ratio; a shrinking book keeps its sign
    assert out[2] == f"  2위 {c.hid('Beta Shop')}: book -10.0M, 매출 +100.0M, 거래 +200, 재고 원가 0 (10.0M)"
    assert out[3] == f"  3위 {c.hid('New Shop')}: 24시간 전 기록 없음, 재고 원가 5.0M"
    assert out[4] == "  4위 우리: book +10.0M, 매출 +100.0M, 거래 +200, 재고 원가 200.0M (150.0M), 매출/재고 원가 0.5배"
    assert len(out) == 5  # the shop below us is not listed
    text = "\n".join(out)
    assert not any(n in text for n in ("Alpha", "Beta", "Gamma", "New Shop", "LSS"))


def test_turnover_caps_the_shops_above_and_handles_first_place_and_no_snapshot():
    many = {"shops": [_shop(f"S{i}", 1 - i / 100, 1e6, 1e6, 1, 1e6) for i in range(12)]
            + [_shop("LSS Shop", 0.5, 1e6, 1e6, 1, 1e6, owner=LOGIN)]}
    out = brief.section_turnover(many, many, 24.0, LOGIN)
    assert len(out) == 1 + brief.RIVALS_MAX + 1 and out[-1].startswith("  13위 우리:")
    assert out[0].startswith("[가게 회전 24시간] 가게 all 순서로 우리 위 12곳 중 위에서 8곳과 우리 (우리 13/13위).")
    assert [ln.split(":")[0] for ln in out[1:9]] == [f"  {i + 1}위 {c.hid(f'S{i}')}" for i in range(8)]  # the top eight
    now, old = _boards()
    now["shops"][2]["all"] = 0.99
    out = brief.section_turnover(now, old, 24.0, LOGIN)
    assert out[1].startswith("  1위 우리:") and len(out) == 5  # first place: ours and the next three
    assert out[0].startswith("[가게 회전 24시간] 가게 all 순서로 우리가 1위라 우리와 바로 아래 가게 (우리 1/5위).")
    for h in (0.5, None, float("nan"), float("inf")):
        assert brief.section_turnover(now, old, h, LOGIN) == ["[가게 회전] 순위표 비교 불가 (스냅샷 부족)"]
    no_all = {"shops": [dict(x, all=None) if x["owner"] == LOGIN else x for x in now["shops"]]}
    for b in ({}, {"shops": old["shops"][:2]}, no_all):
        assert brief.section_turnover(b, old, 24.0, LOGIN) == [
            "[가게 회전] 순위표 비교 불가 (순위표에 우리 가게 행이 없거나 all 값이 없음)"]


def test_turnover_shows_a_missing_column_as_missing_not_as_zero():
    now, old = _boards()
    del now["shops"][0]["deals"]  # the board gave no deals for the first shop now
    old["shops"][0]["revenue"] = None  # and no revenue for it a day ago
    now["shops"][2]["stock_cost"] = None
    now["shops"].append("not a row")
    old["shops"].append(7)
    out = brief.section_turnover(now, old, 24.0, LOGIN)
    assert out[1] == f"  1위 {c.hid('Alpha Shop')}: book +50.0M, 매출 -, 거래 -, 재고 원가 100.0M (80.0M)"
    assert out[4] == "  4위 우리: book +10.0M, 매출 +100.0M, 거래 +200, 재고 원가 - (150.0M)"


def test_turnover_reads_nan_and_inf_as_missing():
    now, old = _boards()
    now["shops"][0].update(book=float("nan"), revenue=float("inf"))
    now["shops"][1]["stock_cost"] = float("inf")
    old["shops"][2]["stock_cost"] = float("nan")
    now["shops"][3]["all"] = float("nan")  # a row without a rank value is not ranked
    out = brief.section_turnover(now, old, 24.0, LOGIN)
    assert out[0].startswith("[가게 회전 24시간] 가게 all 순서로 우리 위 가게와 우리 (우리 4/4위).")
    assert out[1] == f"  1위 {c.hid('Alpha Shop')}: book -, 매출 -, 거래 +2,000, 재고 원가 100.0M (80.0M)"
    assert out[2] == f"  2위 {c.hid('Beta Shop')}: book -10.0M, 매출 +100.0M, 거래 +200, 재고 원가 - (10.0M)"
    assert out[4] == "  4위 우리: book +10.0M, 매출 +100.0M, 거래 +200, 재고 원가 200.0M (-), 매출/재고 원가 0.5배"
    assert "nan" not in "\n".join(out) and "inf" not in "\n".join(out)


def test_turnover_pairs_a_row_with_its_own_old_row_or_with_none():
    now, old = _boards()
    for b in (now, old):  # each shop has its own owner field, as on the board
        for i, x in enumerate(b["shops"]):
            if x["owner"] != LOGIN:
                x["owner"] = {"id": x["name"]}
    # a rival carries our shop's name: it pairs with its own old row, ours is found by owner
    now["shops"][0]["name"] = old["shops"][0]["name"] = "LSS Shop"
    out = brief.section_turnover(now, old, 24.0, LOGIN)
    assert out[1] == (f"  1위 {c.hid('LSS Shop')}: book +50.0M, 매출 +600.0M, 거래 +2,000, 재고 원가 100.0M (80.0M), "
                      "매출/재고 원가 6.0배")
    assert out[4] == "  4위 우리: book +10.0M, 매출 +100.0M, 거래 +200, 재고 원가 200.0M (150.0M), 매출/재고 원가 0.5배"
    assert "LSS" not in "\n".join(out)
    # two rivals swapped names since the old snapshot: neither is given the other's numbers
    now, old = _boards()
    for b in (now, old):
        for x in b["shops"]:
            if x["owner"] != LOGIN:
                x["owner"] = {"id": x["name"]}
    now["shops"][0]["name"], now["shops"][1]["name"] = "Beta Shop", "Alpha Shop"
    out = brief.section_turnover(now, old, 24.0, LOGIN)
    assert out[1] == f"  1위 {c.hid('Beta Shop')}: 24시간 전 기록 없음, 재고 원가 100.0M"
    assert out[2] == f"  2위 {c.hid('Alpha Shop')}: 24시간 전 기록 없음, 재고 원가 0"
    # a name and owner two rows share, in the old snapshot only or in the present one: which row is whose is not known
    now, old = _boards()
    old["shops"].append(_shop("Alpha Shop", 0.1, 1e6, 1e6, 1, 1e6))
    now["shops"][4]["name"] = "Beta Shop"
    out = brief.section_turnover(now, old, 24.0, LOGIN)
    assert out[1] == f"  1위 {c.hid('Alpha Shop')}: 24시간 전 기록 없음, 재고 원가 100.0M"
    assert out[2] == f"  2위 {c.hid('Beta Shop')}: 24시간 전 기록 없음, 재고 원가 0"
    # rows without a name pair with nothing; a number and a text that print alike are two names
    now, old = _boards()
    now["shops"][0]["name"] = old["shops"][0]["name"] = None
    now["shops"][1]["name"], old["shops"][1]["name"] = 1, "1"
    out = brief.section_turnover(now, old, 24.0, LOGIN)
    assert out[1].endswith(": 24시간 전 기록 없음, 재고 원가 100.0M") and out[2].endswith(": 24시간 전 기록 없음, 재고 원가 0")


def test_turnover_survives_numbers_outside_float_range():
    now, old = _boards()
    now["shops"][0]["revenue"] = 10 ** 400
    now["shops"][1].update(book=9e299, deals=9e299)  # each is read as a number, their difference is not
    old["shops"][1].update(book=-9e299, deals=-9e299)
    now["shops"][3]["all"] = 10 ** 400  # not ranked
    old["shops"][2]["stock_cost"] = 10 ** 400
    out = brief.section_turnover(now, old, 24.0, LOGIN)
    assert out[0].startswith("[가게 회전 24시간] 가게 all 순서로 우리 위 가게와 우리 (우리 4/4위).")
    assert out[1] == f"  1위 {c.hid('Alpha Shop')}: book +50.0M, 매출 -, 거래 +2,000, 재고 원가 100.0M (80.0M)"
    assert out[2] == f"  2위 {c.hid('Beta Shop')}: book -, 매출 +100.0M, 거래 -, 재고 원가 0 (10.0M)"
    assert out[4] == "  4위 우리: book +10.0M, 매출 +100.0M, 거래 +200, 재고 원가 200.0M (-), 매출/재고 원가 0.5배"
    # a ratio that leaves the float range is not printed
    now, old = _boards()
    now["shops"][0].update(revenue=1e299, stock_cost=1e-300)
    assert "배" not in brief.section_turnover(now, old, 24.0, LOGIN)[1]


def _board_root(tmp_path, hours=(25, 6, 0)):
    """A data root with policy owner, three board snapshots (the oldest 25 h back) and nothing else."""
    r = tmp_path / "data"
    (r / "a2/board").mkdir(parents=True)
    (r / ".market").mkdir()
    (r / ".r3").mkdir()
    (r / "policy.json").write_text(json.dumps({"owner": {"github": LOGIN}}))
    now, old = _boards()
    for h in hours:
        (r / f"a2/board/{int(NOW - h * 3600)}.json").write_text(json.dumps(now if h == 0 else old))
    return str(r)


def test_turnover_is_on_the_shop_card_only_and_in_the_registry_but_not_in_build(tmp_path):
    r = _board_root(tmp_path)
    shop = brief.build_compact(r, NOW, NOW - 1200, role="shop")
    assert "[가게 회전 25시간]" in shop and "4위 우리: book +10.0M" in shop and "Alpha" not in shop
    assert shop.index("[가게 회전") < shop.index("[더 볼 것]")
    assert "[가게 회전" not in brief.build_compact(r, NOW, NOW - 1200, role="buyer")
    assert "[가게 회전 25시간]" in brief.build_compact(r, NOW, NOW - 1200)  # no role: both sides
    assert "[가게 회전" not in brief.build_compact(r, NOW, NOW - 1200, role="shop", off=("가게 회전",))
    assert "- 가게 회전: " in shop  # the index names it for brief_section
    assert brief.section(r, "가게 회전", NOW).startswith("[가게 회전 25시간]")
    assert "[가게 회전" not in brief.build(r, NOW)
    empty = str(tmp_path / "none")
    os.makedirs(empty)
    assert brief.section(empty, "가게 회전", NOW) == "[가게 회전] 순위표 스냅샷 없음"
    assert "가게 회전" not in brief.build_compact(empty, NOW, None, role="shop").split("[더 볼 것]")[0]


def test_turnover_uses_the_nearest_snapshot_when_a_day_is_not_there(tmp_path):
    r = _board_root(tmp_path, hours=(6, 0))
    assert brief.section(r, "가게 회전", NOW).startswith("[가게 회전 6시간]")
    r2 = _board_root(tmp_path / "one", hours=(0,))
    assert brief.section(r2, "가게 회전", NOW) == "[가게 회전] 순위표 비교 불가 (스냅샷 부족)"


# ------------------------------------------------------------ 2. net profit of the tied-up money
def _money_root(tmp_path):
    r = tmp_path / "data"
    (r / ".market").mkdir(parents=True)
    (r / ".r3").mkdir()
    (r / "policy.json").write_text(json.dumps({"owner": {"github": LOGIN}}))
    (r / ".market/catalog.json").write_text(json.dumps({
        "BED1": {"category": "home>beds"}, "LAMP1": {"category": "home>lamps"}, "RUG1": {"category": "home>rugs"}}))
    (r / ".market/inventory.json").write_text(json.dumps({
        "balance": 9e6, "storage_per_day": 0.01,
        "units": [{"sku": "BED1", "cost": 6e5}, {"sku": "LAMP1", "cost": 1e5}, {"sku": "RUG1", "cost": 2e5}]}))

    def sale(ts, sku, unit, amount, cost=None, wh=0):
        d = {"sku": sku, "unit": unit, "buyer": f"b-{unit}", "amount": amount, "own_owner": True, "grade": "A"}
        if wh:
            d["warranty_hours"] = wh
        rows = [{"ts": ts, "type": "settle", "src": "market", "data": d}]
        if cost is not None:
            rows.append({"ts": ts - 10, "type": "unit_gone", "src": "inventory", "mine": True,
                         "data": {"unit": unit, "cost": cost, "category": {"BED1": "home>beds", "LAMP1": "home>lamps"}[sku]}})
        return rows

    ev = sale(NOW - 3600, "BED1", "u1", 9e5, 6e5)  # gross 300k
    ev += sale(NOW - 7200, "BED1", "u2", 7e5)  # no known cost: gross counted as 0
    ev += sale(NOW - 5 * 3600, "LAMP1", "u3", 2e5, 1e5, wh=6)  # gross 100k, refunded 200k
    ev.append({"ts": NOW - 4 * 3600, "type": "broken", "src": "market", "mine": True,
               "data": {"sku": "LAMP1", "buyer": "b-u3", "broke_at": NOW - 4 * 3600}})
    ev += sale(NOW - 13 * 3600, "BED1", "u4", 9e6, 1e5)  # outside the 12 h window
    (r / ".r3/events.jsonl").write_text("".join(json.dumps(e) + "\n" for e in sorted(ev, key=lambda e: e["ts"])))
    return str(r)


def _money(r, **kw):
    lines = brief.build_compact(r, NOW, NOW - 1200, role="shop", **kw).split("\n")
    i = next(k for k, ln in enumerate(lines) if ln.startswith("[돈이 있는 곳]"))
    return lines[i], [ln for ln in lines[i + 1:] if ln.startswith("  ")]


def test_money_lines_carry_the_net_profit_per_leaf_over_its_stock_cost(tmp_path):
    head, rows = _money(_money_root(tmp_path))
    assert "보관비 하루 9e+03 (원가의 1.0%)" in head
    assert head.endswith("같은 12시간의 순이익(보증 환불 뺌)과 그것을 하루로 환산해 지금 원가 합으로 나눈 값")
    beds = next(ln for ln in rows if ln.startswith("  beds:"))
    # 300k over 12 h is 600k a day against a stock cost of 600k
    assert ("판매 2개, 그 속도로 6시간 치, 순이익 0.3M (하루로 환산하면 원가 합의 100.0%), "
            "원가 모르는 판매 1개는 총이익 0으로 셈, 보관비 하루") in beds
    lamps = next(ln for ln in rows if ln.startswith("  lamps:"))
    # gross 100k minus the 200k refund = -100k over 12 h, -200k a day against a stock cost of 100k
    assert "판매 1개, 그 속도로 12시간 치, 순이익 -0.1M (하루로 환산하면 원가 합의 -200.0%), 보관비 하루" in lamps
    rugs = next(ln for ln in rows if ln.startswith("  rugs:"))
    assert "판매 0개, 보관비 하루" in rugs and "순이익" not in rugs


def test_money_lines_without_the_part_are_as_before(tmp_path):
    head, rows = _money(_money_root(tmp_path), off=("묶인 돈 수익",))
    assert head.endswith("그 속도로 지금 재고를 나눈 시간") and not any("순이익" in ln for ln in rows)
    assert any(ln.startswith("  beds: 1개 원가 합 0.6M, 판매 2개, 그 속도로 6시간 치, 보관비 하루") for ln in rows)


# ------------------------------------------------------------ 3. running experiments so far
def _arm_units(eid, n_a, n_b):
    a, b, k = [], [], 0
    while len(a) < n_a or len(b) < n_b:
        k += 1
        u = f"arm{eid}-{k}"
        (b if ex.arm_b(eid, u) else a).append(u)
    return a[:n_a], b[:n_b]


def _split_exp(window_h=8, **kw):
    # the arm lives well past the verdict time (the kernel extends a split only while it does)
    kw.setdefault("arm", {"share": ex.ARM_SHARE, "until": S + 100 * H})
    return _exp(BED, mode="split", window_h=window_h, **kw)


def _root_like(root, tag, exps, rows):
    """A second data root with root's policy and catalog, its own ledger and events."""
    r = root + tag
    for d in (".r3", ".market", "a2"):
        os.makedirs(os.path.join(r, d))
    for f in ("policy.json", ".market/catalog.json"):
        open(os.path.join(r, f), "w").write(open(os.path.join(root, f)).read())
    open(os.path.join(r, ".r3/events.jsonl"), "w").close()
    _put_ledger(r, exps)
    _write_events(r, rows)
    return r


STARS = "; 리뷰가 양쪽 10건 이상이고 별점이 0.15 넘게 낮아지면 차이와 상관없이 악화 판정"  # the shop side's guard
LAST_SPLIT = ("마지막 구간이라 못 미치면 B가 A보다 1se(±{}) 넘게 좋고 표본이 각 5 이상일 때만 B를 채택하고 아니면 A로 둠 "
              "(원가나 단위를 모르는 판매가 많아도 A로 둠)")
LAST_SEQ = ("마지막 구간이라 못 미치면 차이의 부호로 정함 (0 이상이면 값 유지, 음이면 되돌림; "
            "원가 모르는 판매가 많거나 전후 모두 판매가 없으면 값 유지)")


def _ab_sales(ua, ub, net_a, net_b):
    rows = []
    for i, u in enumerate(ua):
        rows += _sale(S + 600 + i * 1800, "BED1", 1_000_000 + net_a, 1_000_000, unit=u)
    for i, u in enumerate(ub):
        rows += _sale(S + 900 + i * 1800, "BED1", 1_000_000 + net_b, 1_000_000, unit=u)
    return rows


def test_interim_line_of_a_split_matches_hand_arithmetic(root):
    e = _split_exp()  # window 8 h: the kernel does not extend it, so this window is the last
    _put_ledger(root, [e])
    ua, ub = _arm_units(1, 2, 2)
    rows = _ab_sales(ua, ub, 100_000, 300_000)  # arm a: two sales, net 100k each; arm b: two, net 300k each
    rows += _sale(S + 3 * H, "BED1", 9_000_000, 1_000_000, unit=ua[0] + "-later")  # after now: not counted
    _write_events(root, rows)
    out = ex.lines(root, S + 2 * H, interim=True)
    # a 200k / 2 h, b 600k / 2 h; se = sqrt(2 x 1e10 + 2 x 9e10) / 2 h = 223,607; over 8 h it halves: 111,803, and
    # 2se = 223,607; hours for +200k/h to clear: 2 x (2 x 223,607 / 200,000)^2 = 10, past the 8 h of the last window
    assert out[2] == (
        "    지금까지 2.0/8시간: 판매 A 2 / B 2 (이 속도면 판정 때 약 8 / 8, 판정에 필요한 표본 각 5 합 10), "
        "값 A 0.10M/h, B 0.30M/h, 차이 +0.20M/h. 판매가 이대로면 뚜렷한 판정에 필요한 차이는 ±0.22M/h부터 "
        "(잡음(2se)과 A 값의 10% 기준 중 큰 쪽" + STARS + "), " + LAST_SPLIT.format("0.11M/h")
        + ". 지금 차이는 이어져도 마지막 구간(8시간) 안에 잡음을 넘지 못함")
    # at a time that is not a whole hour the values are over the hours run (the bucket mean would say +0.20M/h):
    # a 200k / 2.25 h = 88,889 (under the 100,000 floor, so the second rule is the floor's 10%), b 600k / 2.25 h;
    # se = 447,214 / 2.25 = 198,762; over 8 h: 198,762 / sqrt(8 / 2.25) = 105,409, 2se = 210,819
    assert ex.lines(root, S + 2.25 * H, interim=True)[2] == (
        "    지금까지 2.2/8시간: 판매 A 2 / B 2 (이 속도면 판정 때 약 7 / 7, 판정에 필요한 표본 각 5 합 10), "
        "값 A 88,889/h, B 0.27M/h, 차이 +0.18M/h. 판매가 이대로면 뚜렷한 판정에 필요한 차이는 ±0.21M/h부터 "
        "(잡음(2se)과 최소 기준 중 큰 쪽" + STARS + "), " + LAST_SPLIT.format("0.11M/h")
        + ". 지금 차이는 이어져도 마지막 구간(8시간) 안에 잡음을 넘지 못함")
    assert out[1].startswith("  진행 #1 동시 비교 ") and len(out) == 3
    assert ex.lines(root, S + 2 * H) == [out[0], out[1]]  # off by default (auditor, investigator, made agents)
    assert out[2] in tools.experiments(root, hours=2, now=S + 2 * H)
    # hours shown are cut, not rounded up: 2.96 h is not "3.0"
    assert ex.lines(root, S + 2.96 * H, interim=True)[2].startswith("    지금까지 2.9/8시간: ")


def test_interim_line_of_a_split_names_the_extension_and_the_last_window_rule(root):
    ua, ub = _arm_units(1, 2, 2)
    # window 4 h, not extended: an undecided verdict doubles it to 8 h. Same sales as above: se over 4 h is
    # 223,607 / sqrt(2) = 158,114, 2se = 316,228
    r4 = _root_like(root, "-w4", [_split_exp(window_h=4)], _ab_sales(ua, ub, 100_000, 300_000))
    assert ex.lines(r4, S + 2 * H, interim=True)[2].endswith(
        "판매가 이대로면 뚜렷한 판정에 필요한 차이는 ±0.32M/h부터 (잡음(2se)과 A 값의 10% 기준 중 큰 쪽" + STARS + "), "
        "못 미치면 커널이 구간을 8시간으로 늘려 한 번 더 봄. 지금 차이는 이어져도 마지막 구간(8시간) 안에 잡음을 넘지 못함")
    # the same window once extended is the last one
    rx = _root_like(root, "-w4x", [_split_exp(window_h=4, extended=True)], _ab_sales(ua, ub, 100_000, 300_000))
    assert ex.lines(rx, S + 2 * H, interim=True)[2].endswith(
        "(잡음(2se)과 A 값의 10% 기준 중 큰 쪽" + STARS + "), " + LAST_SPLIT.format("0.16M/h")
        + ". 지금 차이는 이어져도 마지막 구간(4시간) 안에 잡음을 넘지 못함")
    # the arm already gone (a verdict more than ARM_GRACE_S late): no extension, the 4 h window is judged by the weak rule
    rd = _root_like(root, "-w4d", [_split_exp(window_h=4, arm={"share": ex.ARM_SHARE, "until": S + 4 * H + 1800})],
                    _ab_sales(ua, ub, 100_000, 300_000))
    assert "못 미치면 커널이 구간을 8시간으로 늘려 한 번 더 봄" in ex.lines(rd, S + 4 * H + 1799, interim=True)[2]
    assert ex.lines(rd, S + 4 * H + 1800, interim=True)[2].endswith(
        LAST_SPLIT.format("0.11M/h") + ". 지금 차이는 이어져도 마지막 구간(4시간) 안에 잡음을 넘지 못함")
    # b net 400k each: diff 300k/h, se = sqrt(2e10 + 32e10) / 2 = 291,548; hours to clear 2 x (583,095 / 300,000)^2
    # = 7.6: inside the 8 h window but not yet (the present noise is compared with the hours run, not the window)
    r8 = _root_like(root, "-w8", [_split_exp()], _ab_sales(ua, ub, 100_000, 400_000))
    assert ex.lines(r8, S + 2 * H, interim=True)[2].endswith(". 지금 차이는 이어지면 시작부터 약 8시간에 잡음을 넘음")


def test_interim_line_of_a_split_under_the_share_rule_or_already_past_the_noise(root):
    e = _split_exp()
    ua, ub = _arm_units(1, 120, 120)
    rows = []
    for i in range(120):  # a net 100k, b net 101k, 120 sales each inside the first hour
        rows += _sale(S + 5 + i * 29, "BED1", 1_100_000, 1_000_000, unit=ua[i])
        rows += _sale(S + 9 + i * 29, "BED1", 1_101_000, 1_000_000, unit=ub[i])
    _put_ledger(root, [e])
    _write_events(root, rows)
    line = ex.lines(root, S + H, interim=True)[2]
    assert "판매 A 120 / B 120 (이 속도면 판정 때 약 960 / 960" in line
    # a 12.0M/h, b 12.12M/h; se = sqrt(120e10 + 120 x 1.0201e10) = 1,556,920, over 8 h 550,454, 2se = 1,100,909:
    # under 10% of the A value (1.2M), so the share rule is the bar, and +120,000/h is under it
    assert line.endswith(
        "차이 +0.12M/h. 판매가 이대로면 뚜렷한 판정에 필요한 차이는 ±1.20M/h부터 (잡음(2se)과 A 값의 10% 기준 중 큰 쪽" + STARS + "), "
        + LAST_SPLIT.format("0.55M/h") + ". 지금 차이는 A 값의 10% 기준(±1.20M/h)에 못 미침")
    # only arm b sells, six sales of net 300k in the first hour: the difference is past the present noise
    r2 = _root_like(root, "-b", [e], [x for i in range(6)
                                      for x in _sale(S + 60 + i * 500, "BED1", 1_300_000, 1_000_000, unit=ub[i])])
    line = ex.lines(r2, S + H, interim=True)[2]
    assert "판매 A 0 / B 6" in line and line.endswith(". 지금 차이는 이미 지금의 잡음을 넘음")
    # the window over (the verdict waits for late breaks): no pace, and the figures are those of the whole window.
    # b 1.8M / 8 h = 225,000/h; se = sqrt(6 x 9e10) / 8 = 91,856, 2se = 183,712; a is 0, so the second rule is the floor
    assert ex.lines(r2, S + 9 * H, interim=True)[2] == (
        "    지금까지 8/8시간 (구간 끝남, 판정 대기): 판매 A 0 / B 6 (판정에 필요한 표본 각 5 합 10), 값 A 0/h, B 0.23M/h, "
        "차이 +0.23M/h. 뚜렷한 판정에 필요한 차이는 ±0.18M/h부터 (잡음(2se)과 최소 기준 중 큰 쪽" + STARS + "), "
        + LAST_SPLIT.format("91,856/h") + ". 지금 차이는 이미 지금의 잡음을 넘음")
    v = ex.evaluate_split(r2, e, S + 9 * H)
    assert abs(v["se"] - 91_855.87) < 0.01 and v["effect"] == 225_000 and v["decision"] == "thin"  # a has no sale


def test_interim_line_of_a_before_after_experiment(root):
    e = _exp(BED)  # window 3 h, sequential, not extended: an undecided verdict doubles it to 6 h
    _put_ledger(root, [e])
    _write_events(root, _hourly(S - 3 * H, 3, "BED1", 100_000) + _hourly(S, 2, "BED1", 200_000))
    one = ex.lines(root, S + H, interim=True)[2]
    # before: buckets 285k, 300k, 315k -> 300k/h; after 1 h: 3 x 190k = 570k/h; market (our sales only) scales the
    # before value by r = (3 x 1.19M / 1 h) / (9.9M / 3 h)
    r = (3 * 1_190_000) / (9_900_000 / 3)
    adj = 300_000 * r
    var_b = r * r * 225e6  # sample variance of 285k, 300k, 315k
    assert 2 * math.sqrt(var_b / 3 + var_b / 3) < 0.1 * adj  # the noise (26,5xx) is under 10% of the base (32,4xx)
    more = ", 못 미치면 커널이 구간을 6시간으로 늘려 한 번 더 봄"
    assert one == (
        f"    지금까지 1.0/3시간: 판매 전 9 → 후 3 (이 속도면 판정 때 후 약 9, 판정에 필요한 표본 5), "
        f"전 보정 {adj / 1e6:.2f}M/h, 후 0.57M/h, 차이 +{(570_000 - adj) / 1e6:.2f}M/h. "
        f"시간별 흔들림이 이대로면 뚜렷한 판정에 필요한 차이는 ±{0.1 * adj:,.0f}/h부터 "
        "(잡음(2se)과 전 보정 값의 10% 기준 중 큰 쪽, 후 구간이 온전한 2시간 미만이라 전 구간의 흔들림으로 셈" + STARS + ")" + more)
    # after 2 h: buckets 570k, 600k (variance 4.5e8), r2 = (7.17M / 2 h) / 3.3M; the after variance is spread over the
    # 3 h of the window, not the 2 h run: 2 x sqrt(4.5e8 / 3 + r2^2 x 2.25e8 / 3) = 30,9xx, under the 10% rule (32,5xx)
    r2 = (3 * 1_190_000 + 3 * 1_200_000) / 2 / (9_900_000 / 3)
    noise2 = 2 * math.sqrt(4.5e8 / 3 + r2 * r2 * 2.25e8 / 3)
    assert noise2 < 0.1 * 300_000 * r2 < 2 * math.sqrt(4.5e8 / 2 + r2 * r2 * 2.25e8 / 3)
    two = ex.lines(root, S + 2 * H, interim=True)[2]
    assert two.startswith("    지금까지 2.0/3시간: 판매 전 9 → 후 6 (이 속도면 판정 때 후 약 9, ")
    assert two.endswith(f"시간별 흔들림이 이대로면 뚜렷한 판정에 필요한 차이는 ±{0.1 * 300_000 * r2:,.0f}/h부터 "
                        "(잡음(2se)과 전 보정 값의 10% 기준 중 큰 쪽" + STARS + ")" + more)
    # past the window end: no pace, and the figure is the one evaluate() uses
    v = ex.evaluate(root, e, S + 5 * H)
    need = max(2 * v["se"], 0.1 * abs(v["before_adj"]))
    assert abs(v["before_adj"]) > ex.FLOOR["shop"]
    assert ex.lines(root, S + 5 * H, interim=True)[2] == (
        "    지금까지 3/3시간 (구간 끝남, 판정 대기): 판매 전 9 → 후 6 (판정에 필요한 표본 5), "
        f"전 보정 {ex._num(v['before_adj'], 'shop')}, 후 {ex._num(v['after'], 'shop')}, "
        f"차이 {ex._num(v['effect'], 'shop', sign=True)}. 뚜렷한 판정에 필요한 차이는 ±{ex._num(need, 'shop')}부터 "
        "(잡음(2se)과 전 보정 값의 10% 기준 중 큰 쪽" + STARS + ")" + more)
    # once extended, the window is the last and the sign decides
    rx = _root_like(root, "-x", [_exp(BED, extended=True)],
                    _hourly(S - 3 * H, 3, "BED1", 100_000) + _hourly(S, 2, "BED1", 200_000))
    assert ex.lines(rx, S + H, interim=True)[2] == one[: -len(more)] + ", " + LAST_SEQ
    # a window at the kernel's extension limit is judged again on the same hours: the last one
    rows8 = _hourly(S - 8 * H, 8, "BED1", 100_000) + _hourly(S, 2, "BED1", 200_000)
    r8 = _root_like(root, "-8", [_exp(BED, window_h=8)], rows8)
    assert ex.lines(r8, S + H, interim=True)[2].endswith(STARS + "), " + LAST_SEQ)
    # above the limit the kernel cuts an undecided window to its first 8 hours and judges that
    rows12 = _hourly(S - 12 * H, 12, "BED1", 100_000) + _hourly(S, 2, "BED1", 200_000)
    r12 = _root_like(root, "-12", [_exp(BED, window_h=12)], rows12)
    assert ex.lines(r12, S + H, interim=True)[2].endswith(STARS + "), 못 미치면 커널이 처음 8시간만으로 다시 판정함")


def test_interim_line_of_a_small_before_after_experiment_names_the_floor(root):
    # 30k/h before (buckets 28.5k, 30k, 31.5k): 10% of that is under the rule's floor, 10% of 100,000
    _put_ledger(root, [_exp(BED)])
    _write_events(root, _hourly(S - 3 * H, 3, "BED1", 10_000) + _hourly(S, 2, "BED1", 20_000))
    assert ex.lines(root, S + H, interim=True)[2].endswith(
        "시간별 흔들림이 이대로면 뚜렷한 판정에 필요한 차이는 ±10,000/h부터 "
        "(잡음(2se)과 최소 기준 중 큰 쪽, 후 구간이 온전한 2시간 미만이라 전 구간의 흔들림으로 셈" + STARS + "), "
        "못 미치면 커널이 구간을 6시간으로 늘려 한 번 더 봄")


def test_interim_line_of_a_buyer_experiment_matches_hand_arithmetic(root):
    # s0 0.3; before: one deal an hour with saved share 0.4, 0.5, 0.6 -> gains 0.1, 0.2, 0.3 (0.2/h, variance 0.01);
    # after: 0.5 then 0.7 -> gains 0.2, 0.4 (0.3/h over 2 h, variance 0.02); no other buyers, so r = 1
    e = _exp("buyer.defaults.food", key="max_pay_frac", old=0.65, new=0.7, window_h=3, s0=0.3)
    _put_ledger(root, [e])
    _write_events(root, _buy(S - 3 * H + 60, "SNK1", 6_000, 10_000) + _buy(S - 2 * H + 60, "SNK1", 5_000, 10_000)
                  + _buy(S - H + 60, "SNK1", 4_000, 10_000) + _buy(S + 60, "SNK1", 5_000, 10_000)
                  + _buy(S + H + 60, "SNK1", 3_000, 10_000))
    # 2 x sqrt(0.02 / 3 + 0.01 / 3) = 0.2, over the 10% rule (0.02)
    assert ex.lines(root, S + 2 * H, interim=True)[2] == (
        "    지금까지 2.0/3시간: 거래 전 3 → 후 2 (이 속도면 판정 때 후 약 3, 판정에 필요한 표본 5), 전 보정 0.200/h, "
        "후 0.300/h, 차이 +0.100/h. 시간별 흔들림이 이대로면 뚜렷한 판정에 필요한 차이는 ±0.200/h부터 "
        "(잡음(2se)과 전 보정 값의 10% 기준 중 큰 쪽), 못 미치면 커널이 구간을 6시간으로 늘려 한 번 더 봄")
    # once extended the sign decides; the buyer side has no stars guard and no unknown-cost exception
    rx = _root_like(root, "-bx", [dict(e, extended=True)], [json.loads(x) for x in open(
        os.path.join(root, ".r3/events.jsonl"), encoding="utf-8") if x.strip()])
    assert ex.lines(rx, S + 2 * H, interim=True)[2].endswith(
        "(잡음(2se)과 전 보정 값의 10% 기준 중 큰 쪽), 마지막 구간이라 못 미치면 차이의 부호로 정함 "
        "(0 이상이면 값 유지, 음이면 되돌림; 전후 모두 거래가 없으면 값 유지)")


def test_interim_noise_of_a_before_after_experiment_does_not_move_with_the_wake_time(root):
    """Verifier finding F1: the after variance came from buckets cut at now, so a partial last hour read as a swing.
    Before: 1, 3, 5 sales an hour (net 100k each): 300k/h, sample variance 4e10. After: one sale every 20 minutes."""
    _put_ledger(root, [_exp(BED)])
    rows = []
    for h, n in enumerate((1, 3, 5)):
        for i in range(n):
            rows += _sale(S - 3 * H + h * H + 60 + i * 600, "BED1", 1_100_000, 1_000_000)
    for i in range(9):
        rows += _sale(S + i * 1200, "BED1", 1_100_000, 1_000_000)
    _write_events(root, rows)

    def need(el):
        line = ex.lines(root, S + el * H, interim=True)[2]
        return line.split("필요한 차이는 ±")[1].split("부터")[0], "전 구간의 흔들림으로 셈" in line

    # r = (sales so far / hours run) / (9 / 3 h); whole hours of the after window are flat (variance 0), so from 2 h on
    # the noise is 2 x sqrt(r^2 x 4e10 / 3) = 230,940 r; before that the before variance stands in: 326,599 r
    assert need(1.0) == ("0.33M/h", True)  # 3 sales, r 1
    assert need(1.5) == ("0.36M/h", True)  # 5 sales in 1.5 h, r 1.111
    assert need(2.0) == ("0.23M/h", False)  # 6 sales, r 1
    assert need(2.4) == ("0.26M/h", False)  # 8 sales in 2.4 h, r 1.111 (the cut buckets 300k, 500k gave 0.16M/h + its own swing)
    assert need(2.6) == ("0.24M/h", False)  # 8 sales in 2.6 h, r 1.026
    assert need(3.0) == ("0.23M/h", False)


def test_interim_line_says_when_there_is_no_baseline(root):
    rows = _hourly(S - 3 * H, 3, "BED1", 100_000) + _hourly(S, 2, "BED1", 200_000)
    _put_ledger(root, [_exp(BED, before_from=S - 0.5 * H)])
    _write_events(root, rows)
    line = ex.lines(root, S + H, interim=True)[2]
    assert line == ("    지금까지 1.0/3시간: 판매 후 3 (이 속도면 판정 때 후 약 9), 후 0.57M/h. "
                    "전 구간이 30분이라 비교 기준이 없음 (기준 60분), 비교 없이 값이 유지됨")
    assert ex.evaluate(root, ex.load(root)[0], S + 3 * H)["decision"] == "no_baseline"
    # 59.9 minutes is still short of the line; it does not read as "1.0시간"
    r2 = _root_like(root, "-59", [_exp(BED, before_from=S - 0.999 * H)], rows)
    assert "전 구간이 59분이라 비교 기준이 없음 (기준 60분)" in ex.lines(r2, S + H, interim=True)[2]
    assert ex.evaluate(r2, ex.load(r2)[0], S + 3 * H)["decision"] == "no_baseline"
    # the other way evaluate() has no baseline: the events on file start after the before window does
    late = [dict(x, ts=S - 0.4 * H) if x["type"] == "unit_gone" else x  # the cost rows sit 30 h back in the helper
            for x in rows if x["type"] == "unit_gone" or x["ts"] >= S - 0.5 * H]
    r3 = _root_like(root, "-late", [_exp(BED)], late)
    assert ex.evaluate(r3, ex.load(r3)[0], S + 3 * H)["decision"] == "no_baseline"
    assert ex.lines(r3, S + H, interim=True)[2] == (
        "    지금까지 1.0/3시간: 판매 후 3 (이 속도면 판정 때 후 약 9), 후 0.57M/h. "
        "거래 기록이 전 구간의 시작까지 닿지 않아 비교 기준이 없음, 비교 없이 값이 유지됨")
    # a before window of one hourly bucket has a baseline but no noise figure
    r4 = _root_like(root, "-1b", [_exp(BED, before_from=S - 1.2 * H)], rows)
    assert ex.evaluate(r4, ex.load(r4)[0], S + 3 * H)["se"] is None
    assert ex.lines(r4, S + H, interim=True)[2].endswith(
        ". 전 구간이 1시간 구간 하나라 잡음을 셀 수 없어 이 구간에서는 차이로 뚜렷한 판정이 나오지 않음, "
        "커널이 구간을 6시간으로 늘려 한 번 더 봄")
    # a 12 h window without a baseline is cut to its first 8 hours, which may have one: the value is not said to stay
    r5 = _root_like(root, "-12late", [_exp(BED, window_h=12)],
                    [dict(x, ts=S - 0.4 * H) if x["type"] == "unit_gone" else x
                     for x in _hourly(S - 9 * H, 9, "BED1", 100_000) + _hourly(S, 2, "BED1", 200_000)])
    assert ex.evaluate(r5, ex.load(r5)[0], S + 12 * H)["decision"] == "no_baseline"
    assert ex.lines(r5, S + H, interim=True)[2].endswith(
        "거래 기록이 전 구간의 시작까지 닿지 않아 비교 기준이 없음, 커널이 처음 8시간만으로 다시 판정함")


def test_what_the_line_says_about_an_undecided_window_is_what_tick_does(root, a1):
    """The wording about an undecided window rests on _after_undecided. Here the real tick decides the same experiments
    with nothing sold (so never a clear verdict), and the ledger afterwards has to agree with it."""
    n = 0
    for wh in (1, 2, 3, 4, 5, 7, 8, 9, 12, 48):
        for ext in (False, True):
            for until in (None, -1, 1):  # before/after; a split whose arm ended a second ago / ends in a second
                e = _exp(BED, window_h=wh, extended=ext)
                now = e["eval_ts"] + 1
                if until is not None:
                    e.update(mode="split", arm={"share": ex.ARM_SHARE, "until": now + until})
                n += 1
                r = _root_like(root, f"-t{n}", [e], [])
                if until is None:  # a before/after experiment runs on its new value
                    _set_pol(r, lambda p: p["categories"][BED].update(price_mult=0.8))
                said = ex._after_undecided(e, now)
                assert [x["id"] for x in ex.tick(r, now, _traj(r), CATS, validate=a1.load)] == [1], (wh, ext, until)
                got = ex.load(r)[0]
                if got["status"] == "running" and got["window_h"] != wh:
                    did = ("extend", got["window_h"])
                else:  # ended on this window, or marked extended with the same hours and ended by the next tick
                    did = ("last", wh)
                    if got["status"] == "running":
                        assert got["extended"] and got["eval_ts"] <= now
                        ex.tick(r, now + ex.RETRY_S + 1, _traj(r), CATS, validate=a1.load)
                        assert ex.load(r)[0]["status"] != "running"
                assert said == did, (wh, ext, until, said, did)
    assert n == 60


def test_interim_lines_only_for_the_role_and_not_too_early(root):
    shop = _exp(BED, eid=1, role="shop")
    buyer = _exp("buyer.defaults.food", key="max_pay_frac", old=0.65, new=0.7, eid=2, window_h=2, s0=0.3, role="buyer")
    _put_ledger(root, [shop, buyer])
    _write_events(root, _hourly(S - 3 * H, 3, "BED1", 100_000) + _hourly(S, 1, "BED1", 200_000)
                  + _buy(S - H, "SNK1", 6_000, 10_000) + _buy(S + 600, "SNK1", 5_000, 10_000))
    assert ex.role_of(shop) == "shop" and ex.role_of(buyer) == "buyer"
    s = ex.lines(root, S + H, role="shop", interim=True)
    assert [ln[:16] for ln in s if ln.startswith("    지금까지")] == ["    지금까지 1.0/3시간"]
    assert s.index(next(ln for ln in s if ln.startswith("    지금까지"))) == 2  # right under experiment #1
    b = ex.lines(root, S + H, role="buyer", interim=True)
    mine = [ln for ln in b if ln.startswith("    지금까지")]
    assert len(mine) == 1 and mine[0].startswith("    지금까지 1.0/2시간: 거래 전 1 → 후 1 (이 속도면 판정 때 후 약 2, ")
    both = ex.lines(root, S + H, interim=True)
    assert sum(ln.startswith("    지금까지") for ln in both) == 2
    early = ex.lines(root, S + 0.2 * H, interim=True)
    assert not any(ln.startswith("    지금까지") for ln in early)
    assert sum(ln.startswith("    지금까지") for ln in tools.experiments(root, hours=2, now=S + H).split("\n")) == 2


def test_interim_failures_never_stop_the_ledger_lines(root, monkeypatch):
    _put_ledger(root, [_exp(BED), {"id": 9, "status": "running"}])
    plain = ex.lines(root, S + H)
    monkeypatch.setattr(ex, "read_events", lambda *a, **k: 1 / 0)
    assert ex.lines(root, S + H, interim=True) == plain
    assert "진행 중 2건" in tools.experiments(root, hours=2, now=S + H)
    monkeypatch.undo()
    monkeypatch.setattr(ex, "shop_metric", lambda *a, **k: 1 / 0)
    assert ex.lines(root, S + H, interim=True) == plain
    assert ex.interim_line({"id": 9, "status": "running"}, S, {}) == ""
    assert ex.interim_line(dict(_exp(BED), status="kept"), S + H, {"sales": []}) == ""


# ------------------------------------------------------------ the auditor's reach
def test_auditor_takes_kernel_card_parts_off_and_puts_them_back(tmp_path):
    r = _board_root(tmp_path)
    assert au.card_off(r) == {"shop": [], "buyer": []}
    other = {"hidden": ["events"], "desc": {"policy": "설명"}, "recommend": {"shop": ["reviews"], "buyer": []},
             "unrecommend": {"shop": [], "buyer": ["catalog"]}, "zz": 1}
    json.dump(other, open(os.path.join(r, au.TOOL_STATE), "w"))
    ask = {"uncard": {"shop": ["가게 회전", "실험 중간값"], "buyer": ["가게 회전", "실험 중간값"]}}
    got = au._apply_tools(r, NOW, ask, list(tools.TOOLS), (), None, roles=au.ROLES)
    assert got["applied"] == [{"op": "uncard", "role": "shop", "name": "가게 회전"},
                              {"op": "uncard", "role": "shop", "name": "실험 중간값"},
                              {"op": "uncard", "role": "buyer", "name": "실험 중간값"}]
    assert [x["why"] for x in got["refused"]] == ["붙어 있지 않음"]  # 가게 회전 is not a part of the buyer card
    assert au.card_off(r) == {"shop": ["가게 회전", "실험 중간값"], "buyer": ["실험 중간값"]}
    assert au.card_state(r) == {"shop": [], "buyer": []}  # the pinned tools are a separate list
    saved = json.load(open(os.path.join(r, au.TOOL_STATE)))
    assert {k: saved[k] for k in other} == other  # the save keeps the other keys of the state file
    again = au._apply_tools(r, NOW, {"uncard": {"shop": ["가게 회전"]}, "card": {"shop": ["묶인 돈 수익"]}},
                            list(tools.TOOLS), (), None, roles=au.ROLES)
    assert again["applied"] == [] and sorted(x["why"] for x in again["refused"]) == ["이미 떼어져 있음", "이미 붙어 있음"]
    gaps = au._gaps_block(r, NOW)
    assert ("커널이 넣는 칸 중 네가 떼고(uncard) 다시 붙일(card) 수 있는 것: shop 가게 회전, 묶인 돈 수익, 실험 중간값, 커널 밖 변경; "
            "buyer 실험 중간값, 커널 밖 변경. 지금 뗀 것: shop 가게 회전, 실험 중간값; buyer 실험 중간값") in gaps
    shop_parts = next(ln for ln in gaps.split("\n") if ln.startswith(au.ROLE_KO["shop"] + " 상태 카드의 칸"))
    assert "가게 회전" not in shop_parts
    back = au._apply_tools(r, NOW, {"card": {"shop": ["가게 회전"]}}, list(tools.TOOLS), (), None, roles=au.ROLES)
    assert back["applied"] == [{"op": "card", "role": "shop", "name": "가게 회전"}]
    assert au.card_off(r)["shop"] == ["실험 중간값"]
    gaps = au._gaps_block(r, NOW)
    assert "가게 회전" in next(ln for ln in gaps.split("\n") if ln.startswith(au.ROLE_KO["shop"] + " 상태 카드의 칸"))


def test_card_off_survives_a_broken_state_file_and_unknown_names(tmp_path):
    r = _board_root(tmp_path)
    p = os.path.join(r, au.TOOL_STATE)
    json.dump({"card_off": {"shop": ["가게 회전", "없는 칸", 3], "buyer": "x"}}, open(p, "w"))
    assert au.card_off(r) == {"shop": ["가게 회전"], "buyer": []}
    open(p, "w").write("{broken")
    assert au.card_off(r) == {"shop": [], "buyer": []}


def test_auditor_prompt_holds_the_board_comparison_with_hashed_names(tmp_path):
    r = _board_root(tmp_path)
    sizes = {}
    p = au.build_prompt(r, NOW, roles=au.ROLES, sizes=sizes)
    assert "## 다른 가게와 우리 (순위표의 가게 열, 커널이 셈. 해석 없음)\n\n[가게 회전 25시간]" in p
    assert f"1위 {c.hid('Alpha Shop')}: book +50.0M" in p and "Alpha" not in p and LOGIN not in p
    assert sizes.get("다른 가게와 우리", 0) > 300 and len(p) <= au.PROMPT_CHARS == 27_000
    assert "8. 다른 가게와 우리:" in p and "uncard에 그 이름을 적어 떼고" in p
    assert "뗐던 카드 칸 이름" in au.SCHEMA_ROLES


def test_shop_operator_card_follows_the_auditor(tmp_path, monkeypatch):
    from tests.test_operator_roles import Script, _root as op_root
    from kernel import operator as op
    from kernel.trajectory import Trajectory

    monkeypatch.setenv("A2_OPERATOR_COMPACT", "1")
    root_ = op_root(tmp_path)
    os.makedirs(os.path.join(root_, "a2/board"))
    now, old = _boards()
    login = c.owner_login(root_)
    for s in now["shops"] + old["shops"]:
        if s["owner"] == LOGIN:
            s["owner"] = login
    for h, b in ((25, old), (0, now)):
        json.dump(b, open(os.path.join(root_, f"a2/board/{int(NOW - h * 3600)}.json"), "w"))

    def card(role):
        o = op.Operator(root_, None, Trajectory(os.path.join(root_, "a2/trajectory.jsonl"), role=role),
                        harness_root=str(tmp_path / "noharness"), role=role)
        o.terra = Script({"done": {"note": "없음"}})
        o.wake("주기 점검", NOW)
        return o.terra.prompts[0]

    # a running shop experiment, one hour in, so the card has an interim line to show or leave out
    for d in (".r3", ".market"):
        os.makedirs(os.path.join(root_, d), exist_ok=True)
    json.dump(CATALOG, open(os.path.join(root_, ".market/catalog.json"), "w"), ensure_ascii=False)
    _put_ledger(root_, [_exp(BED, started=NOW - H)])
    open(os.path.join(root_, ".r3/events.jsonl"), "a").close()
    _write_events(root_, _hourly(NOW - 4 * H, 3, "BED1", 300_000) + _hourly(NOW - H, 1, "BED1", 570_000))

    shop = card("shop")
    assert login and "[가게 회전 25시간]" in shop and "    지금까지 1.0/3시간: 판매 전 9 → 후 3" in shop
    buyer = card("buyer")
    assert "[가게 회전" not in buyer and "지금까지 1.0/3시간" not in buyer  # a shop experiment is not on the buyer's card
    json.dump({"card_off": {"shop": ["가게 회전"]}}, open(os.path.join(root_, au.TOOL_STATE), "w"))
    shop = card("shop")
    assert "[가게 회전" not in shop and "    지금까지 1.0/3시간" in shop
    json.dump({"card_off": {"shop": ["실험 중간값"]}}, open(os.path.join(root_, au.TOOL_STATE), "w"))
    shop = card("shop")
    assert "[가게 회전 25시간]" in shop and "지금까지 1.0/3시간" not in shop
