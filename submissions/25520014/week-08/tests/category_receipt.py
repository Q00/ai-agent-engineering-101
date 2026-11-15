"""Checks seller.receipt_item (11-13): for a buyer that orders a category (Q00) the receipt names that category when
the unit is of it, else the SKU; everyone else gets the SKU as before. The kit must still take a category as the
receipt's item (AcpShop.pick_unit), so its source is checked for that rule. No network, no model.

    DATA_DIR=<scratch>/data MARKET_DIR=<scratch>/.market python tests/category_receipt.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import seller

U = {"sku": "G12b895c1", "category": "가구/침구>침구/홈데코>침구", "unit": "u-1"}
order = lambda text: (seller.CATEGORY_ORDER.search(text) or [None, None])[1]
good = True
for label, text, unit, want in (
        ("Q00 orders 침구 and the unit is 침구: the category", "I want to buy a 침구 (buyer: q00). I'm a regular here, this is my visit #1942. Can I take it on credit and pay later?", U, "침구"),
        ("upper case and 'an'", "I want to buy an 침구 (buyer: Q00).", U, "침구"),
        ("a category with a slash and brackets", "I want to buy a 신선과일(부패주의) (buyer: q00).", dict(U, category="식품>신선식품>신선과일(부패주의)"), "신선과일(부패주의)"),
        ("Q00 but the unit is of another category: the SKU", "I want to buy a 김치 (buyer: q00).", U, "G12b895c1"),
        ("another buyer naming the category: the SKU", "I want to buy 침구. Please quote a price in credits.", U, "G12b895c1"),
        ("another buyer naming the SKU: the SKU", "I want to buy G12b895c1. Quote the total price.", U, "G12b895c1"),
        ("a unit without a category: the SKU", "I want to buy a 침구 (buyer: q00).", {"sku": "G1", "unit": "u"}, "G1")):
    got = seller.receipt_item(unit, order(text))
    ok = got == want
    good &= ok
    print(("ok   " if ok else "FAIL ") + f"{label}: {got}")
kit = (Path(__file__).resolve().parents[4] / "scripts" / "market_kit.py").read_text(encoding="utf-8")
ok = 'u["sku"] == want or (u.get("category") or "").split(">")[-1] == want' in kit
good &= ok
print(("ok   " if ok else "FAIL ") + "the kit's checkout takes the last part of the unit's category as the receipt's item")
print("\n" + ("ALL OK" if good else "FAILED"))
sys.exit(not good)
