"""Pure/offline tests: no Groq key, network, or market spending required."""
import io
import json
from pathlib import Path
import sys
import tempfile
from contextlib import redirect_stdout
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import market_buyer_r3_v2 as mod


def sample_catalog():
    return {
        "G00000001": {"sku": "G00000001", "list_price": 50000, "category": "goods"},
        "G00000002": {"sku": "G00000002", "list_price": 80000, "category": "goods"},
        "G00000003": {"sku": "G00000003", "list_price": 180000, "category": "goods"},
    }


def sample_shops():
    return [
        {"name": "A", "url": "https://example.invalid/a",
         "offers": [{"sku": "G00000001", "qty": 1}], "reviews": []},
        {"name": "B", "url": "https://example.invalid/b",
         "offers": [{"sku": "G00000002", "qty": 1}], "reviews": []},
    ]


def test_surplus():
    shops = sample_shops()
    shop, sku, why = mod.choose_surplus_aware(
        shops, {"acceptable": ["G00000001", "G00000002"]}, sample_catalog(), [], 100000)
    assert (shop["name"], sku) == ("B", "G00000002"), (shop, sku, why)
    print("PASS: chooses higher expected surplus among affordable products")


def test_settlement():
    catalog = sample_catalog()
    shops = [
        {"name": "Unreliable", "url": "https://example.invalid/a", "offers": [{"sku": "G00000002", "qty": 1}], "reviews": []},
        {"name": "Reliable", "url": "https://example.invalid/b", "offers": [{"sku": "G00000002", "qty": 1}], "reviews": []},
    ]
    payments = ([{"shop": "https://example.invalid/a", "item": "G00000002", "price": 75000, "paid": False} for _ in range(15)]
                + [{"shop": "https://example.invalid/b", "item": "G00000002", "price": 75000, "paid": True} for _ in range(5)])
    shop, sku, why = mod.choose_surplus_aware(
        shops, {"acceptable": ["G00000002"]}, catalog, payments, 100000)
    assert shop["name"] == "Reliable", (shop, sku, why)
    assert "5 paid/0 unpaid" in why
    print("PASS: settled payments preferred to unsuccessful checkout attempts")


def test_constraints():
    catalog = sample_catalog()
    offers = [{"sku": "G00000003", "qty": 1}]
    shop, sku, why = mod.choose_surplus_aware(
        [{"name": "TooExpensive", "url": "https://example.invalid/x", "offers": offers}],
        {"acceptable": ["G00000003"]}, catalog, [], 100000)
    assert shop is None and sku is None
    policy = mod.acceptance_policy({"acceptable": ["G00000001"], "kind": "food", "use_within_hours": 4})
    assert policy == {"cert": True, "grades": ["A", "B"], "min_hours_left": 4}
    print("PASS: budget plausibility guard and food quality acceptance policy")


def test_main_contract():
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        market = root / ".market"
        market.mkdir()
        (market / "catalog.json").write_text(json.dumps(sample_catalog()), encoding="utf-8")
        (market / "payments.jsonl").write_text("", encoding="utf-8")
        shops_file = root / "shops.json"
        order_file = root / "order.json"
        shops_file.write_text(json.dumps(sample_shops()), encoding="utf-8")
        order_file.write_text(json.dumps({"acceptable": ["G00000001", "G00000002"],
                                          "kind": "goods", "item": "goods", "budget": 100000}), encoding="utf-8")
        receipt = {"shop": "B", "item": "G00000002", "price": 74000, "task": "mock", "sig_seller": "mock"}
        out = io.StringIO()
        with patch.object(mod, "MARKET", market), \
             patch.object(mod, "buy", return_value={"deal": True, "receipt": receipt}), \
             patch.object(mod, "store_memory"), \
             patch.object(sys, "argv", ["market_buyer_r3_v2.py", "--shops", str(shops_file),
                                       "--order", str(order_file), "--item", "goods", "--budget", "100000"]), \
             redirect_stdout(out):
            mod.main()
        result = json.loads(out.getvalue().strip().splitlines()[-1])
        assert result["shop"] == "https://example.invalid/b", result
        assert result["receipt"] is receipt or result["receipt"] == receipt
        assert result["accept"]["cert"] is True
        assert result["accept"]["grades"] == ["A", "B"]
        print("PASS: Market Kit v4 JSON output contract with mocked A2A receipt")


if __name__ == "__main__":
    test_surplus()
    test_settlement()
    test_constraints()
    test_main_contract()
    print("All buyer v2 offline tests passed; NO live settlement tested")
