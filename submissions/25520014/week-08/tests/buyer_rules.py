"""Checks the two 11-14 buyer rules in seller.py: WARRANTY_B_BUYER (25620027's wording gets B with a warranty and the
refund in the floor) and NO_C_FALLBACK_TPL ("I want to buy <name> (<SKU>)" gets no C fallback, B still). No network,
no model; real first messages from data/turns.jsonl.

    DATA_DIR=<scratch>/data MARKET_DIR=<scratch>/.market python tests/buyer_rules.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import policy
import seller
import warranty_memo

good = True


def check(ok, label, *info):
    global good
    print("ok " if ok else "BAD", label, *info)
    good &= bool(ok)


SJ = ("I want to buy 베네베딩 5도업 발열 장모 극세사 소파 패드 4인용 (240x60cm) (G1a2b3c4d), please state this unit's "
      "certified grade and offered warranty duration in hours. If a warranty is offered, ...")
IY = "I want to buy [다우닝] 플라나 4인 스윙 전체 통가죽 소파+스툴 (G24f5f979)"
OTHER = "I want to buy G2c023a8c. Please quote this exact SKU, certificate, grade and warranty hours."
check(seller.WARRANTY_B_BUYER.search(SJ) and not seller.WARRANTY_B_BUYER.search(IY)
      and not seller.WARRANTY_B_BUYER.search(OTHER), "25620027's wording is recognised, the others are not")
check(warranty_memo.template(IY, "[다우닝] 플라나 4인 스윙 전체 통가죽 소파+스툴") == seller.NO_C_FALLBACK_TPL
      and warranty_memo.template(SJ, "베네베딩 5도업 발열 장모 극세사 소파 패드 4인용 (240x60cm)") != seller.NO_C_FALLBACK_TPL
      and warranty_memo.template(OTHER) != seller.NO_C_FALLBACK_TPL, "the no-C-fallback template: only the bare wording")

# B with the memo's 72 h: warranty on and the refund priced into the floor; without it no warranty
p = policy.load()
u = {"unit": "u-test", "sku": "G1a2b3c4d", "grade": "B", "kind": "goods", "cost": 500000, "list_price": 1000000,
     "category": "가구/침구>침구/홈데코>침구"}
with_w = seller.option(dict(u), p, 0.0, wants_warranty=True, memo=int(p["warranty_hours"]))
without = seller.option(dict(u), p, 0.0, wants_warranty=True, memo=None)
check(with_w["warranty"] == int(p["warranty_hours"]) and without["warranty"] == 0,
      "B gets the warranty only from the rule", with_w["warranty"], without["warranty"])
brk = seller.BREAK.get("B", 0)
check(with_w["floor"] * (1 - brk) >= u["cost"], "B with a warranty: the floor clears the cost after the expected refund",
      f"floor {with_w['floor']} break {brk:.3f} -> {with_w['floor'] * (1 - brk):.0f} vs cost {u['cost']}")
print(f"   floors / list: with {with_w['floor'] / 1e6:.3f}, without {without['floor'] / 1e6:.3f}")


# c_fallback: the lower grades it tries
def lower(deal, grade="A"):
    return [g for g in (("B", "C") if seller.B_FALLBACK else ("C",)) if seller.RANK[g] < seller.RANK[grade]
            and g not in deal["opts"] and not (g == "C" and deal.get("no_c_fb"))]


check(lower({"opts": {}, "no_c_fb": True}) == ["B"] and lower({"opts": {}, "no_c_fb": False}) == ["B", "C"],
      "no_c_fb drops C from the fallback, keeps B")
src = Path(seller.__file__).read_text()
check('not (g == "C" and deal.get("no_c_fb"))' in src, "c_fallback uses the same condition")

# ONE_SALE_BUYER (11-14 15 h, off 11-15): the wording only, not totato's; one deal, then the normal rules
import tempfile
JH = ("I want to buy [룸마이룸] 포근한 가을 겨울 코튼캔디 순면 차렵이불 SS (Gdac75d55). Quote the total price. Require a market "
      "certificate. Confirm an available unreserved unit before agreeing; select another eligible unit if the first has sold. "
      "Require grade A only, as quoted in the comparison. Include a six-hour warranty when offered. State true grade, ")
TT = ("I want to buy X (Gdac75d55). Quote the total price. Require a market certificate. Confirm an available unreserved "
      "unit before agreeing; select another eligible unit if the first has sold. Prefer grade A near acquisition cost, "
      "with a six-hour warranty")
# 11-15: JH was totato2203-r3's wording (it paid the one deal); ONE_SALE is off and junghoonwoo is JW_BUYER
check(seller.ONE_SALE_BUYER is None, "ONE_SALE off")
JW = ("I want to buy 삼성 김치플러스 뚜껑형 202L RP20C3111EG (G3cbc56d1). Market certificate required. Do not overstate grade. "
      "Need grade A or B with a 6-hour warranty included in total price.")
check(seller.JW_BUYER.search(JW) and not any(seller.JW_BUYER.search(t) for t in (JH, TT, IY)),
      "junghoonwoo's wording is recognised, totato's and the others are not")
srcj = Path(seller.__file__).read_text()
check('memo["A"] = memo["B"] = int(p["warranty_hours"])' in srcj, "JW_BUYER: A and B get the warranty")
o = {"unit": {"list_price": 61300, "cost": 40000}, "ask": 64704, "floor": 62000, "step": 0}
seller.one_sale_lift(o, 4)
check(o["ask"] == o["floor"] == round(0.51 * 61300) and o["step"] == 4, "one sale: asked and held at 0.51 x list, final",
      o)
check(seller.close_floor(dict(o, unit=dict(o["unit"]))) == o["floor"], "under the cost the floor closes as it is")
real = seller.ONE_SALE_FILE
seller.ONE_SALE_FILE = Path(tempfile.mkdtemp()) / "one_sale.json"
left0 = seller.one_sale_left()
seller.one_sale_signed("task-x", {"unit": "u-x", "sku": "Gdac75d55", "cost": 40000}, 31263)
check(left0 == 1 and seller.one_sale_left() == 0, "one deal allowed, none after it is signed", left0, seller.one_sale_left())
seller.ONE_SALE_FILE = real

# sergiobuilds blocked (11-14 15 h), the others not
SG1 = "I want to buy 명랑핫도그 우리밀 감자쌀 핫도그 480g x 2 (G1a2b3c4d). What is your price?"
SG2 = ("I want to buy 명랑핫도그 (G1a2b3c4d). I need it to last 48 hours: please offer only a unit with at least 48 hours "
       "of shelf life left, with the market's certificate.")
check(seller.BLOCKED.search(SG1.strip()) and seller.BLOCKED.search(SG2) and not any(
    seller.BLOCKED.search(t) for t in (IY, SJ, JH, TT, OTHER)), "sergiobuilds' two wordings are blocked, the others are not")

print("all ok" if good else "FAILED")
sys.exit(0 if good else 1)
