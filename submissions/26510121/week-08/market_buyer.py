"""R3 buyer: choose useful surplus; verify exact SKU, receipt, and acceptance policy."""
import argparse
import json
import os
import time
from r3.state import Journal, read_json, warranty_hours
from r3.economy import buyer_candidates
from r3.model import Chooser
from r3.protocol import Peer, quote, verify_receipt
from r3.adaptive import begin_buyer


def buy(order, shops, journal, chooser=None, peer_factory=Peer):
    cfg, strategy = begin_buyer(journal, order)
    journal.reconcile()
    empty = {"shop": "", "receipt": None, "stars": 3, "review": "No settled purchase; no seller rating inferred."}
    if time.time() + cfg["checkout_buffer_seconds"] >= order["expires"]:
        return empty
    catalog = journal.snapshot("catalog.json", {})
    registry = journal.snapshot("registry.json", {})
    ranked = buyer_candidates(order, shops, catalog, journal)
    if not ranked:
        from r3.forum import queue_wanted
        queue_wanted(journal, order)
    chooser = chooser or Chooser(journal, cfg)
    deadline = min(order["expires"] - cfg["checkout_buffer_seconds"], time.time() + 240)
    for _ in range(cfg["max_shop_candidates"]):
        if not ranked or time.time() >= deadline:
            break
        c = chooser.choose("buyer", ranked[:8], {"order": order, "strategy": strategy,
                           'forum_evidence': 'unverified discovery hint; registry and checkout remain authoritative'}, deadline=deadline,
                           critical=ranked[0]["reference_price"] > .8 * order["budget"])
        ranked = [x for x in ranked if x["shop"] != c["shop"]]
        peer = peer_factory()
        started = time.monotonic()
        try:
            pub = registry.get(c["shop"], {}).get("pubkey")
            if not pub:
                raise ValueError("registered_signing_key_unavailable")
            peer.discover(c["shop"])
            request = f"I want to buy {c['sku']}. Please quote this exact SKU, certificate and warranty hours."
            if order.get("kind") == "food":
                request += f" Food must last at least {order.get('use_within_hours', 0)} hours after payment."
            task = peer.send(request)
            agreed = None
            required_warranty = 0
            for turn in range(cfg["max_negotiation_turns"] + 1):
                state = task["status"]["state"]
                if state == "TASK_STATE_COMPLETED":
                    receipts = [p["data"] for a in task.get("artifacts", []) for p in a.get("parts", [])
                                if isinstance(p.get("data"), dict) and "sig_seller" in p["data"]]
                    if len(receipts) != 1 or agreed is None or not verify_receipt(receipts[0], pub, task=task["id"],
                            sku=c["sku"], budget=order["budget"], agreed=agreed):
                        raise ValueError("receipt_integrity_failure")
                    # A2A protobuf numbers decode as whole-valued floats; the signature canonicalizes integer money.
                    rec = dict(receipts[0], price=int(receipts[0]["price"]))
                    if rec.get('cert') is False:
                        # The kit will reject this checkout. Try another shop while the order is still alive.
                        raise ValueError('certificate_unavailable')
                    accept = {"cert": True, "expected_sku": c["sku"]}
                    if order.get("kind") == "food":
                        accept["min_hours_left"] = order.get("use_within_hours", 0) + cfg["checkout_buffer_seconds"] / 3600
                    else:
                        accept["warranty_hours"] = required_warranty
                    journal.add("agreement", {"order": order["order"], "sku": c["sku"], "shop": c["shop"],
                                              "task": task["id"], "price": rec["price"], "accept": accept,
                                              "strategy_version": strategy['version']},
                                eid="agreement:" + order["order"], role="buyer")
                    return {"shop": c["shop"], "receipt": rec, "stars": 3,
                            "review": "Pending checkout; settled quality will determine review.", "accept": accept}
                if state != "TASK_STATE_INPUT_REQUIRED" or time.time() >= deadline:
                    break
                ask = quote(task)
                product = c["product"]
                cap = min(order["budget"], product["list_price"])
                terms = task.get("status", {}).get("message", {}).get("metadata", {})
                if product.get("kind") == "goods":
                    required_warranty = 6 if warranty_hours(terms.get('warranty_hours')) >= 6 else 0
                    if not required_warranty:
                        from r3.meta import observed_breaks
                        risk = observed_breaks(journal, c["sku"])
                        stress = cfg["unknown_goods_failure_stress"]
                        if risk["mature_samples"] >= 10:
                            # Conservative posterior mean plus uncertainty buffer, not a guarantee.
                            n = risk["mature_samples"]
                            stress = min(1, (risk["broken"] + 1) / (n + 2) + 1 / (n + 2) ** .5)
                        cap = min(cap, int(product["list_price"] * (1 - stress)))
                actions = []
                if ask <= cap:
                    actions.append({"id": "accept", "price": ask, "score": product["list_price"] - ask + 1})
                ratio = cfg['buyer_counter_ratio']
                counter = max(1, min(cap, int(min(ask * ratio, c["reference_price"] * ratio / .92))))
                if counter < ask and turn < cfg["max_negotiation_turns"] - 1:
                    actions.append({"id": "counter", "price": counter,
                                    "score": .75 * (product["list_price"] - counter),
                                    "success_prior": "0.75 is an engineering negotiation prior, not empirical evidence"})
                if not actions:
                    break
                move = chooser.choose("buyer", actions, {"ask": ask, "order": order, "turn": turn,
                                       "product": product, "strategy": strategy}, deadline=deadline)
                agreed = move["price"]
                if move['id'] == 'counter':
                    journal.add('buyer_counter', {'order': order['order'], 'shop': c['shop'], 'ask': ask,
                                'price': agreed, 'strategy_version': strategy['version']}, role='buyer')
                task = peer.send(str(agreed), task["id"])
            journal.add("attempt", {"order": order['order'], "strategy_version": strategy['version'],
                                    "shop": c["shop"], "sku": c["sku"], "success": False,
                                    "kind": "no_agreement", "seconds": time.monotonic() - started}, role="buyer")
        except Exception as exc:
            reason = str(exc) if isinstance(exc, ValueError) and str(exc) in {
                'certificate_unavailable', 'receipt_integrity_failure', 'registered_signing_key_unavailable',
                'ambiguous_ask', 'conflicting_ask', 'invalid_money'} else None
            journal.add("attempt", {"order": order['order'], "strategy_version": strategy['version'],
                                    "shop": c["shop"], "sku": c["sku"], "success": False,
                                    'reason': reason,
                                    "kind": type(exc).__name__, "seconds": time.monotonic() - started}, role="buyer")
        finally:
            peer.close()
    from r3.forum import queue_wanted
    queue_wanted(journal, order)
    return empty


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--shops", required=True)
    p.add_argument("--order", required=True)
    p.add_argument("--item")
    p.add_argument("--budget", type=int)
    args = p.parse_args()
    if os.environ.get("R3_STRICT_ADAPTER") != "1":
        raise SystemExit("Use run_market.py: the exact-SKU acceptance adapter is required.")
    order = read_json(args.order, {})
    if not order.get("acceptable"):
        print(json.dumps({"shop": "", "receipt": None, "stars": 3, "review": "R3 actor waits for SKU orders."}))
    else:
        print(json.dumps(buy(order, read_json(args.shops, []), Journal()), ensure_ascii=False))
