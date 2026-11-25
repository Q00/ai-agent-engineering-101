"""Post-settlement evidence and truthful quality review, never a pre-payment reward."""
import argparse
import json
import time
from r3.state import Journal, read_json, warranty_hours
from r3.economy import buyer_value


def review(deal, journal):
    order, rec, settled = deal["order"], deal["receipt"], deal["settled"]
    sku = settled["sku"]
    product = journal.snapshot("catalog.json", {}).get(sku, {})
    price = int(rec["price"])
    now = time.time()
    expires = settled.get("expires_at")
    value = settled.get("value")
    if value is None and product:
        value = buyer_value(product, price, hours_left=(expires - now) / 3600 if expires else float("inf"),
                            use_hours=order.get("use_within_hours", 0))["value"]
    value = value or 0
    from r3.buyer_scoring import formula,LEGACY,cost_scenario,contribution,MARGIN
    version=formula(journal)
    scenario=cost_scenario(product,settled.get('grade')) if product.get('list_price') and version==MARGIN else None
    # Settlement is signed, but does not expose acquisition cost. Do not invent
    # an exact margin-share outcome from a seller's claimed cost or our bound.
    initial=((value-price)/product['list_price'] if value and product.get('list_price') else -1) if version==LEGACY else None
    item = {"order": order["order"], "sku": sku, "unit": settled.get("unit"), "shop": settled["shop"],
            "shop_identity": journal.snapshot("registry.json", {}).get(settled["shop"], {}).get("pubkey"),
            "name": settled.get("name", product.get("name", sku)), "kind": product.get("kind", order.get("kind")),
            "price": price, "value": value, "paid_at": now, "grade": settled.get("grade"),
            'list_price':product.get('list_price'),'expires_at':expires,
            'initial_saved_contribution':initial,'score_formula':version,
            'initial_saved_lower_scenario':contribution(price,product['list_price'],score_formula=MARGIN,
                 unit_cost=scenario['unit_cost']) if scenario and value else None,
            'cost_scenario':scenario,
            "warranty_hours": warranty_hours(settled.get('warranty_hours')), "ledger": settled.get("ledger"),
            "strategy_version": journal.get('buyer_strategy:' + str(order['order']), {}).get('version', 0)}
    journal.add("purchase", item, eid="purchase:" + order["order"], role="buyer")
    honest = settled.get("claim_honest") is not False
    stars = 1 if not honest else 2 if value == 0 else 5 if price <= value * .85 else 4
    claim_status = ('no grade claim recorded' if not settled.get('claim') else
                    'claim matched' if settled.get('claim_honest') is True else
                    'claim did not match' if settled.get('claim_honest') is False else 'claim not verified')
    text = f"Settled {sku} at {price}; grade {settled.get('grade')}; {claim_status}."
    # The shared forum scheduler publishes ledger-backed observations with per-role cooldowns.
    return {"stars": stars, "review": text}


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--deal", required=True)
    args = p.parse_args()
    print(json.dumps(review(read_json(args.deal), Journal()), ensure_ascii=False))
