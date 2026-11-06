"""market_buyer hook (Round 3): pick shops, negotiate by rule (D9 model reading only for a reply the rules
cannot read), hand the kit a receipt and accept terms.

    python market_buyer.py --shops .market/shops.json --item 김치 --budget 50000 --order .market/order.json

Prints exactly one line on stdout, always: {"shop", "receipt", "stars", "review", "accept"}, or the
no-deal line {"shop": "", "receipt": null}. Diagnostics go to logs/hooks/market_buyer.log and the
decision to logs/decisions/buyer.jsonl. Each shop's first ask is folded into memory/shop_asks.json, which ranks
the shops of later orders (buyer_policy.candidates).
"""
import argparse
import fcntl
import json
import time
from pathlib import Path

NO_DEAL = {"shop": "", "receipt": None}
ASKS = "shop_asks.json"             # under memory/: {"<shop url>|<kind>": {n, share, w, ts}}, see buyer_policy.fold_ask
ASK_LOCK_TRIES, ASK_LOCK_WAIT_S = 10, 0.05   # a busy record lock is skipped after this: the order must not wait


def remember_names(shops):
    """memory/shop_names.json: shop url -> name from every order's shops.json (forum reports name shops)."""
    from memory.store import MEM, atomic_write_json, read_json
    try:
        names = read_json(MEM / "shop_names.json", {}) or {}
        new = {str(s["url"]).rstrip("/"): str(s["name"])[:60] for s in shops or []
               if isinstance(s, dict) and s.get("url") and s.get("name")}
        if any(names.get(k) != v for k, v in new.items()):
            names.update(new)
            atomic_write_json(MEM / "shop_names.json", dict(list(names.items())[-500:]))
    except Exception:
        pass


def run(args, t0):
    """The order's decision. Returns the result line."""
    import policy
    from memory import query
    from memory.decisions import decision
    from memory.store import hook_log, read_json
    from tools import buyer_policy as bp
    from tools.negotiation import Deadline, negotiate

    pol, ver, errors = policy.load()
    if errors:
        decision("buyer", "policy_error", policy_ver=ver, choice="use " + ver, why="; ".join(errors)[:500])
    order = read_json(args.order, {}) or {}
    shops = read_json(args.shops, []) or []
    remember_names(shops)
    catalog = read_json(Path(args.order).parent / "catalog.json", {}) or {}
    summary = query.load_summary()
    ref = {"order": order.get("order")}
    leaf = order.get("item") or args.item
    ref_list = bp.reference_list(order, catalog)
    inputs = {"order": order.get("order"), "item": leaf, "category": order.get("category"),
              "kind": order.get("kind"), "budget": order.get("budget", args.budget),
              "use_within_hours": order.get("use_within_hours"), "round": order.get("round"),
              "n_acceptable": len(order.get("acceptable") or []), "n_shops": len(shops),
              "n_offers": sum(len(s.get("offers") or []) for s in shops),
              "acceptable_median_list": ref_list,
              "budget_ratio": round(order.get("budget", args.budget) / ref_list, 4) if ref_list else None}
    order.setdefault("budget", args.budget)
    order.setdefault("item", args.item)

    def done(out, choice, why, rules, refs=(), **extra):
        decision("buyer", "order", policy_ver=ver, ref=ref, inputs=inputs, memory_refs=list(refs),
                 rules=rules, choice=choice, why=why, elapsed_ms=int((time.monotonic() - t0) * 1000), **extra)
        hook_log("market_buyer", f"order {order.get('order')}: {why}")
        return out

    if (order.get("round") or 0) < 3 or not order.get("acceptable"):
        return done(NO_DEAL, "no_deal", "not a Round 3 order with acceptable SKUs", ["contract"])
    cands = bp.candidates(order, shops, catalog, summary, pol, load_asks())
    refs = [query.summary_ref("cat", leaf)] + [query.summary_ref("shop", c["shop"]) for c in cands]
    if not cands:
        return done(NO_DEAL, "no_deal", "no shop offers an acceptable SKU worth its cap", ["D6"], refs,
                    candidates=[])
    deadline = Deadline(pol, start=t0)
    tried = []
    for cand in cands:
        if deadline.left() < 2:
            break
        if deadline.past_target() and cand["p_checkout"] * cand["p_reach"] < pol["buyer"]["continue_p"]:
            break                                   # D8: past the target only a likely deal is worth the time
        res = negotiate(cand["shop"], cand, order, deadline, pol, ver=ver)
        record_ask(cand, res, time.time())
        problem = None
        if res["receipt"]:
            problem = bp.verify_receipt(order, res["receipt"], cand, catalog, pol, res["unit"], res["warranty_h"])
        tried.append({k: res[k] for k in ("shop", "sku", "state", "turns", "asks", "offers", "cap", "unit",
                                          "warranty_h", "canceled", "llm_reads", "elapsed", "why")}
                     | {"p_trace": res["p_trace"], "receipt_problem": problem})
        if res["receipt"] and not problem:
            receipt = res["receipt"]
            unit = receipt.get("unit")
            accept = bp.build_accept(order, cand, receipt, unit, res["warranty_h"], pol)
            choice = {"shop": cand["shop"], "sku": receipt.get("item"), "name": cand["name"], "unit": unit,
                      "price": receipt["price"], "V": bp.list_price(catalog, receipt.get("item")) or cand["V"],
                      "cap": res["cap"], "warranty_h": res["warranty_h"]}
            stars, review = bp.rate_deal(receipt, {"choice": choice}, provisional=True)
            out = {"shop": cand["shop"], "receipt": receipt, "stars": stars, "review": review, "accept": accept}
            return done(out, choice, f"deal with {cand['shop']} at {receipt['price']} ({cand['why']})",
                        ["D6", "D7", "D8", "D12"], refs + cand["memory_refs"], candidates=_brief(cands),
                        tried=tried, p_trace={t["shop"]: t["p_trace"] for t in tried}, accept=accept)
    return done(NO_DEAL, "no_deal", f"no deal after {len(tried)} shop(s)", ["D6", "D8"], refs,
                candidates=_brief(cands), tried=tried, p_trace={t["shop"]: t["p_trace"] for t in tried})


def load_asks():
    """The entries of memory/shop_asks.json updated within buyer_policy.ASK_KEEP_H; {} if missing or broken."""
    from memory.store import MEM, read_json
    from tools import buyer_policy as bp
    return bp.fresh_asks(read_json(MEM / ASKS, {}), time.time())


def record_ask(cand, res, now):
    """Fold the shop's first ask / V and whether it granted the full warranty into memory/shop_asks.json under an
    exclusive lock (several buyer hooks can run at once). A lock still busy after ASK_LOCK_TRIES or any other
    failure is logged, never raised: the record must not cost the order."""
    from memory.store import MEM, atomic_write_json, hook_log
    from tools import buyer_policy as bp
    try:
        ask = (res.get("asks") or [None])[0]
        if not isinstance(ask, (int, float)) or ask <= 0 or not cand["V"] > 0:
            return
        MEM.mkdir(parents=True, exist_ok=True)
        with open(MEM / ".shop_asks.lock", "a") as lock:
            for _ in range(ASK_LOCK_TRIES):
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)   # released when the file closes
                    break
                except BlockingIOError:
                    time.sleep(ASK_LOCK_WAIT_S)
            else:
                raise TimeoutError(f"{lock.name} busy")
            warranty = (res.get("warranty_h") or 0) >= bp.WARRANTY_FULL_H
            atomic_write_json(MEM / ASKS, bp.fold_ask(load_asks(), cand["shop"], cand["kind"], ask / cand["V"], now,
                                                      warranty))
    except Exception as e:
        hook_log("market_buyer", f"shop ask not recorded: {type(e).__name__}: {e}")


def _brief(cands):
    return [{k: c[k] for k in ("shop", "sku", "V", "cap", "ask_est", "kept", "score", "p_checkout", "p_reach")}
            for c in cands]


def main(argv=None):
    t0 = time.monotonic()
    out = NO_DEAL
    try:
        ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
        ap.add_argument("--shops", required=True, type=Path)
        ap.add_argument("--item", required=True)
        ap.add_argument("--budget", required=True, type=int)
        ap.add_argument("--order", required=True, type=Path)
        out = run(ap.parse_args(argv), t0)
    except Exception as e:
        try:
            from memory.store import hook_log
            import traceback
            hook_log("market_buyer", f"error {type(e).__name__}: {e}\n{traceback.format_exc()}")
        except Exception:
            pass
        out = NO_DEAL
    finally:
        print(json.dumps(out, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
